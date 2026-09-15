"""SQLite persistence for scan sessions and history."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from app import barcode_catalog
from app.components import capitalize_person_name
from app.paths import get_data_dir
from app.pdf_parser import PickingTicket, ticket_from_dict, ticket_to_dict
from app.sqlite_util import DB_LOCK, connect as sqlite_connect, retry_locked
from app.verification import compute_verification

# Schema/backfill only needs to run once per process after startup.
_schema_ready = False


def _db_path() -> Path:
    return get_data_dir() / "scanner.db"


def _connect() -> sqlite3.Connection:
    return sqlite_connect(_db_path(), timeout=60.0)


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """Open scanner.db under the shared lock (safe with Firebase heartbeats)."""
    with DB_LOCK:
        with _connect() as conn:
            _migrate(conn)
            yield conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply schema upgrades. Heavy backfills run only once per process."""
    global _schema_ready
    if _schema_ready:
        return

    session_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(scan_sessions)").fetchall()
    }
    # Tables may not exist yet (init_db creates them first). Skip ALTERs until then.
    if not session_cols:
        return

    if "ticket_json" not in session_cols:
        conn.execute("ALTER TABLE scan_sessions ADD COLUMN ticket_json TEXT")
    if "updated_at" not in session_cols:
        conn.execute("ALTER TABLE scan_sessions ADD COLUMN updated_at TEXT")
    if "check_time" not in session_cols:
        conn.execute("ALTER TABLE scan_sessions ADD COLUMN check_time TEXT")

    item_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(scan_items)").fetchall()
    }
    if "match_status" not in item_cols:
        conn.execute("ALTER TABLE scan_items ADD COLUMN match_status TEXT")
    if "box_qty" not in item_cols:
        conn.execute("ALTER TABLE scan_items ADD COLUMN box_qty INTEGER")
    if "set_qty" not in item_cols:
        conn.execute("ALTER TABLE scan_items ADD COLUMN set_qty INTEGER")
    if "pallet_qty" not in item_cols:
        conn.execute("ALTER TABLE scan_items ADD COLUMN pallet_qty INTEGER")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS picker_names (
            name TEXT PRIMARY KEY COLLATE NOCASE,
            created_at TEXT NOT NULL
        )
        """
    )
    now = datetime.now().isoformat(timespec="seconds")
    for row in conn.execute(
        "SELECT DISTINCT picker_name FROM scan_sessions WHERE TRIM(picker_name) != ''"
    ):
        name = capitalize_person_name(row[0])
        if name:
            conn.execute(
                "INSERT OR IGNORE INTO picker_names (name, created_at) VALUES (?, ?)",
                (name, now),
            )
    _schema_ready = True


def list_picker_names() -> list[str]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT name FROM picker_names ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [row[0] for row in rows]


def remember_picker_name(name: str) -> None:
    picker = capitalize_person_name(name).strip()
    if not picker:
        return
    with _db() as conn:
        _remember_picker_name(conn, picker)


def _remember_picker_name(conn: sqlite3.Connection, name: str) -> None:
    picker = capitalize_person_name(name).strip()
    if not picker:
        return
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR IGNORE INTO picker_names (name, created_at) VALUES (?, ?)",
        (picker, now),
    )


def delete_picker_name(name: str) -> None:
    picker = capitalize_person_name(name).strip()
    if not picker:
        return
    with _db() as conn:
        conn.execute("DELETE FROM picker_names WHERE name = ? COLLATE NOCASE", (picker,))


def init_db() -> None:
    def setup() -> None:
        # Create tables BEFORE migrate. Do not use _db() here — it migrates first
        # and would ALTER non-existent tables on a fresh install.
        with DB_LOCK:
            with _connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS scan_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        picker_name TEXT NOT NULL,
                        checker_name TEXT NOT NULL,
                        check_date TEXT NOT NULL,
                        check_time TEXT,
                        sales_order_no TEXT NOT NULL,
                        no_of_boxes TEXT,
                        picking_correct INTEGER NOT NULL DEFAULT 0,
                        item_correct INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'completed',
                        ticket_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT
                    );

                    CREATE TABLE IF NOT EXISTS scan_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id INTEGER NOT NULL,
                        item_scanned TEXT NOT NULL,
                        part_no TEXT,
                        description TEXT,
                        qty INTEGER NOT NULL DEFAULT 1,
                        match_status TEXT,
                        set_qty INTEGER,
                        box_qty INTEGER,
                        pallet_qty INTEGER,
                        FOREIGN KEY (session_id) REFERENCES scan_sessions(id)
                    );
                    """
                )
                global _schema_ready
                _schema_ready = False
                _migrate(conn)

    retry_locked(setup)
    # Missing master list must not block app launch (cloud sync can fill it later).
    try:
        barcode_catalog.ensure_loaded()
    except Exception:
        pass


def _item_row(item: dict[str, Any]) -> tuple:
    return (
        item.get("item_scanned", ""),
        item.get("part_no", ""),
        item.get("description", ""),
        int(item.get("qty", 1)),
        item.get("match_status", ""),
        item.get("set_qty"),
        item.get("box_qty"),
        item.get("pallet_qty"),
    )


def save_session(
    *,
    picker_name: str,
    checker_name: str,
    check_date: str,
    check_time: str = "",
    sales_order_no: str,
    no_of_boxes: str,
    items: list[dict[str, Any]],
    picking_ticket: PickingTicket | None = None,
    session_id: int | None = None,
    status: str = "completed",
) -> int:
    picking_correct, item_correct = compute_verification(picking_ticket, items)
    ticket_json = json.dumps(ticket_to_dict(picking_ticket)) if picking_ticket else None
    now = datetime.now().isoformat(timespec="seconds")
    picker_name = capitalize_person_name(picker_name)
    checker_name = capitalize_person_name(checker_name)

    def write() -> tuple[int, bool]:
        with _db() as conn:
            was_completed = False
            if session_id:
                prev = conn.execute(
                    "SELECT status FROM scan_sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                if prev is not None:
                    was_completed = str(prev["status"] or "") == "completed"
                conn.execute(
                    """
                    UPDATE scan_sessions SET
                        picker_name = ?, checker_name = ?, check_date = ?,
                        check_time = ?, sales_order_no = ?, no_of_boxes = ?,
                        picking_correct = ?, item_correct = ?, status = ?,
                        ticket_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        picker_name.strip(),
                        checker_name.strip(),
                        check_date,
                        (check_time or "").strip(),
                        sales_order_no.strip(),
                        no_of_boxes.strip(),
                        int(picking_correct),
                        int(item_correct),
                        status,
                        ticket_json,
                        now,
                        session_id,
                    ),
                )
                conn.execute("DELETE FROM scan_items WHERE session_id = ?", (session_id,))
                sid = session_id
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO scan_sessions (
                        picker_name, checker_name, check_date, check_time, sales_order_no,
                        no_of_boxes, picking_correct, item_correct, status,
                        ticket_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        picker_name.strip(),
                        checker_name.strip(),
                        check_date,
                        (check_time or "").strip(),
                        sales_order_no.strip(),
                        no_of_boxes.strip(),
                        int(picking_correct),
                        int(item_correct),
                        status,
                        ticket_json,
                        now,
                        now,
                    ),
                )
                sid = cursor.lastrowid

            for item in items:
                conn.execute(
                    """
                    INSERT INTO scan_items (
                        session_id, item_scanned, part_no, description, qty,
                        match_status, set_qty, box_qty, pallet_qty
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (sid, *_item_row(item)),
                )
            _remember_picker_name(conn, picker_name)
            return int(sid), was_completed

    sid, was_completed = retry_locked(write)

    if status == "completed" and not was_completed:
        try:
            from app import analytics

            analytics.track_pick_completed(
                picker_name=picker_name,
                sales_order_no=sales_order_no,
                session_id=int(sid) if sid is not None else None,
                username=checker_name,
            )
        except Exception:
            pass
        # Push updated fulfilment totals to Firebase immediately (non-blocking)
        # so Monitor real-time listeners refresh without waiting for heartbeat.
        try:
            from app import firebase_presence

            firebase_presence.notify_stats_changed(username=checker_name)
        except Exception:
            pass
    return sid


def list_sessions(limit: int = 100, *, status: str | None = None) -> list[dict[str, Any]]:
    return search_sessions(limit=limit, status=status)


def _parse_display_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def search_sessions(
    *,
    sales_order: str = "",
    date_from: str = "",
    date_to: str = "",
    status: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    with _db() as conn:
        query = """
            SELECT s.*,
                COUNT(i.id) AS scan_count,
                COUNT(DISTINCT COALESCE(NULLIF(i.part_no, ''), i.item_scanned)) AS item_count
            FROM scan_sessions s
            LEFT JOIN scan_items i ON i.session_id = s.id
        """
        params: list[Any] = []
        if status and status != "all":
            query += " WHERE s.status = ?"
            params.append(status)
        query += """
            GROUP BY s.id
            ORDER BY COALESCE(s.updated_at, s.created_at) DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(query, params).fetchall()

    sessions = [dict(row) for row in rows]

    sales_query = sales_order.strip().upper()
    if sales_query:
        sessions = [s for s in sessions if sales_query in s.get("sales_order_no", "").upper()]

    start = _parse_display_date(date_from)
    end = _parse_display_date(date_to)
    if start or end:
        filtered = []
        for session in sessions:
            session_date = _parse_display_date(session.get("check_date", ""))
            if session_date is None:
                continue
            if start and session_date < start:
                continue
            if end and session_date > end:
                continue
            filtered.append(session)
        sessions = filtered

    return sessions


def get_sessions_with_items(session_ids: list[int] | None = None) -> list[dict[str, Any]]:
    sessions = search_sessions(limit=1000)
    if session_ids is not None:
        wanted = set(session_ids)
        sessions = [s for s in sessions if s["id"] in wanted]
    results = []
    for summary in sessions:
        full = get_session(summary["id"])
        if full:
            results.append(full)
    return results


def delete_all_sessions() -> int:
    def wipe() -> int:
        with _db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM scan_sessions"
            ).fetchone()["count"]
            conn.execute("DELETE FROM scan_items")
            conn.execute("DELETE FROM scan_sessions")
            return int(count)

    return retry_locked(wipe)


def list_drafts(limit: int = 20) -> list[dict[str, Any]]:
    return list_sessions(limit=limit, status="draft")


def session_stats() -> dict[str, int]:
    """Return total session counts keyed by status plus ``total``."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM scan_sessions GROUP BY status"
        ).fetchall()

    stats = {"completed": 0, "draft": 0, "total": 0}
    for row in rows:
        status = row["status"] or "completed"
        count = int(row["count"])
        stats[status] = stats.get(status, 0) + count
        stats["total"] += count
    return stats


def fulfilment_counts_by_picker(*, today_only: bool = False) -> dict[str, int]:
    """Completed fulfilments (scan sessions) grouped by picker name.

    ``today_only`` matches ``check_date`` in DD/MM/YYYY (local app date format).
    """
    picks, _lines = fulfilment_stats_by_picker(today_only=today_only)
    return picks


def week_date_bounds(which: str = "this") -> tuple[date, date]:
    """Return Monday–Sunday bounds. ``which`` is ``this`` or ``last``."""
    today = date.today()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    if (which or "this").strip().lower() == "last":
        start = start - timedelta(days=7)
        end = end - timedelta(days=7)
    return start, end


def month_date_bounds(today: date | None = None) -> tuple[date, date]:
    """Return first–last day of the current calendar month."""
    today = today or date.today()
    start = today.replace(day=1)
    if today.month == 12:
        end = date(today.year, 12, 31)
    else:
        end = date(today.year, today.month + 1, 1) - timedelta(days=1)
    return start, end


def _coerce_bound_date(value: Any) -> date | None:
    """Parse ISO (YYYY-MM-DD) or display (DD/MM/YYYY) date strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        pass
    return _parse_display_date(raw)


def period_date_bounds(
    which: str = "this",
    *,
    custom_from: Any = None,
    custom_to: Any = None,
) -> tuple[date, date]:
    """Return inclusive date bounds for a leaderboard period.

    ``which``: ``today`` | ``this`` | ``last`` | ``month`` | ``custom``.
    """
    key = (which or "this").strip().lower()
    today = date.today()
    if key in {"today", "day"}:
        return today, today
    if key in {"month", "this_month", "this-month"}:
        return month_date_bounds(today)
    if key == "custom":
        start = _coerce_bound_date(custom_from) or today
        end = _coerce_bound_date(custom_to) or today
        if end < start:
            start, end = end, start
        return start, end
    return week_date_bounds("last" if key == "last" else "this")


def _session_line_count(ticket_json: str | None, item_rows: list[Any]) -> int:
    """Lines/rows for one completed pick — ticket lines, else distinct scanned parts."""
    if ticket_json:
        try:
            data = json.loads(ticket_json)
            items = data.get("items") if isinstance(data, dict) else None
            if isinstance(items, list) and items:
                return len(items)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    parts: set[str] = set()
    for item in item_rows:
        if isinstance(item, sqlite3.Row):
            part = str(item["part_no"] or item["item_scanned"] or "").strip()
        elif isinstance(item, dict):
            part = str(item.get("part_no") or item.get("item_scanned") or "").strip()
        else:
            part = ""
        if part:
            parts.add(part.upper())
    return len(parts)


def fulfilment_stats_by_picker(
    *, today_only: bool = False
) -> tuple[dict[str, int], dict[str, int]]:
    """Return (picks_by_picker, lines_by_picker) for completed sessions."""
    today = date.today().strftime("%d/%m/%Y")
    with _db() as conn:
        sessions = conn.execute(
            """
            SELECT id, picker_name, check_date, ticket_json
            FROM scan_sessions
            WHERE COALESCE(status, 'completed') = 'completed'
            """
        ).fetchall()
        items_by_session: dict[int, list[Any]] = {}
        for item in conn.execute(
            "SELECT session_id, part_no, item_scanned FROM scan_items"
        ).fetchall():
            sid = int(item["session_id"])
            items_by_session.setdefault(sid, []).append(item)

    picks: dict[str, int] = {}
    lines: dict[str, int] = {}
    for row in sessions:
        if today_only and str(row["check_date"] or "").strip() != today:
            continue
        name = capitalize_person_name(str(row["picker_name"] or "")).strip() or "Unknown"
        picks[name] = picks.get(name, 0) + 1
        lines[name] = lines.get(name, 0) + _session_line_count(
            row["ticket_json"],
            items_by_session.get(int(row["id"]), []),
        )
    return picks, lines


def fulfilment_counts_by_picker_range(start: date, end: date) -> dict[str, int]:
    """Completed fulfilments whose check_date falls in ``start``..``end`` inclusive."""
    picks, _lines = fulfilment_stats_by_picker_range(start, end)
    return picks


def fulfilment_stats_by_picker_range(
    start: date, end: date
) -> tuple[dict[str, int], dict[str, int]]:
    """Return (picks, lines) for completed sessions in ``start``..``end`` inclusive."""
    daily_picks, daily_lines = fulfilment_daily_stats(start, end)
    picks: dict[str, int] = {}
    lines: dict[str, int] = {}
    for day_map in daily_picks.values():
        for name, qty in day_map.items():
            picks[name] = picks.get(name, 0) + int(qty)
    for day_map in daily_lines.values():
        for name, qty in day_map.items():
            lines[name] = lines.get(name, 0) + int(qty)
    return picks, lines


def fulfilment_daily_stats(
    start: date, end: date
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """Return per-day (picks, lines) maps: ``{YYYY-MM-DD: {picker: count}}``."""
    if end < start:
        start, end = end, start
    with _db() as conn:
        sessions = conn.execute(
            """
            SELECT id, picker_name, check_date, ticket_json
            FROM scan_sessions
            WHERE COALESCE(status, 'completed') = 'completed'
            """
        ).fetchall()
        items_by_session: dict[int, list[Any]] = {}
        for item in conn.execute(
            "SELECT session_id, part_no, item_scanned FROM scan_items"
        ).fetchall():
            sid = int(item["session_id"])
            items_by_session.setdefault(sid, []).append(item)

    daily_picks: dict[str, dict[str, int]] = {}
    daily_lines: dict[str, dict[str, int]] = {}
    for row in sessions:
        session_date = _parse_display_date(str(row["check_date"] or ""))
        if session_date is None or session_date < start or session_date > end:
            continue
        name = capitalize_person_name(str(row["picker_name"] or "")).strip() or "Unknown"
        key = session_date.isoformat()
        picks_day = daily_picks.setdefault(key, {})
        lines_day = daily_lines.setdefault(key, {})
        picks_day[name] = picks_day.get(name, 0) + 1
        lines_day[name] = lines_day.get(name, 0) + _session_line_count(
            row["ticket_json"],
            items_by_session.get(int(row["id"]), []),
        )
    return daily_picks, daily_lines


def local_fulfilment_snapshot(
    *,
    custom_from: str | None = None,
    custom_to: str | None = None,
) -> dict[str, Any]:
    """Compact stats payload for Firebase presence heartbeats (by picker)."""
    today_map, today_lines = fulfilment_stats_by_picker(today_only=True)
    total_map, total_lines = fulfilment_stats_by_picker(today_only=False)
    week_start, week_end = week_date_bounds("this")
    last_start, last_end = week_date_bounds("last")
    month_start, month_end = month_date_bounds()
    week_map, week_lines = fulfilment_stats_by_picker_range(week_start, week_end)
    last_week_map, last_week_lines = fulfilment_stats_by_picker_range(
        last_start, last_end
    )
    month_map, month_lines = fulfilment_stats_by_picker_range(month_start, month_end)
    custom_start, custom_end = period_date_bounds(
        "custom",
        custom_from=custom_from,
        custom_to=custom_to,
    )
    # Only emit custom stats when an explicit range was provided.
    if custom_from or custom_to:
        custom_map, custom_lines = fulfilment_stats_by_picker_range(
            custom_start, custom_end
        )
    else:
        custom_map, custom_lines = {}, {}
    daily_start = date.today() - timedelta(days=62)
    daily_end = date.today()
    daily_picks, daily_lines = fulfilment_daily_stats(daily_start, daily_end)
    return {
        "today": today_map,
        "total": total_map,
        "week": week_map,
        "last_week": last_week_map,
        "month": month_map,
        "custom": custom_map,
        "today_lines": today_lines,
        "total_lines": total_lines,
        "week_lines": week_lines,
        "last_week_lines": last_week_lines,
        "month_lines": month_lines,
        "custom_lines": custom_lines,
        "daily_picks": daily_picks,
        "daily_lines": daily_lines,
        "today_sum": int(sum(today_map.values())),
        "total_sum": int(sum(total_map.values())),
        "week_sum": int(sum(week_map.values())),
        "last_week_sum": int(sum(last_week_map.values())),
        "month_sum": int(sum(month_map.values())),
        "custom_sum": int(sum(custom_map.values())),
        "today_lines_sum": int(sum(today_lines.values())),
        "week_lines_sum": int(sum(week_lines.values())),
        "month_lines_sum": int(sum(month_lines.values())),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "last_week_start": last_start.isoformat(),
        "last_week_end": last_end.isoformat(),
        "month_start": month_start.isoformat(),
        "month_end": month_end.isoformat(),
        "custom_start": custom_start.isoformat() if (custom_from or custom_to) else "",
        "custom_end": custom_end.isoformat() if (custom_from or custom_to) else "",
        "daily_start": daily_start.isoformat(),
        "daily_end": daily_end.isoformat(),
        "as_of": datetime.now().isoformat(timespec="seconds"),
    }

def get_session(session_id: int) -> dict[str, Any] | None:
    def load() -> dict[str, Any] | None:
        with _db() as conn:
            session = conn.execute(
                "SELECT * FROM scan_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not session:
                return None
            items = conn.execute(
                "SELECT * FROM scan_items WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        result = dict(session)
        result["items"] = [dict(row) for row in items]
        if result.get("ticket_json"):
            result["picking_ticket"] = ticket_from_dict(json.loads(result["ticket_json"]))
        return result

    return retry_locked(load)


def delete_session(session_id: int) -> None:
    def wipe() -> None:
        with _db() as conn:
            conn.execute("DELETE FROM scan_items WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM scan_sessions WHERE id = ?", (session_id,))

    retry_locked(wipe)


def lookup_barcode(barcode: str) -> dict[str, str] | None:
    return barcode_catalog.lookup_barcode(barcode)

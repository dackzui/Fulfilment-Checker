"""SQLite persistence for scan sessions and history."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app import barcode_catalog
from app.components import capitalize_person_name
from app.paths import get_data_dir
from app.pdf_parser import PickingTicket, ticket_from_dict, ticket_to_dict
from app.verification import compute_verification


def _db_path() -> Path:
    return get_data_dir() / "scanner.db"


def _connect() -> sqlite3.Connection:
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    session_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(scan_sessions)").fetchall()
    }
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


def list_picker_names() -> list[str]:
    with _connect() as conn:
        _migrate(conn)
        rows = conn.execute(
            "SELECT name FROM picker_names ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [row[0] for row in rows]


def remember_picker_name(name: str) -> None:
    picker = capitalize_person_name(name).strip()
    if not picker:
        return
    with _connect() as conn:
        _migrate(conn)
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
    with _connect() as conn:
        _migrate(conn)
        conn.execute("DELETE FROM picker_names WHERE name = ? COLLATE NOCASE", (picker,))


def init_db() -> None:
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
                box_qty INTEGER,
                pallet_qty INTEGER,
                FOREIGN KEY (session_id) REFERENCES scan_sessions(id)
            );
            """
        )
        _migrate(conn)
    barcode_catalog.ensure_loaded()


def _item_row(item: dict[str, Any]) -> tuple:
    return (
        item.get("item_scanned", ""),
        item.get("part_no", ""),
        item.get("description", ""),
        int(item.get("qty", 1)),
        item.get("match_status", ""),
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

    with _connect() as conn:
        _migrate(conn)
        if session_id:
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
                    match_status, box_qty, pallet_qty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sid, *_item_row(item)),
            )
        _remember_picker_name(conn, picker_name)
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
    with _connect() as conn:
        _migrate(conn)
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
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS count FROM scan_sessions").fetchone()["count"]
        conn.execute("DELETE FROM scan_items")
        conn.execute("DELETE FROM scan_sessions")
        return int(count)


def list_drafts(limit: int = 20) -> list[dict[str, Any]]:
    return list_sessions(limit=limit, status="draft")


def session_stats() -> dict[str, int]:
    """Return total session counts keyed by status plus ``total``."""
    with _connect() as conn:
        _migrate(conn)
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


def get_session(session_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
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


def delete_session(session_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM scan_items WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM scan_sessions WHERE id = ?", (session_id,))


def lookup_barcode(barcode: str) -> dict[str, str] | None:
    return barcode_catalog.lookup_barcode(barcode)

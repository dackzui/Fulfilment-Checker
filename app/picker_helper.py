"""Picking ticket bin sorter — based on Deks Picker Helper by Deepak Pius.

Source: https://github.com/deepakpius/deks_picker_helper
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any, BinaryIO, Literal

from fpdf import FPDF

from app.pick_orders import CUSTOM_PICK_ORDER, CUSTOM_PICK_ORDER_2
from app.pdf_parser import JOINED_ITEM_RE, ORDER_RE, _layout_lines, _pdf_bytes

SortMethod = Literal["ascending", "model_i", "model_ii"]

SORT_LABELS = {
    "ascending": "Sort by Ascending",
    "model_i": "Sort by Model I",
    "model_ii": "Sort by Model II",
}

PART_NUMBER_PATTERN = re.compile(r"^[A-Z0-9\-/]+$")
BIN_CONTINUATION_RE = re.compile(r"^\d+$")

CREDIT_AUTHOR = "Deepak Pius"
CREDIT_URL = "https://github.com/deepakpius/deks_picker_helper"
CREDIT_TEXT = (
    f"Picker Helper sorting by {CREDIT_AUTHOR} — {CREDIT_URL}"
)


@dataclass
class PickEntry:
    pick: str
    part_no: str
    qty_committed: str


def _safe_pdf_text(value: Any) -> str:
    text = str(value or "").replace("\r", "")
    for src, dest in (
        ("\u2014", " - "),
        ("\u2013", "-"),
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2026", "..."),
    ):
        text = text.replace(src, dest)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _pdf_lines_like_deks_picking(pdf_data: bytes) -> list[str]:
    """Match deks_picking.py: fitz page.get_text().splitlines() when available."""
    try:
        import fitz  # PyMuPDF — same library as deks_picking.py

        doc = fitz.open(stream=pdf_data, filetype="pdf")
        lines: list[str] = []
        for page in doc:
            lines.extend(page.get_text().splitlines())
        doc.close()
        if lines:
            return lines
    except ImportError:
        pass
    except Exception:
        pass

    try:
        from pdfminer.high_level import extract_text

        text = extract_text(io.BytesIO(pdf_data))
        if text and text.strip():
            return text.splitlines()
    except Exception:
        pass

    return _layout_lines(pdf_data)


def _normalize_single_bay(value: str) -> str:
    bay = (value or "").strip()
    if not bay:
        return ""
    if bay in CUSTOM_PICK_ORDER or bay in CUSTOM_PICK_ORDER_2:
        return bay
    padded = bay.zfill(3)
    if padded in CUSTOM_PICK_ORDER or padded in CUSTOM_PICK_ORDER_2:
        return padded
    if bay.isdigit():
        trimmed = str(int(bay))
        if trimmed in CUSTOM_PICK_ORDER or trimmed in CUSTOM_PICK_ORDER_2:
            return trimmed
    return bay


def _normalize_pick_bay(pick_bay: str) -> str:
    raw = (pick_bay or "").strip()
    if not raw:
        return ""
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        return ""
    return ",".join(_normalize_single_bay(part) for part in parts)


def _primary_pick_bay(pick_bay: str) -> str:
    normalized = _normalize_pick_bay(pick_bay)
    if not normalized:
        return ""
    return normalized.split(",", 1)[0].strip()


def _pick_sort_rank(pick_bay: str, method: SortMethod) -> float:
    bay = _primary_pick_bay(pick_bay)
    if not bay:
        return float("inf")

    if method == "model_ii":
        order = CUSTOM_PICK_ORDER_2
    elif method == "model_i":
        order = CUSTOM_PICK_ORDER
    else:
        try:
            return float(int(bay))
        except ValueError:
            return float("inf")

    try:
        return float(order.index(bay))
    except ValueError:
        return float("inf")


def _last_bin_segment(bin_value: str) -> str:
    if "," in bin_value:
        return bin_value.rsplit(",", 1)[-1].strip()
    return bin_value.strip()


def _is_wrapped_bin_continuation(previous_bin: str, next_line: str) -> bool:
    """True when PDF wrapped a multi-bin value across lines.

    Examples:
      '433,44' + '4'   -> '433,444'
      '1189,1' + '200' -> '1189,1200'
    """
    candidate = (next_line or "").strip()
    if not BIN_CONTINUATION_RE.match(candidate):
        return False
    # Only stitch when we already saw a comma and the trailing fragment is short.
    # Avoids gluing a complete bay onto the next part number (e.g. '1573' + '2027').
    if "," not in previous_bin:
        return False
    return len(_last_bin_segment(previous_bin)) < 3


def _read_multiline_bin_value(lines: list[str], start_idx: int) -> str:
    if start_idx >= len(lines):
        return ""

    bin_value = lines[start_idx].strip().split()[0]
    idx = start_idx + 1
    while idx < len(lines):
        candidate = lines[idx].strip()
        if not candidate:
            idx += 1
            continue
        if PART_NUMBER_PATTERN.match(candidate) and not candidate.isdigit():
            break
        if candidate == "EA":
            break
        if _is_wrapped_bin_continuation(bin_value, candidate):
            bin_value += candidate
            idx += 1
            continue
        break
    return bin_value


def extract_order_number(lines: list[str]) -> str:
    full_text = "\n".join(lines)
    match = ORDER_RE.search(full_text)
    if match:
        return match.group(1).strip()
    match = re.search(
        r"Order\s*Number\s*:\s*([A-Za-z0-9\-_/]+)",
        full_text,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else "UNKNOWN"


def _extract_ea_entries(lines: list[str]) -> list[PickEntry]:
    """deks_picking.py EA-line algorithm."""
    entries: list[PickEntry] = []

    for i in range(6, len(lines)):
        if lines[i].strip() != "EA":
            continue

        committed_qty = lines[i + 2].strip() if i + 2 < len(lines) else ""

        part_number = ""
        part_candidate_1 = lines[i - 2].strip()
        part_candidate_2 = lines[i - 3].strip()

        if PART_NUMBER_PATTERN.match(part_candidate_1):
            part_number = part_candidate_1
        elif PART_NUMBER_PATTERN.match(part_candidate_2):
            part_number = part_candidate_2

        bin_value = ""
        for j in range(i - 1, i - 10, -1):
            if j < 0:
                break
            if "PICK" not in lines[j]:
                continue
            pick_line = lines[j].strip()
            if pick_line.startswith("PICK ") and len(pick_line.split()) > 1:
                bin_text = pick_line.split(" ", 1)[1].strip()
                bin_value = bin_text.split()[0]
            elif pick_line.strip() == "PICK" and j + 1 < len(lines):
                bin_value = _read_multiline_bin_value(lines, j + 1)
            elif pick_line.startswith("PICK") and len(pick_line) > 4 and j + 1 < len(lines):
                pick_suffix = pick_line[4:].strip()
                if pick_suffix:
                    first_line = (pick_suffix + lines[j + 1].strip()).strip()
                    bin_value = _read_multiline_bin_value(
                        [first_line, *lines[j + 2 :]],
                        0,
                    )
                else:
                    bin_value = _read_multiline_bin_value(lines, j + 1)
            break

        if bin_value and part_number:
            entries.append(
                PickEntry(
                    pick=_normalize_pick_bay(bin_value),
                    part_no=part_number,
                    qty_committed=committed_qty,
                )
            )

    return entries


def _extract_joined_entries(lines: list[str]) -> list[PickEntry]:
    """Single-line ticket rows (pdfminer layout used by New Scan)."""
    entries: list[PickEntry] = []
    for line in lines:
        match = JOINED_ITEM_RE.match(line.strip())
        if not match:
            continue
        pick_bay = (match.group("pick_bay") or match.group("pick_bay2") or "").strip()
        part_number = (match.group("part_no") or "").strip()
        qty = (match.group("qty") or "").strip()
        if pick_bay and part_number:
            entries.append(
                PickEntry(
                    pick=_normalize_pick_bay(pick_bay),
                    part_no=part_number,
                    qty_committed=qty,
                )
            )
    return entries


def extract_pick_entries_from_pdf(pdf_data: bytes) -> list[PickEntry]:
    """Try deks EA lines and joined layout lines from every PDF text source."""
    seen: set[tuple[str, str]] = set()
    entries: list[PickEntry] = []

    def add(entry: PickEntry) -> None:
        key = (entry.part_no.strip().upper(), entry.pick)
        if key in seen:
            return
        seen.add(key)
        entries.append(entry)

    line_sets = [
        _pdf_lines_like_deks_picking(pdf_data),
        _layout_lines(pdf_data),
    ]
    for lines in line_sets:
        for entry in _extract_ea_entries(lines):
            add(entry)
        for entry in _extract_joined_entries(lines):
            add(entry)

    return entries


def extract_pick_entries(lines: list[str]) -> list[PickEntry]:
    entries = _extract_ea_entries(lines)
    if entries:
        return entries
    return _extract_joined_entries(lines)


def sort_pick_entries(
    entries: list[PickEntry],
    method: SortMethod = "model_i",
) -> list[PickEntry]:
    def ascending_key(entry: PickEntry):
        try:
            return (0, int(entry.pick))
        except ValueError:
            return (1, entry.pick)

    def custom_sort_key(entry: PickEntry):
        return _pick_sort_rank(entry.pick, "model_i")

    def custom_sort_key2(entry: PickEntry):
        return _pick_sort_rank(entry.pick, "model_ii")

    if method == "ascending":
        return sorted(entries, key=ascending_key)
    if method == "model_ii":
        return sorted(entries, key=custom_sort_key2)
    return sorted(entries, key=custom_sort_key)


def sort_ticket_items(ticket, method: SortMethod = "model_i") -> None:
    """Re-sort ticket line items by pick bay using Model I / II / Ascending."""
    ticket.items.sort(key=lambda item: _pick_sort_rank(item.pick_bay, method))


def attach_pick_bays_to_ticket(
    ticket,
    source: str | bytes | BinaryIO,
    *,
    method: SortMethod = "model_i",
) -> None:
    """Assign pick_bay and sort items (deks_picking.py + joined-line PDFs)."""
    from app.pdf_parser import parts_match

    pdf_data = _pdf_bytes(source)
    entries = sort_pick_entries(
        extract_pick_entries_from_pdf(pdf_data),
        method=method,
    )

    for item in ticket.items:
        if item.pick_bay:
            item.pick_bay = _normalize_pick_bay(item.pick_bay)
            continue
        for entry in entries:
            if parts_match(item.part_no, entry.part_no):
                item.pick_bay = entry.pick
                break
            # Ticket parser may have stored pick bay in the part_no column.
            if parts_match(item.part_no, entry.pick):
                item.pick_bay = entry.pick
                item.part_no = entry.part_no
                break

    if entries:
        def item_order(item) -> tuple[float, int]:
            for index, entry in enumerate(entries):
                if parts_match(item.part_no, entry.part_no):
                    return (_pick_sort_rank(entry.pick, method), index)
                if parts_match(item.part_no, entry.pick):
                    return (_pick_sort_rank(entry.pick, method), index)
            return (_pick_sort_rank(item.pick_bay, method), 999999)

        ticket.items.sort(key=item_order)
    else:
        ticket.items.sort(key=lambda item: _pick_sort_rank(item.pick_bay, method))


def analyze_picking_pdf(
    source: str | bytes | BinaryIO,
    *,
    method: SortMethod = "model_i",
) -> tuple[str, list[PickEntry]]:
    pdf_data = _pdf_bytes(source)
    lines = _pdf_lines_like_deks_picking(pdf_data)
    order_number = extract_order_number(lines)
    entries = sort_pick_entries(extract_pick_entries_from_pdf(pdf_data), method=method)
    return order_number, entries


def export_sorted_picks_pdf_bytes(
    entries: list[PickEntry],
    *,
    order_number: str = "",
    sort_method: SortMethod = "model_i",
) -> bytes:
    class SummaryPDF(FPDF):
        def header(self):
            self.set_font("Helvetica", "B", 12)
            title = "Picking Ticket Summary"
            if order_number:
                title += f" - Order {order_number}"
            self.cell(0, 10, _safe_pdf_text(title), new_x="LMARGIN", new_y="NEXT", align="C")
            self.set_font("Helvetica", "", 8)
            self.cell(
                0,
                5,
                _safe_pdf_text(f"{SORT_LABELS.get(sort_method, sort_method)} | {CREDIT_TEXT}"),
                new_x="LMARGIN",
                new_y="NEXT",
                align="C",
            )
            self.ln(2)

    pdf = SummaryPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 10)
    col_widths = (50, 80, 40)
    headers = ("PICK", "Part #", "Qty Committed")
    for i, header in enumerate(headers):
        pdf.cell(col_widths[i], 8, _safe_pdf_text(header), border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 10)
    for entry in entries:
        pdf.cell(col_widths[0], 8, _safe_pdf_text(entry.pick), border=1)
        pdf.cell(col_widths[1], 8, _safe_pdf_text(entry.part_no), border=1)
        pdf.cell(col_widths[2], 8, _safe_pdf_text(entry.qty_committed), border=1)
        pdf.ln()
    return bytes(pdf.output())

"""Parse DEKS picking ticket PDF files (pdfminer.six — Android compatible)."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTAnno, LTChar, LTTextContainer, LTTextLine

ORDER_RE = re.compile(r"Order Number\s*:\s*(\S+)", re.IGNORECASE)
ORDER_DATE_RE = re.compile(r"Order Date\s*:\s*(.+)", re.IGNORECASE)
SHIP_DATE_RE = re.compile(r"Ship Date\s*:\s*(.+)", re.IGNORECASE)
ADDRESS_SPLIT_X = 185
JOINED_ITEM_RE = re.compile(
    # Layout: PICK[bay_prefix] <Part #> <Description> EA <Ordered> <Committed> <B/O>
    # Bay may start glued to PICK (PICK13) and finish on the next line (09 -> 1309).
    r"^PICK(?P<bay_prefix>\d*)\s+(?P<part_no>[A-Z0-9\-/]+)\s+(?P<description>.+?)\s+"
    r"EA\s+(?P<qty_ordered>\d+)\s+(?P<qty>\d+)\s+(?P<qty_bo>\d+)\s*$",
    re.IGNORECASE,
)
ITEM_LINE_RE = re.compile(
    r"^PICK(?P<bay_prefix>\d*)\s+(\S+)\s+(.+?)\s+EA\s+\d+\s+(\d+)\s+\d+\s*$",
    re.IGNORECASE,
)
# Leading bin fragment on a follow-on line, optionally with description text after it.
BIN_FOLLOW_RE = re.compile(r"^([\d,]+)(?:\s+(.*))?$")
SKIP_LINE_RE = re.compile(
    r"^(PICKING NOTES:|Customer|Order Date|Order Number|Ship Date|\d+ of \d+)",
    re.IGNORECASE,
)


@dataclass
class PickingTicketItem:
    part_no: str
    description: str
    # Expected pick qty = Qty Committed from the ticket (not Qty Ordered).
    # Back-order tickets can be re-picked later when more is committed.
    qty_ordered: int
    qty_scanned: int = 0
    pick_bay: str = ""
    picked: bool = False

    @property
    def is_complete(self) -> bool:
        return self.qty_scanned >= self.qty_ordered

    @property
    def is_partial(self) -> bool:
        return 0 < self.qty_scanned < self.qty_ordered


@dataclass
class PickingTicket:
    order_number: str
    items: list[PickingTicketItem] = field(default_factory=list)
    source_file: str = ""
    order_date: str = ""
    ship_date: str = ""
    ship_to: str = ""
    bill_to: str = ""


def _pdf_bytes(source: str | Path | bytes | BinaryIO) -> bytes:
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    if isinstance(source, bytes):
        return source
    if hasattr(source, "seek"):
        source.seek(0)
    return source.read()


def _extract_words(page_layout, page_height: float) -> list[dict]:
    """Extract word positions (pdfplumber-compatible top-left origin)."""
    words: list[dict] = []

    def walk(obj) -> None:
        if isinstance(obj, LTTextLine):
            current = ""
            x0 = None
            top = None
            for char in obj:
                if isinstance(char, LTAnno):
                    if current:
                        words.append({"text": current, "x0": x0, "top": top})
                        current = ""
                        x0 = top = None
                    continue
                if not isinstance(char, LTChar):
                    continue
                text = char.get_text()
                if text.isspace():
                    if current:
                        words.append({"text": current, "x0": x0, "top": top})
                        current = ""
                        x0 = top = None
                    continue
                if x0 is None:
                    x0 = char.x0
                    top = page_height - char.y1
                current += text
            if current:
                words.append({"text": current, "x0": x0, "top": top})
        elif isinstance(obj, LTTextContainer):
            for child in obj:
                walk(child)

    for element in page_layout:
        walk(element)
    return words


def _words_to_lines(words: list[dict], y_tolerance: float = 3.0) -> list[str]:
    if not words:
        return []

    sorted_words = sorted(words, key=lambda word: (word["top"], word["x0"]))
    grouped: list[list[dict]] = []
    current_group = [sorted_words[0]]
    current_top = sorted_words[0]["top"]

    for word in sorted_words[1:]:
        if abs(word["top"] - current_top) <= y_tolerance:
            current_group.append(word)
        else:
            grouped.append(current_group)
            current_group = [word]
            current_top = word["top"]
    grouped.append(current_group)

    return [
        " ".join(word["text"] for word in sorted(group, key=lambda w: w["x0"]))
        for group in grouped
    ]


def _layout_lines(pdf_data: bytes) -> list[str]:
    lines: list[str] = []
    for page_layout in extract_pages(io.BytesIO(pdf_data)):
        page_height = float(page_layout.height)
        words = _extract_words(page_layout, page_height)
        lines.extend(_words_to_lines(words))
    return lines


def _parse_dates(text: str) -> tuple[str, str]:
    order_date = ""
    ship_date = ""
    order_match = ORDER_DATE_RE.search(text)
    ship_match = SHIP_DATE_RE.search(text)
    if order_match:
        order_date = order_match.group(1).strip()
    if ship_match:
        ship_date = ship_match.group(1).strip()
    return order_date, ship_date


def _parse_addresses(pdf_data: bytes) -> tuple[str, str]:
    try:
        page_layout = next(extract_pages(io.BytesIO(pdf_data)))
        page_height = float(page_layout.height)
        words = _extract_words(page_layout, page_height)
    except Exception:
        return "", ""

    header_top = None
    end_top = None
    for index, word in enumerate(words):
        if (
            word["text"] == "Ship"
            and index + 1 < len(words)
            and words[index + 1]["text"] == "To"
            and abs(words[index + 1]["top"] - word["top"]) < 5
        ):
            header_top = word["top"]
        if word["text"] == "Customer" and header_top is not None and end_top is None:
            end_top = word["top"]

    if header_top is None or end_top is None:
        return "", ""

    block = [word for word in words if header_top < word["top"] < end_top - 2]
    ship_words = [word for word in block if word["x0"] < ADDRESS_SPLIT_X]
    bill_words = [word for word in block if word["x0"] >= ADDRESS_SPLIT_X]
    return _words_to_multiline(ship_words), _words_to_multiline(bill_words)


def _words_to_multiline(words: list[dict]) -> str:
    lines: dict[int, list[str]] = {}
    for word in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        top = round(word["top"])
        lines.setdefault(top, []).append(word["text"])
    return "\n".join(" ".join(lines[top]) for top in sorted(lines))


def _looks_like_item_line(line: str) -> bool:
    stripped = line.strip()
    if ITEM_LINE_RE.match(stripped):
        return True
    return bool(
        re.match(r"^PICK\d+\s+\S+", stripped, re.IGNORECASE)
        and re.search(r"\s+EA\s+\d+\s+\d+\s+\d+\s*$", stripped, re.IGNORECASE)
    )


def _is_description_continuation(line: str) -> bool:
    stripped = line.strip()
    if not stripped or _looks_like_item_line(stripped):
        return False
    if re.match(r"^PICK\d*\s", stripped, re.IGNORECASE):
        return False
    if SKIP_LINE_RE.match(stripped):
        return False
    if re.fullmatch(r"[\d,.\s]+", stripped):
        return False
    return bool(re.search(r"[A-Za-z]", stripped))


def _append_continuation(description: str, line: str) -> str:
    stripped = line.strip()
    match = re.match(r"^[\d,.\s]+\s+(.+)$", stripped)
    extra = match.group(1).strip() if match else stripped
    if extra.lower() in description.lower():
        return description
    return f"{description} {extra}".strip()


def _merge_bin_fragments(fragments: list[str]) -> str:
    """Join wrapped bin fragments.

    Examples:
      '433,44' + '4' -> '433,444'
      '13' + '09' -> '1309'  (PICK13 + following '09')
    """
    if not fragments:
        return ""
    merged = fragments[0].strip()
    for fragment in fragments[1:]:
        piece = fragment.strip()
        if not piece:
            continue
        # Complete a comma-wrapped segment: '433,44' + '4' -> '433,444'
        if "," in merged and len(merged.rsplit(",", 1)[-1]) < 3 and piece.isdigit():
            merged += piece
            continue
        # Complete a short wrapped bay without comma: '13' + '09' -> '1309'
        merged_digits = merged.replace(",", "")
        if (
            merged_digits.isdigit()
            and piece.isdigit()
            and len(merged_digits) < 4
            and len(merged_digits) + len(piece) <= 4
        ):
            merged += piece
            continue
        if merged.endswith(",") or piece.startswith(","):
            merged = merged.rstrip(",") + "," + piece.lstrip(",")
            continue
        merged = f"{merged},{piece}" if merged else piece
    return merged


def _consume_item_follow_lines(
    lines: list[str],
    start_index: int,
    description: str,
    *,
    bay_prefix: str = "",
) -> tuple[str, str, int]:
    """Read bin + description continuations after a PICK/EA item line.

    Returns (pick_bay, description, next_index).
    """
    index = start_index
    bin_fragments: list[str] = []
    if (bay_prefix or "").strip():
        bin_fragments.append(bay_prefix.strip())

    while index < len(lines):
        next_line = lines[index].strip()
        if not next_line:
            index += 1
            continue
        if _looks_like_item_line(next_line) or SKIP_LINE_RE.match(next_line):
            break

        bin_match = BIN_FOLLOW_RE.match(next_line)
        if bin_match:
            bin_fragments.append(bin_match.group(1))
            extra = (bin_match.group(2) or "").strip()
            if extra and _is_description_continuation(extra):
                description = _append_continuation(description, extra)
            index += 1
            continue

        if _is_description_continuation(next_line):
            description = _append_continuation(description, next_line)
            index += 1
            continue

        break

    return _merge_bin_fragments(bin_fragments), description, index


def parse_picking_ticket(
    source: str | Path | bytes | BinaryIO,
    *,
    source_file: str = "",
) -> PickingTicket:
    pdf_data = _pdf_bytes(source)
    lines = _layout_lines(pdf_data)
    text = "\n".join(lines)

    order_match = ORDER_RE.search(text)
    if not order_match:
        raise ValueError("Could not find Order Number in the PDF.")

    order_date, ship_date = _parse_dates(text)
    ship_to, bill_to = _parse_addresses(pdf_data)

    items: list[PickingTicketItem] = []
    index = 0

    while index < len(lines):
        line = lines[index].strip()
        match = ITEM_LINE_RE.match(line)
        if not match:
            index += 1
            continue

        bay_prefix = (match.group("bay_prefix") or "").strip()
        part_no = match.group(2)
        description = match.group(3)
        qty_text = match.group(4)
        index += 1
        pick_bay, description, index = _consume_item_follow_lines(
            lines,
            index,
            description.strip(),
            bay_prefix=bay_prefix,
        )

        items.append(
            PickingTicketItem(
                part_no=part_no.strip(),
                description=description.strip(),
                qty_ordered=int(qty_text),  # Qty Committed
                pick_bay=pick_bay,
            )
        )

    if not items:
        raise ValueError("No line items found in the picking ticket PDF.")

    return PickingTicket(
        order_number=order_match.group(1).strip(),
        items=items,
        source_file=source_file,
        order_date=order_date,
        ship_date=ship_date,
        ship_to=ship_to,
        bill_to=bill_to,
    )


def normalize_part(part_no: str) -> str:
    return part_no.strip().upper()


def parts_match(part_a: str, part_b: str) -> bool:
    """Compare part numbers, including numeric forms like 2046 vs 02046."""
    left = normalize_part(part_a)
    right = normalize_part(part_b)
    if not left or not right:
        return False
    if left == right:
        return True
    if left.isdigit() and right.isdigit():
        return int(left) == int(right)
    return False


def find_ticket_item(
    items: list[PickingTicketItem], part_no: str
) -> PickingTicketItem | None:
    matches = find_ticket_items(items, part_no)
    return matches[0] if matches else None


def find_ticket_items(
    items: list[PickingTicketItem], part_no: str
) -> list[PickingTicketItem]:
    target = normalize_part(part_no)
    if not target:
        return []
    return [item for item in items if parts_match(item.part_no, target)]


def total_qty_ordered_for_part(
    items: list[PickingTicketItem], part_no: str
) -> int:
    return sum(item.qty_ordered for item in find_ticket_items(items, part_no))


def apply_scans_to_ticket_items(
    items: list[PickingTicketItem],
    scanned_items: list[dict[str, Any]],
) -> None:
    """FIFO-allocate on-ticket scans across duplicate part rows on the ticket."""
    for item in items:
        item.qty_scanned = 0

    for scan in scanned_items:
        if scan.get("match_status") != "on_ticket":
            continue
        part_no = scan.get("part_no", "")
        remaining = int(scan.get("qty", 0))
        if remaining <= 0:
            continue
        for item in items:
            if remaining <= 0:
                break
            if not parts_match(item.part_no, part_no):
                continue
            capacity = item.qty_ordered - item.qty_scanned
            if capacity <= 0:
                continue
            item.qty_scanned += min(remaining, capacity)
            remaining -= min(remaining, capacity)


def ticket_to_dict(ticket: PickingTicket) -> dict:
    return {
        "order_number": ticket.order_number,
        "source_file": ticket.source_file,
        "order_date": ticket.order_date,
        "ship_date": ticket.ship_date,
        "ship_to": ticket.ship_to,
        "bill_to": ticket.bill_to,
        "items": [
            {
                "part_no": item.part_no,
                "description": item.description,
                "qty_ordered": item.qty_ordered,
                "qty_scanned": item.qty_scanned,
                "pick_bay": item.pick_bay,
                "picked": bool(item.picked),
            }
            for item in ticket.items
        ],
    }


def ticket_from_dict(data: dict) -> PickingTicket:
    return PickingTicket(
        order_number=data["order_number"],
        source_file=data.get("source_file", ""),
        order_date=data.get("order_date", ""),
        ship_date=data.get("ship_date", ""),
        ship_to=data.get("ship_to", ""),
        bill_to=data.get("bill_to", ""),
        items=[
            PickingTicketItem(
                part_no=item["part_no"],
                description=item["description"],
                qty_ordered=int(item["qty_ordered"]),
                qty_scanned=int(item.get("qty_scanned", 0)),
                pick_bay=item.get("pick_bay", ""),
                picked=bool(item.get("picked", False)),
            )
            for item in data.get("items", [])
        ],
    )

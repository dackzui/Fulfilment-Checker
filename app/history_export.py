"""Export history sessions to PDF."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fpdf import FPDF

from app.components import format_check_when
from app.item_grouping import box_qty_display, group_scanned_items
from app.paths import get_data_dir


def _export_dir() -> Path:
    return get_data_dir() / "exports"


def _safe_text(value: Any) -> str:
    text = str(value or "").replace("\r", "")
    for src, dest in (
        ("\u2014", " - "),  # em dash
        ("\u2013", "-"),  # en dash
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2026", "..."),
    ):
        text = text.replace(src, dest)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _ticket_info(session: dict) -> dict[str, str]:
    ticket = session.get("picking_ticket")
    if ticket:
        return {
            "sales_order_no": ticket.order_number,
            "order_date": ticket.order_date or "—",
            "ship_date": ticket.ship_date or "—",
            "ship_to": ticket.ship_to or "—",
            "bill_to": ticket.bill_to or "—",
        }
    return {
        "sales_order_no": session.get("sales_order_no", "—"),
        "order_date": "—",
        "ship_date": "—",
        "ship_to": "—",
        "bill_to": "—",
    }


class _ReportPDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def _barcode_cell_text(scan: dict) -> str:
    barcode = (scan.get("item_scanned") or "").strip()
    box_display = box_qty_display(scan)
    if box_display:
        return f"{barcode} ({box_display})"
    if scan.get("pallet_qty"):
        return f"{barcode} (Pallet)"
    return barcode


def _write_items_table(pdf: FPDF, items: list[dict]) -> None:
    grouped = group_scanned_items(items)

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(32, 7, _safe_text("Part No."), border=1)
    pdf.cell(72, 7, _safe_text("Description"), border=1)
    pdf.cell(58, 7, _safe_text("Barcode"), border=1)
    pdf.cell(18, 7, _safe_text("Qty"), border=1, ln=True)
    pdf.set_font("Helvetica", "", 8)

    for item in grouped:
        scans = item.get("scans") or []
        if not scans:
            scans = [{"item_scanned": item.get("item_scanned", ""), "qty": item.get("qty", 1)}]

        for index, scan in enumerate(scans):
            part = _safe_text(item.get("part_no") or "-") if index == 0 else ""
            description = _safe_text(item.get("description") or "-")[:42] if index == 0 else ""
            pdf.cell(32, 6, part, border=1)
            pdf.cell(72, 6, description, border=1)
            pdf.cell(58, 6, _safe_text(_barcode_cell_text(scan)), border=1)
            pdf.cell(18, 6, _safe_text(scan.get("qty", "")), border=1, ln=True)

        if len(scans) > 1:
            pdf.cell(32, 6, "", border=1)
            pdf.cell(72, 6, "", border=1)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(58, 6, _safe_text("Part Total"), border=1)
            pdf.cell(18, 6, _safe_text(item.get("qty", "")), border=1, ln=True)
            pdf.set_font("Helvetica", "", 8)

def _write_session_section(pdf: FPDF, session: dict) -> None:
    ticket = _ticket_info(session)
    items = session.get("items", [])

    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _safe_text(f"Checking #{session['id']} — Sales Order No: {ticket['sales_order_no']}"), ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, _safe_text(f"Order Date: {ticket['order_date']}    Ship Date: {ticket['ship_date']}"), ln=True)
    pdf.cell(
        0,
        6,
        _safe_text(
            f"Picker: {session.get('picker_name', '')}    Checker: {session.get('checker_name', '')}    "
            f"Checked: {format_check_when(session)}    Boxes: {session.get('no_of_boxes') or '—'}"
        ),
        ln=True,
    )
    pdf.cell(
        0,
        6,
        _safe_text(
            f"Status: {session.get('status', 'completed')}    "
            f"Picking Correct: {'Yes' if session.get('picking_correct') else 'No'}    "
            f"Item Correct: {'Yes' if session.get('item_correct') else 'No'}"
        ),
        ln=True,
    )
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(95, 6, _safe_text("Ship To"), ln=0)
    pdf.cell(0, 6, _safe_text("Bill To"), ln=True)
    pdf.set_font("Helvetica", "", 9)
    ship_lines = _safe_text(ticket["ship_to"]).split("\n")
    bill_lines = _safe_text(ticket["bill_to"]).split("\n")
    rows = max(len(ship_lines), len(bill_lines), 1)
    for index in range(rows):
        ship_line = ship_lines[index] if index < len(ship_lines) else ""
        bill_line = bill_lines[index] if index < len(bill_lines) else ""
        pdf.cell(95, 5, ship_line, ln=0)
        pdf.cell(0, 5, bill_line, ln=True)

    pdf.ln(4)
    _write_items_table(pdf, items)
    pdf.ln(6)


def export_session_pdf_bytes(session: dict) -> bytes:
    pdf = _ReportPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Picking Verification Report", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, _safe_text(f"Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}"), ln=True)
    pdf.ln(4)
    _write_session_section(pdf, session)
    return bytes(pdf.output())


def export_session_pdf(session: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(export_session_pdf_bytes(session))
    return output_path


def export_report_pdf_bytes(
    sessions: list[dict],
    *,
    filter_summary: str = "",
) -> bytes:
    pdf = _ReportPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Picking Verification History Report", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, _safe_text(f"Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}"), ln=True)
    if filter_summary:
        pdf.multi_cell(0, 5, _safe_text(f"Filters: {filter_summary}"))
        # multi_cell leaves the cursor on the right edge; reset before next line.
        pdf.set_x(pdf.l_margin)
    pdf.cell(0, 6, _safe_text(f"Sessions: {len(sessions)}"), ln=True)
    pdf.ln(4)

    for index, session in enumerate(sessions):
        if index > 0:
            pdf.add_page()
        _write_session_section(pdf, session)

    return bytes(pdf.output())


def export_report_pdf(
    sessions: list[dict],
    output_path: Path,
    *,
    filter_summary: str = "",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(
        export_report_pdf_bytes(sessions, filter_summary=filter_summary)
    )
    return output_path

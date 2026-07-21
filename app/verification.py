"""Automatic picking and item verification status."""

from __future__ import annotations

from typing import Any

from app.pdf_parser import PickingTicket


def compute_verification(
    picking_ticket: PickingTicket | None,
    scanned_items: list[dict[str, Any]],
) -> tuple[bool, bool]:
    """Return (picking_correct, item_correct)."""
    if not picking_ticket or not scanned_items:
        return False, False

    item_correct = all(
        scan.get("match_status") in {"on_ticket", "catalog_only", "manual_no_barcode"}
        for scan in scanned_items
    )

    sync_complete = all(item.is_complete for item in picking_ticket.items)
    picking_correct = item_correct and sync_complete and len(picking_ticket.items) > 0

    return picking_correct, item_correct

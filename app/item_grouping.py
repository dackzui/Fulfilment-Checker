"""Group scanned line items by part number for display."""

from __future__ import annotations

from typing import Any

from app.pdf_parser import normalize_part


def box_pack_label(item: dict[str, Any]) -> str | None:
    """Return box count label e.g. ``2 Boxes``."""
    from app import barcode_catalog

    if item.get("manual"):
        return None

    barcode = (item.get("item_scanned") or "").strip()
    if not barcode:
        return None

    pack_count = item.get("pack_count")
    if pack_count:
        n = int(pack_count)
        return f"{n} Box" if n == 1 else f"{n} Boxes"

    if not item.get("box_qty"):
        return None

    lookup = barcode_catalog.lookup_barcode(barcode)
    unit = int(lookup["box_qty"]) if lookup and lookup.get("box_qty") else None
    if not unit:
        return None

    total = int(item.get("qty", 0))
    n = max(1, total // unit)
    return f"{n} Box" if n == 1 else f"{n} Boxes"


def box_qty_display(item: dict[str, Any]) -> str | None:
    """Return e.g. ``Qty 40 (2 Boxes)`` for box scans."""
    pack = box_pack_label(item)
    if not pack:
        return None
    return f"Qty {int(item.get('qty', 0))} ({pack})"


def _group_key(item: dict[str, Any]) -> str:
    part_no = (item.get("part_no") or "").strip()
    if part_no:
        return part_no.upper()
    return (item.get("item_scanned") or "").strip().upper()


def _scan_qty_type(item: dict[str, Any]) -> str:
    if item.get("pallet_qty"):
        return "pallet"
    if item.get("box_qty"):
        return "box"
    return "unit"


def _scan_barcode_key(item: dict[str, Any]) -> str:
    if item.get("manual"):
        part_no = normalize_part(item.get("part_no", ""))
        return f"manual|{part_no}"
    barcode = (item.get("item_scanned") or "").strip().upper()
    return f"{barcode}|{_scan_qty_type(item)}"


def consolidate_scans_by_barcode(scans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge identical barcodes within a part, summing quantities."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for scan in scans:
        if scan.get("manual"):
            part_no = (scan.get("part_no") or "").strip()
            if not part_no:
                continue
            key = _scan_barcode_key(scan)
            if key not in merged:
                merged[key] = {
                    "item_scanned": "Manual",
                    "qty": 0,
                    "box_qty": None,
                    "pallet_qty": None,
                    "part_no": scan.get("part_no", ""),
                    "description": scan.get("description", ""),
                    "manual": True,
                }
                order.append(key)
            merged[key]["qty"] += int(scan.get("qty", 1))
            continue

        barcode = (scan.get("item_scanned") or "").strip()
        if not barcode:
            continue

        key = _scan_barcode_key(scan)
        qty_type = _scan_qty_type(scan)
        if key not in merged:
            merged[key] = {
                "item_scanned": barcode,
                "qty": 0,
                "box_qty": scan.get("box_qty") if qty_type == "box" else None,
                "pallet_qty": scan.get("pallet_qty") if qty_type == "pallet" else None,
                "pack_count": int(scan.get("pack_count") or 0) if qty_type == "box" else 0,
                "part_no": scan.get("part_no", ""),
                "description": scan.get("description", ""),
            }
            order.append(key)

        entry = merged[key]
        entry["qty"] += int(scan.get("qty", 1))
        if qty_type == "box":
            added = int(scan.get("pack_count") or 0)
            if not added:
                from app import barcode_catalog

                lookup = barcode_catalog.lookup_barcode(barcode)
                unit = int(lookup["box_qty"]) if lookup and lookup.get("box_qty") else None
                if unit:
                    added = max(1, int(scan.get("qty", 1)) // unit)
            entry["pack_count"] = int(entry.get("pack_count") or 0) + added
        if entry.get("pallet_qty") is not None:
            entry["pallet_qty"] = entry["qty"]

    return [merged[key] for key in order]


def format_scan_label(item: dict[str, Any]) -> str:
    """Format one consolidated scan line for grouped display."""
    if item.get("manual"):
        qty = int(item.get("qty", 1))
        return f"Manual — Qty: {qty}"
    barcode = (item.get("item_scanned") or "").strip()
    box_display = box_qty_display(item)
    if box_display:
        return f"{barcode} — {box_display}"
    qty = int(item.get("qty", 1))
    if item.get("pallet_qty"):
        return f"{barcode} — Pallet: {qty}"
    if qty > 1:
        return f"{barcode} — Qty: {qty}"
    return barcode


def group_scanned_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for item in items:
        key = _group_key(item)
        if not key:
            continue

        if key not in grouped:
            grouped[key] = {
                "item_scanned": "",
                "part_no": item.get("part_no", ""),
                "description": item.get("description", ""),
                "qty": 0,
                "scans": [],
            }

        entry = grouped[key]
        entry["qty"] += int(item.get("qty", 1))
        entry["scans"].append(item)

    results: list[dict[str, Any]] = []
    for entry in grouped.values():
        scans = consolidate_scans_by_barcode(entry["scans"])
        entry["scans"] = scans
        entry["item_scanned"] = "\n".join(format_scan_label(scan) for scan in scans)
        results.append(entry)

    return sorted(results, key=lambda row: row.get("part_no", ""))

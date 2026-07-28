"""DEKS Top Pickers Monitor — Super Admin desktop entry point."""

from __future__ import annotations

import traceback
from pathlib import Path

import flet as ft

from app.monitor_app import main


def _write_startup_error(exc: BaseException) -> None:
    try:
        path = Path(__file__).resolve().parent / "data" / "startup_err.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )
    except Exception:
        pass


if __name__ == "__main__":
    try:
        ft.run(main, assets_dir="assets")
    except BaseException as exc:
        _write_startup_error(exc)
        raise

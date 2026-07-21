"""Picking Barcode Scanner — entry point."""

import flet as ft

from app.main_app import main


if __name__ == "__main__":
    ft.run(main, assets_dir="assets")

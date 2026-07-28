"""Writable and bundled paths for desktop and mobile builds."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import flet as ft

_data_dir: Path | None = None

_SEED_FILES = ("BarcodeMasterList.xlsx", "deks_logo.png", "config.json")


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """Writable app root (repo root, or folder containing the packaged EXE)."""
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundle_root() -> Path:
    """Read-only resources shipped with a packaged build."""
    if _is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled_data_dir() -> Path:
    return _bundle_root() / "data"


def get_data_dir() -> Path:
    global _data_dir
    if _data_dir is None:
        _data_dir = project_root() / "data"
        _data_dir.mkdir(parents=True, exist_ok=True)
    return _data_dir


async def init_app_storage(page: ft.Page) -> None:
    """Point data storage at app documents on mobile; seed bundled files once."""
    global _data_dir
    from flet.utils.platform_utils import is_mobile

    if not is_mobile():
        _data_dir = project_root() / "data"
        _data_dir.mkdir(parents=True, exist_ok=True)
        if _is_frozen():
            _seed_mobile_data()
        return

    from flet.controls.services.storage_paths import StoragePaths

    docs = await StoragePaths().get_application_documents_directory()
    _data_dir = Path(docs) / "picker_check_data"
    _data_dir.mkdir(parents=True, exist_ok=True)
    (_data_dir / "exports").mkdir(exist_ok=True)
    _seed_mobile_data()


def _seed_mobile_data() -> None:
    data = get_data_dir()
    bundled = bundled_data_dir()
    for name in _SEED_FILES:
        src = bundled / name
        dest = data / name
        if src.exists() and not dest.exists():
            shutil.copy2(src, dest)


def logo_path() -> Path:
    for candidate in (
        get_data_dir() / "deks_logo.png",
        bundled_data_dir() / "deks_logo.png",
        project_root() / "assets" / "deks_logo.png",
        _bundle_root() / "assets" / "deks_logo.png",
    ):
        if candidate.exists():
            return candidate

    assets_dir = os.environ.get("FLET_ASSETS_DIR")
    if assets_dir:
        asset_logo = Path(assets_dir) / "deks_logo.png"
        if asset_logo.exists():
            return asset_logo
    return bundled_data_dir() / "deks_logo.png"


def logo_src() -> str:
    """Image src for the sidebar logo (works on desktop and in APK)."""
    assets_dir = os.environ.get("FLET_ASSETS_DIR")
    logo = logo_path()
    if assets_dir:
        try:
            return str(logo.relative_to(Path(assets_dir)))
        except ValueError:
            pass
    return str(logo)

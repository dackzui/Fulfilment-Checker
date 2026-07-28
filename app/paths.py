"""Writable and bundled paths for desktop and mobile builds."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import flet as ft

_data_dir: Path | None = None

_SEED_FILES = ("BarcodeMasterList.xlsx", "deks_logo.png", "config.json")
# Copied into a packaged Monitor data folder when missing (local install only).
_RUNTIME_SEED_FILES = (
    "firebase_config.json",
    "config.json",
    "admins.json",
    "deks_logo.png",
    "BarcodeMasterList.xlsx",
)


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


def _shared_desktop_data_dir() -> Path:
    """Stable per-user data folder for packaged desktop apps."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "DEKS" / "picker-check" / "data"


def get_data_dir() -> Path:
    global _data_dir
    if _data_dir is None:
        override = (os.environ.get("PICKER_CHECK_DATA") or "").strip()
        if override:
            _data_dir = Path(override)
        elif _is_frozen():
            # Packaged Monitor/Scanner keep config here so reinstalls keep Firebase keys.
            _data_dir = _shared_desktop_data_dir()
        else:
            _data_dir = project_root() / "data"
        _data_dir.mkdir(parents=True, exist_ok=True)
    return _data_dir


async def init_app_storage(page: ft.Page) -> None:
    """Point data storage at app documents on mobile; seed bundled files once."""
    global _data_dir
    from flet.utils.platform_utils import is_mobile

    if not is_mobile():
        override = (os.environ.get("PICKER_CHECK_DATA") or "").strip()
        if override:
            _data_dir = Path(override)
        elif _is_frozen():
            _data_dir = _shared_desktop_data_dir()
        else:
            _data_dir = project_root() / "data"
        _data_dir.mkdir(parents=True, exist_ok=True)
        _seed_desktop_data()
        return

    from flet.controls.services.storage_paths import StoragePaths

    docs = await StoragePaths().get_application_documents_directory()
    _data_dir = Path(docs) / "picker_check_data"
    _data_dir.mkdir(parents=True, exist_ok=True)
    (_data_dir / "exports").mkdir(exist_ok=True)
    _seed_mobile_data()


def _candidate_source_data_dirs() -> list[Path]:
    """Places that may already have firebase_config.json (dev tree / old install)."""
    candidates: list[Path] = []
    # Dev checkout data next to a dist\monitor\...\EXE
    exe_dir = project_root()
    for rel in (
        Path("..") / ".." / ".." / "data",  # dist/monitor/App/ -> repo/data
        Path("..") / ".." / "data",
        Path("data"),
    ):
        candidates.append((exe_dir / rel).resolve())
    # Previous install beside the EXE
    candidates.append(exe_dir / "data")
    # Explicit project path used during development
    env_src = (os.environ.get("PICKER_CHECK_SOURCE_DATA") or "").strip()
    if env_src:
        candidates.append(Path(env_src))
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _seed_desktop_data() -> None:
    """Ensure packaged desktop apps can find Firebase config and logos."""
    data = get_data_dir()
    bundled = bundled_data_dir()

    for name in _SEED_FILES:
        src = bundled / name
        dest = data / name
        if src.exists() and not dest.exists():
            shutil.copy2(src, dest)

    # Import runtime secrets/config from the project/install if this shared folder is empty.
    for source in _candidate_source_data_dirs():
        if not source.is_dir():
            continue
        for name in _RUNTIME_SEED_FILES:
            src = source / name
            dest = data / name
            if src.exists() and not dest.exists():
                try:
                    shutil.copy2(src, dest)
                except Exception:
                    pass
        # Stop early once Firebase is available.
        if (data / "firebase_config.json").exists():
            break


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

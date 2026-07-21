"""App name, version, and copyright from pyproject.toml."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def app_metadata() -> dict[str, str]:
    pyproject = _PROJECT_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    flet = data.get("tool", {}).get("flet", {})
    return {
        "name": flet.get("product") or project.get("name", "Picking Barcode Scanner"),
        "version": flet.get("build_version") or project.get("version", "1.0.0"),
        "copyright": flet.get("copyright", ""),
        "developer": "Marie Apellanes",
    }

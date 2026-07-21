"""Check GitHub for a newer Fulfilment-Checker release / build version."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from app.metadata import app_metadata
from app.paths import get_data_dir

GITHUB_OWNER = "dackzui"
GITHUB_REPO = "Fulfilment-Checker"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
TAGS_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/tags"
RAW_PYPROJECT_CANDIDATES = (
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/pyproject.toml",
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/master/pyproject.toml",
)

_VERSION_RE = re.compile(
    r"""build_version\s*=\s*["']([0-9]+(?:\.[0-9]+){0,3})["']"""
)
_TAG_RE = re.compile(r"^v?([0-9]+(?:\.[0-9]+){0,3})$", re.IGNORECASE)


@dataclass(frozen=True)
class UpdateInfo:
    latest_version: str
    current_version: str
    release_url: str
    release_notes: str = ""


def _dismissed_path() -> Path:
    return get_data_dir() / "dismissed_update.json"


def _config_path() -> Path:
    return get_data_dir() / "config.json"


def _parse_version(value: str) -> tuple[int, ...]:
    text = (value or "").strip().lstrip("vV")
    parts: list[int] = []
    for part in text.split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts) if parts else (0,)


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def get_dismissed_version() -> str:
    path = _dismissed_path()
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get("version", "") or "")
    except Exception:
        return ""


def dismiss_update(version: str) -> None:
    path = _dismissed_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": version}, indent=2),
        encoding="utf-8",
    )


def _github_token() -> str:
    env = (os.environ.get("FULFILMENT_CHECKER_GITHUB_TOKEN") or "").strip()
    if env:
        return env
    path = _config_path()
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(
            data.get("github_update_token")
            or data.get("github_token")
            or ""
        ).strip()
    except Exception:
        return ""


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Fulfilment-Checker/{app_metadata()['version']}",
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _version_from_release(payload: dict) -> str:
    tag = str(payload.get("tag_name") or "").strip()
    match = _TAG_RE.match(tag)
    if match:
        return match.group(1)
    name = str(payload.get("name") or "").strip()
    match = _TAG_RE.match(name)
    if match:
        return match.group(1)
    return ""


def _fetch_latest_release() -> UpdateInfo | None:
    response = requests.get(RELEASES_API, headers=_headers(), timeout=8)
    if response.status_code in {401, 403, 404}:
        return None
    response.raise_for_status()
    payload = response.json()
    latest = _version_from_release(payload)
    if not latest:
        return None
    current = app_metadata()["version"]
    return UpdateInfo(
        latest_version=latest,
        current_version=current,
        release_url=str(payload.get("html_url") or GITHUB_REPO_URL),
        release_notes=str(payload.get("body") or "").strip(),
    )


def _fetch_latest_tag() -> UpdateInfo | None:
    response = requests.get(
        TAGS_API, headers=_headers(), timeout=8, params={"per_page": 10}
    )
    if response.status_code in {401, 403, 404}:
        return None
    response.raise_for_status()
    tags = response.json()
    if not isinstance(tags, list):
        return None
    for item in tags:
        name = str(item.get("name") or "").strip()
        match = _TAG_RE.match(name)
        if not match:
            continue
        latest = match.group(1)
        current = app_metadata()["version"]
        return UpdateInfo(
            latest_version=latest,
            current_version=current,
            release_url=f"{GITHUB_REPO_URL}/releases/tag/{name}",
        )
    return None


def _fetch_remote_pyproject_version() -> UpdateInfo | None:
    for url in RAW_PYPROJECT_CANDIDATES:
        try:
            response = requests.get(url, headers=_headers(), timeout=8)
            if response.status_code in {401, 403, 404}:
                continue
            response.raise_for_status()
            match = _VERSION_RE.search(response.text)
            if not match:
                continue
            latest = match.group(1)
            current = app_metadata()["version"]
            return UpdateInfo(
                latest_version=latest,
                current_version=current,
                release_url=GITHUB_REPO_URL,
            )
        except Exception:
            continue
    return None


def check_for_update() -> UpdateInfo | None:
    """Return update info when GitHub has a newer version than this build."""
    candidates: list[UpdateInfo] = []

    for fetcher in (
        _fetch_latest_release,
        _fetch_latest_tag,
        _fetch_remote_pyproject_version,
    ):
        try:
            info = fetcher()
        except Exception:
            continue
        if info and is_newer(info.latest_version, info.current_version):
            candidates.append(info)

    if not candidates:
        return None

    best = max(candidates, key=lambda item: _parse_version(item.latest_version))
    if get_dismissed_version() == best.latest_version:
        return None
    return best


def check_for_update_async(on_result: Callable[[UpdateInfo | None], None]) -> None:
    def worker() -> None:
        try:
            on_result(check_for_update())
        except Exception:
            on_result(None)

    threading.Thread(target=worker, daemon=True).start()

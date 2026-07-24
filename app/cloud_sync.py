"""Upload History exports to Google Drive or OneDrive."""

from __future__ import annotations

import json
import re
import shutil
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.paths import get_data_dir
from app import cloud_oauth_defaults

PROVIDER_GOOGLE = "google"
PROVIDER_ONEDRIVE = "onedrive"
PROVIDER_FOLDER = "folder"
PROVIDER_LABELS = {
    PROVIDER_FOLDER: "Cloud folder on this device",
    PROVIDER_GOOGLE: "Google Drive",
    PROVIDER_ONEDRIVE: "OneDrive",
}

BACKUP_FULL_DB = "full_db"
BACKUP_FILTERED = "filtered"

CLOUD_FOLDER_NAME = "Picking Barcode Scanner"
HISTORY_SUBFOLDER = "History"


def sanitize_folder_name(name: str) -> str:
    """Make a safe single-folder name for Drive/OneDrive local sync."""
    cleaned = re.sub(r'[<>:"/\\|?*]+', " ", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:80] or CLOUD_FOLDER_NAME


def cloud_root_folder_name(checker_tag: str = "") -> str:
    """App root folder including login user so tablets do not share one folder."""
    tag = sanitize_folder_name(checker_tag)
    if not tag or tag.lower() == "unknown":
        return CLOUD_FOLDER_NAME
    return f"{CLOUD_FOLDER_NAME} - {tag}"


def sync_root_path(
    checker_tag: str = "",
    *,
    root_folder_name: str | None = None,
) -> Path | None:
    base = get_sync_folder()
    if base is None:
        return None
    name = sanitize_folder_name(root_folder_name or cloud_root_folder_name(checker_tag))
    return base / name


def rename_sync_root(old_name: str, new_name: str) -> Path:
    """Rename an existing cloud app folder under the chosen sync parent."""
    base = get_sync_folder()
    if base is None:
        raise FileNotFoundError("No cloud folder selected in Settings.")
    old = base / sanitize_folder_name(old_name)
    new = base / sanitize_folder_name(new_name)
    if not old.exists() or not old.is_dir():
        raise FileNotFoundError(f"Folder not found: {old.name}")
    if old.resolve() == new.resolve():
        return old
    if new.exists():
        raise FileExistsError(f"A folder named '{new.name}' already exists.")
    old.rename(new)
    return new


ProgressCallback = Callable[[str], None]


@dataclass
class SyncResult:
    provider: str
    uploaded: list[str]
    folder_url: str = ""


def credentials_path() -> Path:
    return get_data_dir() / "cloud_credentials.json"


def _config_path() -> Path:
    return get_data_dir() / "config.json"


def _load_app_config() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_app_config(config: dict[str, Any]) -> None:
    get_data_dir().mkdir(parents=True, exist_ok=True)
    _config_path().write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_sync_folder() -> Path | None:
    raw = (_load_app_config().get("cloud_sync_folder") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.exists() and path.is_dir() else None


def folder_pick_error_message(path: Path | str | None) -> str:
    """Explain why a picked folder cannot be used (common on Android)."""
    raw = str(path or "").strip()
    if not raw:
        return "No folder was selected."
    lower = raw.lower()
    if lower.startswith("content://") or "com.google.android.apps.docs" in lower:
        return (
            "Android cannot use Google Drive / cloud folders from the picker "
            "as a normal folder. On a tablet, use Sign in with Google Drive or "
            "OneDrive below (after Super Admin enables them), or on a PC pick "
            "the real OneDrive/Google Drive folder under your user profile."
        )
    if "onedrive" in lower and (":" not in raw[:3] and not raw.startswith("/storage")):
        # content-style OneDrive picks often look unusual
        pass
    folder = Path(raw)
    if not folder.exists():
        return (
            f"Folder not found: {folder}\n\n"
            "On tablets, the system picker often returns a cloud shortcut that "
            "is not a real folder. Prefer Sign in with OneDrive / Google Drive, "
            "or on a PC choose something like:\n"
            "  …\\OneDrive - Your Company\\…\n"
            "  …\\Google Drive\\My Drive\\…"
        )
    if not folder.is_dir():
        return f"Not a folder: {folder}"
    return f"Folder not found: {folder}"


def set_sync_folder(path: Path | str | None) -> Path | None:
    config = _load_app_config()
    if path is None or str(path).strip() == "":
        config.pop("cloud_sync_folder", None)
        _save_app_config(config)
        return None
    raw = str(path).strip()
    lower = raw.lower()
    if lower.startswith("content://") or "com.google.android.apps.docs" in lower:
        raise FileNotFoundError(folder_pick_error_message(raw))
    folder = Path(raw)
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(folder_pick_error_message(folder))
    config["cloud_sync_folder"] = str(folder.resolve())
    _save_app_config(config)
    return folder


def oauth_status_lines() -> list[str]:
    """Short status strings for Settings."""
    lines: list[str] = []
    if oauth_available(PROVIDER_GOOGLE):
        state = "signed in" if is_signed_in(PROVIDER_GOOGLE) else "ready — sign in"
        lines.append(f"Google Drive: {state}")
    else:
        lines.append("Google Drive: not enabled (needs client ID + secret)")
    if oauth_available(PROVIDER_ONEDRIVE):
        state = "signed in" if is_signed_in(PROVIDER_ONEDRIVE) else "ready — sign in"
        lines.append(f"OneDrive: {state}")
    else:
        lines.append("OneDrive: not enabled (needs Microsoft app client ID)")
    return lines


def _folder_is_writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".picker_check_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


async def resolve_mobile_sync_targets(page) -> list[tuple[str, Path]]:
    """Writable folders tablets can use without the Android Drive picker."""
    from flet.controls.services.storage_paths import StoragePaths
    from flet.utils.platform_utils import is_mobile

    if not is_mobile():
        return []

    storage = StoragePaths()
    # Ensure the service is attached to the active page.
    try:
        if storage not in page.services:
            page.services.append(storage)
    except Exception:
        pass

    found: list[tuple[str, Path]] = []
    seen: set[str] = set()

    async def add(label: str, raw: str | None) -> None:
        if not raw:
            return
        base = Path(str(raw))
        # Keep exports in a predictable subfolder users can open in Files / Drive.
        folder = base / CLOUD_FOLDER_NAME
        key = str(folder).lower()
        if key in seen:
            return
        if not _folder_is_writable(folder):
            return
        seen.add(key)
        found.append((label, folder))

    try:
        await add("Downloads", await storage.get_downloads_directory())
    except Exception:
        pass
    try:
        await add("App documents", await storage.get_application_documents_directory())
    except Exception:
        pass
    try:
        await add("External storage", await storage.get_external_storage_directory())
    except Exception:
        pass
    try:
        externals = await storage.get_external_storage_directories() or []
        for idx, raw in enumerate(externals):
            await add(f"Shared storage {idx + 1}", raw)
    except Exception:
        pass

    # Always-available fallback inside app data.
    from app.paths import get_data_dir

    await add("App data", str(get_data_dir() / "exports"))
    return found


def tokens_dir() -> Path:
    path = get_data_dir() / "cloud_tokens"
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_staging_dir() -> Path:
    path = get_data_dir() / "exports" / "sync"
    path.mkdir(parents=True, exist_ok=True)
    return path


def oauth_available(provider: str | None = None) -> bool:
    creds = resolve_credentials()
    google = creds.get("google") or {}
    microsoft = creds.get("microsoft") or {}
    google_ok = bool(google.get("client_id") and google.get("client_secret"))
    ms_ok = bool(microsoft.get("client_id"))
    if provider == PROVIDER_GOOGLE:
        return google_ok
    if provider == PROVIDER_ONEDRIVE:
        return ms_ok
    return google_ok or ms_ok


def credentials_configured() -> bool:
    """True when a sync folder is set (or optional OAuth is available)."""
    return get_sync_folder() is not None or oauth_available()


def resolve_credentials() -> dict[str, Any]:
    """Merge built-in defaults with optional local override file."""
    builtin = {
        "google": cloud_oauth_defaults.builtin_google(),
        "microsoft": cloud_oauth_defaults.builtin_microsoft(),
    }
    path = credentials_path()
    if not path.exists():
        return builtin
    try:
        override = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return builtin
    if not isinstance(override, dict):
        return builtin

    google = dict(builtin["google"])
    microsoft = dict(builtin["microsoft"])
    og = override.get("google") or {}
    om = override.get("microsoft") or {}
    if (og.get("client_id") or "").strip():
        google["client_id"] = og["client_id"].strip()
    if (og.get("client_secret") or "").strip():
        google["client_secret"] = og["client_secret"].strip()
    if (om.get("client_id") or "").strip():
        microsoft["client_id"] = om["client_id"].strip()
    if (om.get("tenant") or "").strip():
        microsoft["tenant"] = om["tenant"].strip()
    return {"google": google, "microsoft": microsoft}


def load_credentials() -> dict[str, Any]:
    creds = resolve_credentials()
    google = creds.get("google") or {}
    microsoft = creds.get("microsoft") or {}
    if not (
        (google.get("client_id") and google.get("client_secret"))
        or microsoft.get("client_id")
    ):
        raise FileNotFoundError(
            "Cloud sync is not set up yet. Open Settings → Cloud Sync and "
            "choose your Google Drive or OneDrive folder on this device."
        )
    return creds


def load_credentials_or_empty() -> dict[str, Any]:
    return resolve_credentials()


def save_credentials(
    *,
    google_client_id: str = "",
    google_client_secret: str = "",
    microsoft_client_id: str = "",
    microsoft_tenant: str = "consumers",
) -> Path:
    path = credentials_path()
    payload = {
        "google": {
            "client_id": (google_client_id or "").strip(),
            "client_secret": (google_client_secret or "").strip(),
        },
        "microsoft": {
            "client_id": (microsoft_client_id or "").strip(),
            "tenant": (microsoft_tenant or "consumers").strip() or "consumers",
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def is_signed_in(provider: str) -> bool:
    if provider == PROVIDER_FOLDER:
        return get_sync_folder() is not None
    token_file = tokens_dir() / f"{provider}_token.json"
    return token_file.exists() and token_file.stat().st_size > 0


def sign_out(provider: str) -> None:
    if provider == PROVIDER_FOLDER:
        set_sync_folder(None)
        return
    token_file = tokens_dir() / f"{provider}_token.json"
    if token_file.exists():
        token_file.unlink()


def sign_in(
    provider: str,
    *,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Open browser / device login for Google or OneDrive."""
    provider = (provider or "").strip().lower()
    if provider == PROVIDER_GOOGLE:
        _log(on_progress, "Opening Google sign-in…")
        _google_credentials()
        _log(on_progress, "Signed in to Google Drive.")
        return
    if provider == PROVIDER_ONEDRIVE:
        _log(on_progress, "Opening Microsoft sign-in…")
        _onedrive_token(on_progress)
        _log(on_progress, "Signed in to OneDrive.")
        return
    raise ValueError(f"Unknown provider: {provider}")


def prepare_sync_files(
    *,
    pdf_bytes: bytes,
    pdf_name: str,
    backup_mode: str,
    filtered_sessions: list[dict[str, Any]] | None = None,
    filter_summary: str = "",
    checker_tag: str = "",
) -> list[tuple[str, Path]]:
    """Write sync payload files and return (remote_name, local_path) pairs."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = (checker_tag or "unknown").strip() or "unknown"
    staging = export_staging_dir() / f"{tag}_{stamp}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    files: list[tuple[str, Path]] = []

    pdf_path = staging / pdf_name
    pdf_path.write_bytes(pdf_bytes)
    files.append((pdf_name, pdf_path))

    if backup_mode == BACKUP_FULL_DB:
        db_src = get_data_dir() / "scanner.db"
        if not db_src.exists():
            raise FileNotFoundError("scanner.db not found — nothing to back up.")
        db_name = f"scanner_backup_{tag}_{stamp}.db"
        db_dest = staging / db_name
        shutil.copy2(db_src, db_dest)
        files.append((db_name, db_dest))
    else:
        payload = {
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "filter_summary": filter_summary,
            "session_count": len(filtered_sessions or []),
            "checker": tag,
            "sessions": filtered_sessions or [],
        }
        json_name = f"sessions_filtered_{tag}_{stamp}.json"
        json_path = staging / json_name
        json_path.write_text(
            json.dumps(payload, indent=2, default=str),
            encoding="utf-8",
        )
        files.append((json_name, json_path))

    return files


def sync_files(
    provider: str,
    files: list[tuple[str, Path]],
    *,
    on_progress: ProgressCallback | None = None,
    checker_tag: str = "",
    root_folder_name: str | None = None,
) -> SyncResult:
    provider = (provider or "").strip().lower()
    if provider == PROVIDER_FOLDER:
        return _sync_folder(
            files,
            on_progress=on_progress,
            checker_tag=checker_tag,
            root_folder_name=root_folder_name,
        )
    if provider == PROVIDER_GOOGLE:
        return _sync_google(
            files,
            on_progress=on_progress,
            checker_tag=checker_tag,
            root_folder_name=root_folder_name,
        )
    if provider == PROVIDER_ONEDRIVE:
        return _sync_onedrive(
            files,
            on_progress=on_progress,
            checker_tag=checker_tag,
            root_folder_name=root_folder_name,
        )
    raise ValueError(f"Unknown provider: {provider}")


def _log(on_progress: ProgressCallback | None, message: str) -> None:
    if on_progress:
        on_progress(message)


def _sync_folder(
    files: list[tuple[str, Path]],
    *,
    on_progress: ProgressCallback | None = None,
    checker_tag: str = "",
    root_folder_name: str | None = None,
) -> SyncResult:
    folder = get_sync_folder()
    if folder is None:
        raise FileNotFoundError(
            "No cloud folder selected. Open Settings → Cloud Sync and choose "
            "your Google Drive or OneDrive folder on this device."
        )

    root_name = sanitize_folder_name(
        root_folder_name or cloud_root_folder_name(checker_tag)
    )
    batch_name = datetime.now().strftime("%Y-%m-%d_%H%M")
    dest_root = folder / root_name / HISTORY_SUBFOLDER / batch_name
    dest_root.mkdir(parents=True, exist_ok=True)

    uploaded: list[str] = []
    for remote_name, local_path in files:
        _log(on_progress, f"Copying {remote_name}…")
        shutil.copy2(local_path, dest_root / remote_name)
        uploaded.append(remote_name)

    _log(on_progress, f"Saved to {dest_root}")
    return SyncResult(
        provider=PROVIDER_FOLDER,
        uploaded=uploaded,
        folder_url=str(dest_root),
    )


GOOGLE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _google_token_path() -> Path:
    return tokens_dir() / "google_token.json"


def _google_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_cfg = resolve_credentials().get("google") or {}
    client_id = (creds_cfg.get("client_id") or "").strip()
    client_secret = (creds_cfg.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise ValueError(
            "Google Drive login is not enabled yet. Ask Super Admin to enable "
            "it in Settings → Cloud Sync."
        )

    token_path = _google_token_path()
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_config = {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, GOOGLE_SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def _google_ensure_folder(service, name: str, parent_id: str | None = None) -> str:
    query = (
        f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )
    if parent_id:
        query += f" and '{parent_id}' in parents"
    else:
        query += " and 'root' in parents"

    result = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)", pageSize=1)
        .execute()
    )
    files = result.get("files", [])
    if files:
        return files[0]["id"]

    metadata: dict[str, Any] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]
    created = service.files().create(body=metadata, fields="id").execute()
    return created["id"]


def _sync_google(
    files: list[tuple[str, Path]],
    *,
    on_progress: ProgressCallback | None = None,
    checker_tag: str = "",
    root_folder_name: str | None = None,
) -> SyncResult:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    _log(on_progress, "Signing in to Google Drive…")
    creds = _google_credentials()
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    root_name = sanitize_folder_name(
        root_folder_name or cloud_root_folder_name(checker_tag)
    )
    _log(on_progress, "Preparing Drive folders…")
    root_id = _google_ensure_folder(service, root_name)
    history_id = _google_ensure_folder(service, HISTORY_SUBFOLDER, parent_id=root_id)
    batch_name = datetime.now().strftime("%Y-%m-%d_%H%M")
    batch_id = _google_ensure_folder(service, batch_name, parent_id=history_id)

    uploaded: list[str] = []
    for remote_name, local_path in files:
        _log(on_progress, f"Uploading {remote_name}…")
        media = MediaFileUpload(str(local_path), resumable=True)
        service.files().create(
            body={"name": remote_name, "parents": [batch_id]},
            media_body=media,
            fields="id, name",
        ).execute()
        uploaded.append(remote_name)

    folder_url = f"https://drive.google.com/drive/folders/{batch_id}"
    _log(on_progress, "Google Drive sync complete.")
    return SyncResult(provider=PROVIDER_GOOGLE, uploaded=uploaded, folder_url=folder_url)


MS_SCOPES = ["Files.ReadWrite", "User.Read", "offline_access"]


def _onedrive_token_path() -> Path:
    return tokens_dir() / "onedrive_token.json"


def _msal_app():
    import msal

    ms_cfg = resolve_credentials().get("microsoft") or {}
    client_id = (ms_cfg.get("client_id") or "").strip()
    tenant = (ms_cfg.get("tenant") or "consumers").strip() or "consumers"
    if not client_id:
        raise ValueError(
            "OneDrive login is not enabled yet. Ask Super Admin to enable "
            "it in Settings → Cloud Sync."
        )

    return msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
    )


def _onedrive_token(on_progress: ProgressCallback | None = None) -> str:
    app = _msal_app()
    token_path = _onedrive_token_path()
    cache: dict[str, Any] = {}
    if token_path.exists():
        try:
            cache = json.loads(token_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache = {}

    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(MS_SCOPES, account=accounts[0])

    if not result and cache.get("refresh_token"):
        result = app.acquire_token_by_refresh_token(
            cache["refresh_token"], scopes=MS_SCOPES
        )

    if not result or "access_token" not in result:
        _log(on_progress, "Waiting for Microsoft sign-in…")
        flow = app.initiate_device_flow(scopes=MS_SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Failed to start Microsoft sign-in: {flow}")
        message = flow.get("message", "Sign in with the code shown in the browser.")
        _log(on_progress, message)
        verify_url = flow.get("verification_uri") or "https://microsoft.com/devicelogin"
        try:
            webbrowser.open(verify_url)
        except Exception:
            pass
        result = app.acquire_token_by_device_flow(flow)

    if not result or "access_token" not in result:
        error = (
            (result or {}).get("error_description")
            or (result or {}).get("error")
            or "unknown"
        )
        raise RuntimeError(f"Microsoft sign-in failed: {error}")

    token_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result["access_token"]


def _graph_request(
    method: str,
    url: str,
    token: str,
    *,
    json_body: dict | None = None,
    data: bytes | None = None,
    headers: dict | None = None,
) -> dict | None:
    import requests

    hdrs = {"Authorization": f"Bearer {token}"}
    if headers:
        hdrs.update(headers)
    response = requests.request(
        method, url, headers=hdrs, json=json_body, data=data, timeout=120
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"OneDrive API error {response.status_code}: {response.text[:400]}"
        )
    if not response.content:
        return None
    return response.json()


def _onedrive_ensure_child_folder(token: str, parent_path: str, name: str) -> str:
    encoded = parent_path.strip("/")
    list_url = (
        f"https://graph.microsoft.com/v1.0/me/drive/root:/{encoded}:/children"
        if encoded
        else "https://graph.microsoft.com/v1.0/me/drive/root/children"
    )
    listing = _graph_request("GET", list_url, token) or {}
    for item in listing.get("value", []):
        if item.get("name") == name and "folder" in item:
            return f"{parent_path.rstrip('/')}/{name}" if parent_path else f"/{name}"

    create_url = (
        f"https://graph.microsoft.com/v1.0/me/drive/root:/{encoded}:/children"
        if encoded
        else "https://graph.microsoft.com/v1.0/me/drive/root/children"
    )
    created = (
        _graph_request(
            "POST",
            create_url,
            token,
            json_body={
                "name": name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "rename",
            },
        )
        or {}
    )
    created_name = created.get("name") or name
    return (
        f"{parent_path.rstrip('/')}/{created_name}"
        if parent_path
        else f"/{created_name}"
    )


def _sync_onedrive(
    files: list[tuple[str, Path]],
    *,
    on_progress: ProgressCallback | None = None,
    checker_tag: str = "",
    root_folder_name: str | None = None,
) -> SyncResult:
    token = _onedrive_token(on_progress)

    root_name = sanitize_folder_name(
        root_folder_name or cloud_root_folder_name(checker_tag)
    )
    _log(on_progress, "Preparing OneDrive folders…")
    root = _onedrive_ensure_child_folder(token, "", root_name)
    history = _onedrive_ensure_child_folder(token, root, HISTORY_SUBFOLDER)
    batch_name = datetime.now().strftime("%Y-%m-%d_%H%M")
    batch = _onedrive_ensure_child_folder(token, history, batch_name)

    uploaded: list[str] = []
    for remote_name, local_path in files:
        _log(on_progress, f"Uploading {remote_name}…")
        remote_path = f"{batch.strip('/')}/{remote_name}"
        url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{remote_path}:/content"
        _graph_request(
            "PUT",
            url,
            token,
            data=local_path.read_bytes(),
            headers={"Content-Type": "application/octet-stream"},
        )
        uploaded.append(remote_name)

    _log(on_progress, "OneDrive sync complete.")
    return SyncResult(
        provider=PROVIDER_ONEDRIVE,
        uploaded=uploaded,
        folder_url="https://onedrive.live.com",
    )

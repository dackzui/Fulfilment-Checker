"""Admin authentication for protected actions."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.paths import get_data_dir

ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN = "admin"
ROLE_PICKER = "picker"
ROLE_MONITOR_VIEWER = "monitor_viewer"
# Legacy alias — older installs/data used "checker".
ROLE_CHECKER = ROLE_PICKER
ROLE_LABELS = {
    ROLE_SUPER_ADMIN: "Super Admin",
    ROLE_ADMIN: "Admin",
    ROLE_PICKER: "Picker",
    ROLE_MONITOR_VIEWER: "Monitor Viewer",
}

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
MIN_PASSWORD_LENGTH = 4

_last_sync_mono = 0.0
_sync_lock = threading.Lock()
_admins_file_lock = threading.Lock()
_SYNC_MIN_INTERVAL_SEC = 45.0
SESSION_FILE = "session.json"


@dataclass(frozen=True)
class AdminAccount:
    username: str
    role: str

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role.replace("_", " ").title())


def _admins_path() -> Path:
    return get_data_dir() / "admins.json"


def _session_path() -> Path:
    return get_data_dir() / SESSION_FILE


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    )
    return digest.hex()


def hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    return salt, _hash_password(password, salt)


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    candidate = _hash_password(password, salt)
    return secrets.compare_digest(candidate, password_hash)


def _normalize_username(username: str) -> str:
    return (username or "").strip()


def _username_key(username: str) -> str:
    return _normalize_username(username).lower()


def _normalize_role(role: str | None) -> str:
    value = (role or ROLE_ADMIN).strip().lower()
    if value == "checker":
        return ROLE_PICKER
    if value in ROLE_LABELS:
        return value
    return ROLE_ADMIN


def _normalize_admin_record(admin: dict) -> dict:
    record = {
        "username": _normalize_username(admin.get("username", "")),
        "role": _normalize_role(admin.get("role")),
        "salt": admin["salt"],
        "password_hash": admin["password_hash"],
    }
    updated_at = str(admin.get("updated_at") or "").strip()
    if updated_at:
        record["updated_at"] = updated_at
    return record


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_updated_at(admin: dict) -> str:
    return str(admin.get("updated_at") or "").strip()


def _validate_password(password: str, *, field_name: str = "Password") -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"{field_name} must be at least {MIN_PASSWORD_LENGTH} characters."
        )


def _validate_username(username: str) -> str:
    name = _normalize_username(username)
    if len(name) < 2:
        raise ValueError("Username must be at least 2 characters.")
    return name


def _read_admins_file() -> list[dict]:
    with _admins_file_lock:
        data = json.loads(_admins_path().read_text(encoding="utf-8"))
        return data.get("admins", [])


def ensure_admins_file() -> None:
    admins_path = _admins_path()
    admins_path.parent.mkdir(parents=True, exist_ok=True)
    with _admins_file_lock:
        if admins_path.exists():
            pass
        else:
            salt, password_hash = hash_password(DEFAULT_PASSWORD)
            admins_path.write_text(
                json.dumps(
                    {
                        "admins": [
                            {
                                "username": DEFAULT_USERNAME,
                                "role": ROLE_SUPER_ADMIN,
                                "salt": salt,
                                "password_hash": password_hash,
                                "updated_at": _now_iso(),
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
    if admins_path.exists():
        _migrate_admins_file()


def _migrate_admins_file() -> None:
    admins = _read_admins_file()
    changed = False
    migrated: list[dict] = []
    for admin in admins:
        record = dict(admin)
        raw_role = str(record.get("role") or "").strip().lower()
        if not raw_role:
            record["role"] = ROLE_SUPER_ADMIN
            changed = True
        elif raw_role == "checker":
            record["role"] = ROLE_PICKER
            record["updated_at"] = _now_iso()
            changed = True
        migrated.append(_normalize_admin_record(record))
    if changed:
        _save_admins(migrated)
        for record in migrated:
            if record.get("role") == ROLE_PICKER:
                _publish_user_cloud(record)


def _load_admins_raw() -> list[dict]:
    ensure_admins_file()
    return _read_admins_file()


def _save_admins(admins: list[dict]) -> None:
    payload = {"admins": [_normalize_admin_record(admin) for admin in admins]}
    with _admins_file_lock:
        _admins_path().write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )


def save_persisted_session(username: str, role: str) -> None:
    """Remember who is signed in so tablets keep the session across app restarts."""
    path = _session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "username": _normalize_username(username),
                "role": _normalize_role(role),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def clear_persisted_session() -> None:
    path = _session_path()
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def load_persisted_session() -> AdminAccount | None:
    """Restore a previous sign-in if that user still exists locally."""
    path = _session_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    username = _normalize_username(str(data.get("username") or ""))
    if not username:
        return None
    account = get_account(username)
    if account is None:
        clear_persisted_session()
        return None
    # Keep the file role in sync with the current account (e.g. checker → picker).
    if account.role != _normalize_role(str(data.get("role") or "")):
        save_persisted_session(account.username, account.role)
    return account


def _publish_user_cloud(record: dict) -> str | None:
    """Push one user to Firebase. Returns an error message, or None on success/skip."""
    try:
        from app import firebase_presence

        if not firebase_presence.is_configured():
            return "Firebase is not set up on this device."
        firebase_presence.publish_cloud_app_user(record)
        return None
    except Exception as exc:
        return str(exc)


def _remove_user_cloud(username: str) -> str | None:
    try:
        from app import firebase_presence

        if not firebase_presence.is_configured():
            return None
        firebase_presence.remove_cloud_app_user(username)
        return None
    except Exception as exc:
        return str(exc)


def push_all_users_to_cloud() -> tuple[int, str | None]:
    """Force-publish every local account to Firebase. Returns (count, error)."""
    try:
        from app import firebase_presence

        if not firebase_presence.is_configured():
            return 0, "Firebase is not set up on this device."
    except Exception as exc:
        return 0, str(exc)

    published = 0
    last_error: str | None = None
    for record in _load_admins():
        err = _publish_user_cloud(record)
        if err:
            last_error = err
        else:
            published += 1
    return published, last_error


def sync_with_cloud(*, force: bool = False) -> bool:
    """Pull/push shared users so all tablets share the same logins.

    Returns True when a cloud sync completed (even if nothing changed).
    Throttled to avoid blocking the UI on every screen open.
    """
    global _last_sync_mono

    with _sync_lock:
        now = time.monotonic()
        if (
            not force
            and _last_sync_mono
            and (now - _last_sync_mono) < _SYNC_MIN_INTERVAL_SEC
        ):
            return False

        ensure_admins_file()
        try:
            from app import firebase_presence

            if not firebase_presence.is_configured():
                return False
            cloud = firebase_presence.fetch_cloud_app_users()
        except Exception:
            return False

        local = _load_admins()
        by_key: dict[str, dict] = {
            _username_key(admin["username"]): dict(admin) for admin in local
        }
        cloud_by: dict[str, dict] = {
            _username_key(admin["username"]): dict(admin) for admin in cloud
        }

        for key, crec in cloud_by.items():
            if key not in by_key:
                by_key[key] = crec
                continue
            local_ts = _record_updated_at(by_key[key])
            cloud_ts = _record_updated_at(crec)
            if cloud_ts > local_ts:
                by_key[key] = crec
            # Equal or older cloud: keep local so a concurrent sync cannot
            # flap passwords/roles and look like a random logout.

        merged = [_normalize_admin_record(admin) for admin in by_key.values()]
        _save_admins(merged)

        for rec in merged:
            key = _username_key(rec["username"])
            cloud_rec = cloud_by.get(key)
            if cloud_rec is None:
                _publish_user_cloud(rec)
                continue
            raw_cloud_role = str(cloud_rec.get("role") or "").strip().lower()
            if (
                raw_cloud_role == "checker"
                or _record_updated_at(rec) > _record_updated_at(cloud_rec)
            ):
                if raw_cloud_role == "checker" and not _record_updated_at(rec):
                    rec["updated_at"] = _now_iso()
                _publish_user_cloud(rec)

        _last_sync_mono = time.monotonic()
        return True


def sync_with_cloud_background(*, force: bool = False) -> None:
    """Run user sync off the UI thread."""

    def work():
        try:
            sync_with_cloud(force=force)
        except Exception:
            pass

    threading.Thread(target=work, name="auth-cloud-sync", daemon=True).start()


def _load_admins() -> list[dict]:
    return [_normalize_admin_record(admin) for admin in _load_admins_raw()]


def _find_admin(username: str) -> dict | None:
    target = _username_key(username)
    for admin in _load_admins():
        if _username_key(admin["username"]) == target:
            return admin
    return None


def _account_from_record(admin: dict) -> AdminAccount:
    return AdminAccount(username=admin["username"], role=admin["role"])


def authenticate(username: str, password: str) -> AdminAccount | None:
    # Fast path: check local cache first (no network).
    admin = _find_admin(username)
    if admin and password and verify_password(password, admin["salt"], admin["password_hash"]):
        return _account_from_record(admin)

    # Unknown user or password mismatch — refresh from cloud, then retry once.
    try:
        sync_with_cloud(force=admin is None)
    except Exception:
        pass
    admin = _find_admin(username)
    if not admin or not password:
        return None
    if verify_password(password, admin["salt"], admin["password_hash"]):
        return _account_from_record(admin)
    return None


def get_account(username: str) -> AdminAccount | None:
    admin = _find_admin(username)
    if not admin:
        return None
    return _account_from_record(admin)


def is_super_admin(username: str | None) -> bool:
    account = get_account(username or "")
    return account is not None and account.role == ROLE_SUPER_ADMIN


def is_picker(username: str | None) -> bool:
    account = get_account(username or "")
    return account is not None and account.role == ROLE_PICKER


def is_checker(username: str | None) -> bool:
    """Deprecated alias for is_picker()."""
    return is_picker(username)


def can_manage_picker_names(role: str | None) -> bool:
    return role in (ROLE_PICKER, ROLE_ADMIN, ROLE_SUPER_ADMIN)


def list_picker_usernames(*, sync: bool = False) -> list[str]:
    """Usernames with the Picker role — used for the New Scan picker dropdown."""
    if sync:
        try:
            sync_with_cloud()
        except Exception:
            pass
    from app.components import capitalize_person_name

    names = [
        capitalize_person_name(admin["username"])
        for admin in _load_admins()
        if admin.get("role") == ROLE_PICKER
    ]
    return sorted({n for n in names if n}, key=str.casefold)


def match_picker_username(value: str) -> str | None:
    """Return the canonical picker username if value matches a Picker user."""
    from app.components import capitalize_person_name

    target = capitalize_person_name(value or "").strip()
    if not target:
        return None
    for name in list_picker_usernames():
        if name.casefold() == target.casefold():
            return name
    return None


def can_access_monitor(role: str | None) -> bool:
    """Super Admin (full) or Monitor Viewer (read-only board)."""
    return role in (ROLE_SUPER_ADMIN, ROLE_MONITOR_VIEWER)


def can_manage_monitor_settings(role: str | None) -> bool:
    return role == ROLE_SUPER_ADMIN


def list_admin_accounts(actor_username: str) -> list[AdminAccount]:
    if not is_super_admin(actor_username):
        raise PermissionError("Only Super Admin users can manage accounts.")
    return [_account_from_record(admin) for admin in _load_admins()]


def _set_password(username: str, new_password: str) -> None:
    admin = _find_admin(username)
    if not admin:
        raise ValueError("Account not found.")
    _validate_password(new_password, field_name="New password")

    salt, password_hash = hash_password(new_password)
    admins = _load_admins()
    updated: dict | None = None
    for record in admins:
        if _username_key(record["username"]) == _username_key(username):
            record["salt"] = salt
            record["password_hash"] = password_hash
            record["updated_at"] = _now_iso()
            updated = record
            break
    _save_admins(admins)
    if updated:
        _publish_user_cloud(updated)


def set_user_password(
    actor_username: str,
    target_username: str,
    new_password: str,
) -> None:
    if not is_super_admin(actor_username):
        raise PermissionError("Only Super Admin can set passwords.")
    if not _find_admin(target_username):
        raise ValueError("Account not found.")
    _set_password(target_username, new_password)


def set_user_role(
    actor_username: str,
    target_username: str,
    role: str,
) -> AdminAccount:
    if not is_super_admin(actor_username):
        raise PermissionError("Only Super Admin can change user roles.")
    target = _find_admin(target_username)
    if not target:
        raise ValueError("Account not found.")

    new_role = _normalize_role(role)
    if new_role == ROLE_SUPER_ADMIN:
        raise ValueError("Cannot assign Super Admin role from the app.")

    admins = _load_admins()
    updated: dict | None = None
    for record in admins:
        if _username_key(record["username"]) == _username_key(target_username):
            if record["role"] == ROLE_SUPER_ADMIN and new_role != ROLE_SUPER_ADMIN:
                super_admins = [
                    admin
                    for admin in admins
                    if admin["role"] == ROLE_SUPER_ADMIN
                    and _username_key(admin["username"]) != _username_key(target_username)
                ]
                if not super_admins:
                    raise ValueError("At least one Super Admin account must remain.")
            record["role"] = new_role
            record["updated_at"] = _now_iso()
            updated = record
            break
    _save_admins(admins)
    if updated:
        _publish_user_cloud(updated)
    return AdminAccount(username=target["username"], role=new_role)


def create_user(
    actor_username: str,
    username: str,
    password: str,
    *,
    role: str = ROLE_ADMIN,
) -> AdminAccount:
    if not is_super_admin(actor_username):
        raise PermissionError("Only Super Admin can add users.")

    name = _validate_username(username)
    _validate_password(password)
    if _find_admin(name):
        raise ValueError("That username is already in use.")

    new_role = _normalize_role(role)
    if new_role == ROLE_SUPER_ADMIN:
        raise ValueError("Cannot create Super Admin accounts from the app.")

    salt, password_hash = hash_password(password)
    record = {
        "username": name,
        "role": new_role,
        "salt": salt,
        "password_hash": password_hash,
        "updated_at": _now_iso(),
    }
    admins = _load_admins()
    admins.append(record)
    _save_admins(admins)
    cloud_error = _publish_user_cloud(record)
    account = AdminAccount(username=name, role=new_role)
    if cloud_error:
        # Local save succeeded; surface cloud failure to the caller.
        raise RuntimeError(
            f"User '{name}' was saved on this PC, but Firebase sync failed: {cloud_error}"
        )
    return account


def delete_admin(actor_username: str, target_username: str) -> None:
    if not is_super_admin(actor_username):
        raise PermissionError("Only Super Admin can delete users.")

    actor_key = _username_key(actor_username)
    target_key = _username_key(target_username)
    if actor_key == target_key:
        raise ValueError("You cannot delete your own account while signed in.")

    admins = _load_admins()
    target = next(
        (admin for admin in admins if _username_key(admin["username"]) == target_key),
        None,
    )
    if not target:
        raise ValueError("Account not found.")

    remaining = [admin for admin in admins if _username_key(admin["username"]) != target_key]
    super_admins = [
        admin for admin in remaining if admin["role"] == ROLE_SUPER_ADMIN
    ]
    if target["role"] == ROLE_SUPER_ADMIN and not super_admins:
        raise ValueError("At least one Super Admin account must remain.")
    _save_admins(remaining)
    _remove_user_cloud(target["username"])


def admin_usernames() -> list[str]:
    return [admin["username"] for admin in _load_admins()]

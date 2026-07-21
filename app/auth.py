"""Admin authentication for protected actions."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from app.paths import get_data_dir

ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN = "admin"
ROLE_CHECKER = "checker"
ROLE_LABELS = {
    ROLE_SUPER_ADMIN: "Super Admin",
    ROLE_ADMIN: "Admin",
    ROLE_CHECKER: "Checker",
}

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
MIN_PASSWORD_LENGTH = 4


@dataclass(frozen=True)
class AdminAccount:
    username: str
    role: str

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role.replace("_", " ").title())


def _admins_path() -> Path:
    return get_data_dir() / "admins.json"


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
    if value in ROLE_LABELS:
        return value
    return ROLE_ADMIN


def _normalize_admin_record(admin: dict) -> dict:
    return {
        "username": _normalize_username(admin.get("username", "")),
        "role": _normalize_role(admin.get("role")),
        "salt": admin["salt"],
        "password_hash": admin["password_hash"],
    }


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
    data = json.loads(_admins_path().read_text(encoding="utf-8"))
    return data.get("admins", [])


def ensure_admins_file() -> None:
    admins_path = _admins_path()
    admins_path.parent.mkdir(parents=True, exist_ok=True)
    if admins_path.exists():
        _migrate_admins_file()
        return

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
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _migrate_admins_file() -> None:
    admins = _read_admins_file()
    changed = False
    migrated: list[dict] = []
    for admin in admins:
        record = dict(admin)
        if not record.get("role"):
            record["role"] = ROLE_SUPER_ADMIN
            changed = True
        migrated.append(_normalize_admin_record(record))
    if changed:
        _save_admins(migrated)


def _load_admins_raw() -> list[dict]:
    ensure_admins_file()
    return _read_admins_file()


def _save_admins(admins: list[dict]) -> None:
    payload = {"admins": [_normalize_admin_record(admin) for admin in admins]}
    _admins_path().write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


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


def is_checker(username: str | None) -> bool:
    account = get_account(username or "")
    return account is not None and account.role == ROLE_CHECKER


def can_manage_picker_names(role: str | None) -> bool:
    return role in (ROLE_CHECKER, ROLE_ADMIN, ROLE_SUPER_ADMIN)


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
    for record in admins:
        if _username_key(record["username"]) == _username_key(username):
            record["salt"] = salt
            record["password_hash"] = password_hash
            break
    _save_admins(admins)


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
            break
    _save_admins(admins)
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
    admins = _load_admins()
    admins.append(
        {
            "username": name,
            "role": new_role,
            "salt": salt,
            "password_hash": password_hash,
        }
    )
    _save_admins(admins)
    return AdminAccount(username=name, role=new_role)


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


def admin_usernames() -> list[str]:
    return [admin["username"] for admin in _load_admins()]

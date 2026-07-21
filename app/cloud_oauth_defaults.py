"""Built-in OAuth app IDs for simple Sign in with Google / OneDrive.

End users do not enter API keys. DEKS IT creates the OAuth apps once and
fills these values (or sets environment variables). Users only click Sign In.

Environment overrides (optional):
  PICKER_GOOGLE_CLIENT_ID
  PICKER_GOOGLE_CLIENT_SECRET
  PICKER_MS_CLIENT_ID
  PICKER_MS_TENANT
"""

from __future__ import annotations

import os

# Fill these once after registering apps in Google Cloud / Azure Portal
# (or Super Admin can enter them once in Settings → Cloud Sync).
GOOGLE_CLIENT_ID = ""
GOOGLE_CLIENT_SECRET = ""

# Azure public client (mobile/desktop) — device code login, no secret needed.
MICROSOFT_CLIENT_ID = ""
MICROSOFT_TENANT = "consumers"


def builtin_google() -> dict[str, str]:
    return {
        "client_id": (
            os.environ.get("PICKER_GOOGLE_CLIENT_ID", "").strip() or GOOGLE_CLIENT_ID
        ).strip(),
        "client_secret": (
            os.environ.get("PICKER_GOOGLE_CLIENT_SECRET", "").strip()
            or GOOGLE_CLIENT_SECRET
        ).strip(),
    }


def builtin_microsoft() -> dict[str, str]:
    return {
        "client_id": (
            os.environ.get("PICKER_MS_CLIENT_ID", "").strip() or MICROSOFT_CLIENT_ID
        ).strip(),
        "tenant": (
            os.environ.get("PICKER_MS_TENANT", "").strip() or MICROSOFT_TENANT
        ).strip()
        or "consumers",
    }

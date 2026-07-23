"""Inject Android package-visibility queries so UrlLauncher can open https links."""

from __future__ import annotations

import sys
import time
from pathlib import Path

QUERIES_BLOCK = """
    <!-- Allow opening https/http links in an external browser (Android 11+) -->
    <queries>
        <intent>
            <action android:name="android.intent.action.VIEW" />
            <category android:name="android.intent.category.BROWSABLE" />
            <data android:scheme="https" />
        </intent>
        <intent>
            <action android:name="android.intent.action.VIEW" />
            <category android:name="android.intent.category.BROWSABLE" />
            <data android:scheme="http" />
        </intent>
        <intent>
            <action android:name="android.support.customtabs.action.CustomTabsService" />
        </intent>
    </queries>
""".rstrip()

MARKER = "android.support.customtabs.action.CustomTabsService"


def patch_manifest(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text and "<queries>" in text:
        return False
    if "</manifest>" not in text:
        return False
    patched = text.replace("</manifest>", f"{QUERIES_BLOCK}\n</manifest>", 1)
    path.write_text(patched, encoding="utf-8")
    return True


def find_manifest(root: Path) -> Path | None:
    candidates = [
        root / "build" / "flutter" / "android" / "app" / "src" / "main" / "AndroidManifest.xml",
        root / "build" / "site" / "flutter" / "android" / "app" / "src" / "main" / "AndroidManifest.xml",
    ]
    for path in candidates:
        if path.exists():
            return path
    matches = list(root.glob("build/**/android/app/src/main/AndroidManifest.xml"))
    return matches[0] if matches else None


def watch_and_patch(root: Path, timeout_sec: float = 600.0) -> int:
    deadline = time.time() + timeout_sec
    patched_once = False
    while time.time() < deadline:
        manifest = find_manifest(root)
        if manifest is not None:
            changed = patch_manifest(manifest)
            if changed:
                print(f"Patched URL queries into {manifest}")
                patched_once = True
            elif not patched_once:
                print(f"Manifest already has URL queries: {manifest}")
                patched_once = True
            # Keep ensuring patch sticks if flet rewrites the file.
            time.sleep(2.0)
            continue
        time.sleep(1.0)
    return 0 if patched_once else 1


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
    raise SystemExit(watch_and_patch(project_root, timeout_sec=timeout))

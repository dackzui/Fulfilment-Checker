"""Shared SQLite helpers — one lock for scanner.db across modules."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

# All writers/readers of scanner.db must share this lock (Android is strict).
DB_LOCK = threading.RLock()


def connect(db_path: Path, *, timeout: float = 60.0) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        timeout=timeout,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    # busy_timeout is in milliseconds — wait instead of failing immediately.
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def retry_locked(operation, *, attempts: int = 8, base_delay: float = 0.05):
    """Run ``operation`` under DB_LOCK, retrying SQLite lock errors."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with DB_LOCK:
                return operation()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "locked" not in message and "busy" not in message:
                raise
            last_error = exc
            time.sleep(base_delay * (2**attempt))
    assert last_error is not None
    raise last_error

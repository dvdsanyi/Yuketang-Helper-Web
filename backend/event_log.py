"""Per-account append-only event log stored as JSON Lines.

Path: store/events/{account_id}.jsonl
Keeps the most recent MAX_EVENTS entries per account; older ones are trimmed.
"""

import json
import threading
from datetime import datetime
from pathlib import Path

from config import STORE_DIR

MAX_EVENTS = 5000
_lock = threading.Lock()
_LOG_DIR = STORE_DIR / "events"
_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _path(account_id: str) -> Path:
    return _LOG_DIR / f"{account_id}.jsonl"


def append(account_id: str, event: dict) -> None:
    record = {**event, "logged_at": datetime.now().isoformat(timespec="seconds")}
    p = _path(account_id)
    with _lock:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        _trim(p)


def load_recent(account_id: str, n: int = 50) -> list:
    p = _path(account_id)
    if not p.exists():
        return []
    with _lock:
        lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [json.loads(l) for l in lines[-n:]]


def clear(account_id: str) -> None:
    p = _path(account_id)
    with _lock:
        if p.exists():
            p.unlink()


def _trim(p: Path) -> None:
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(lines) > MAX_EVENTS:
        p.write_text("\n".join(lines[-MAX_EVENTS:]) + "\n", encoding="utf-8")

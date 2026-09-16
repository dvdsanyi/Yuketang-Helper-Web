"""Per-account append-only event log stored as JSON Lines.

Path: store/events/{account_id}.jsonl
Keeps the most recent MAX_EVENTS entries per account; older ones are trimmed.
"""

import json
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

from config import STORE_DIR

MAX_EVENTS = 5000
# Trim only every Nth append to amortise the O(N) rewrite cost.
_TRIM_EVERY = 100

_LOG_DIR = STORE_DIR / "events"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Per-account lock so different accounts don't contend on each other.
# Lock-object creation is guarded by `_registry_lock` to guarantee one Lock
# per account_id. Reads/writes of `_append_counts` are guarded by the matching
# per-account lock (`_lock_for(account_id)`) — we use a plain dict instead of
# defaultdict so insertion happens under that lock too.
_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()
_append_counts: dict[str, int] = {}


def _lock_for(account_id: str) -> threading.Lock:
    lock = _locks.get(account_id)
    if lock is not None:
        return lock
    with _registry_lock:
        return _locks.setdefault(account_id, threading.Lock())


def _path(account_id: str) -> Path:
    return _LOG_DIR / f"{account_id}.jsonl"


def append(account_id: str, event: dict) -> None:
    # Place `logged_at` first so a caller-provided value in `event` wins.
    record = {"logged_at": datetime.now().isoformat(timespec="seconds"), **event}
    p = _path(account_id)
    with _lock_for(account_id):
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        count = _append_counts.get(account_id, 0) + 1
        if count >= _TRIM_EVERY:
            _append_counts[account_id] = 0
            _trim(p)
        else:
            _append_counts[account_id] = count


def load_recent(account_id: str, n: int = 50) -> list:
    p = _path(account_id)
    if not p.exists():
        return []
    with _lock_for(account_id):
        with open(p, "r", encoding="utf-8") as f:
            tail = deque(f, maxlen=n)
    return [json.loads(line) for line in tail if line.strip()]


def clear(account_id: str) -> None:
    p = _path(account_id)
    with _lock_for(account_id):
        p.unlink(missing_ok=True)
        _append_counts.pop(account_id, None)
    with _registry_lock:
        _locks.pop(account_id, None)


def _trim(p: Path) -> None:
    with open(p, "r", encoding="utf-8") as f:
        tail = deque(f, maxlen=MAX_EVENTS)
    if len(tail) < MAX_EVENTS:
        return
    p.write_text("".join(tail), encoding="utf-8")

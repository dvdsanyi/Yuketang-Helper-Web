"""Process-wide shared state (event queue, subscribers, monitor registry,
pending-account slots). Kept in its own module so routers and the main app
can import from one place without circular dependencies."""

import asyncio
import threading
from typing import Optional

from fastapi import HTTPException

from config import get_account
from monitor import Monitor


# Single asyncio queue all monitors push into; the broadcaster fans events
# out to the per-account WebSocket subscribers below.
event_queue: asyncio.Queue = asyncio.Queue()

# { account_id: set[asyncio.Queue] }
subscribers: dict[str, set[asyncio.Queue]] = {}
subscribers_lock = threading.Lock()

# Registry of running monitors, one per account.
monitors: dict[str, Monitor] = {}
monitors_lock = threading.Lock()

# In-memory pending account slots — never persisted; lost on restart.
pending: dict[str, dict] = {}
pending_lock = threading.Lock()


def get_monitor(account_id: str) -> Optional[Monitor]:
    with monitors_lock:
        return monitors.get(account_id)


def set_monitor(account_id: str, m: Optional[Monitor]) -> None:
    with monitors_lock:
        if m is None:
            monitors.pop(account_id, None)
        else:
            monitors[account_id] = m


def kick_subscribers(account_id: str) -> None:
    """Push a sentinel into every subscriber queue for this account so the
    WebSocket handler exits its `await q.get()` and closes cleanly."""
    with subscribers_lock:
        queues = list(subscribers.pop(account_id, ()))
    for q in queues:
        try:
            q.put_nowait({"type": "account_closed"})
        except asyncio.QueueFull:
            pass


def require_account(account_id: str) -> dict:
    """Return the account dict or raise 404. Helper for route handlers."""
    acc = get_account(account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="account not found")
    return acc

"""Process-wide shared state (event queue, subscribers, monitor registry,
pending-account slots). Kept in its own module so routers and the main app
can import from one place without circular dependencies."""

import asyncio
import contextlib
import threading

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

# Logins in progress: { pending_id: domain }. Never persisted; lost on restart.
pending: dict[str, str] = {}
pending_lock = threading.Lock()


def get_monitor(account_id: str) -> Monitor | None:
    with monitors_lock:
        return monitors.get(account_id)


def set_monitor(account_id: str, m: Monitor | None) -> None:
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
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait({"type": "account_closed"})

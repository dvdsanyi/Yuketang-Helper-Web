import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import event_log
import state
from config import account_exists

router = APIRouter()


@router.websocket("/ws/accounts/{account_id}/events")
async def ws_events(ws: WebSocket, account_id: str):
    await ws.accept()
    if not account_exists(account_id):
        await ws.send_json({"type": "error", "message": "account not found"})
        await ws.close()
        return

    client_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    with state.subscribers_lock:
        state.subscribers.setdefault(account_id, set()).add(client_queue)

    try:
        history = event_log.load_recent(account_id, 50)
        if history:
            await ws.send_json({"type": "history", "events": history})

        while True:
            # A quiet account still gets a frame every 30s: sending is the only
            # way to notice the browser has gone and release this subscription.
            try:
                event = await asyncio.wait_for(client_queue.get(), timeout=30)
            except TimeoutError:
                event = {"type": "heartbeat"}
            await ws.send_json(event)
            if event["type"] == "account_closed":
                break
    except (WebSocketDisconnect, RuntimeError, ConnectionError):
        pass
    finally:
        with state.subscribers_lock:
            bucket = state.subscribers.get(account_id)
            if bucket:
                bucket.discard(client_queue)
                if not bucket:
                    state.subscribers.pop(account_id, None)

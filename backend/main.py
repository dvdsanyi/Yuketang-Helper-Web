import asyncio
import logging
import mimetypes
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import pushdeer
import state
from auth import refresh_local_cache, start_monitor
from config import STORE_DIR, list_accounts_summary
from routers import accounts, ai, courses, domains, events, login, pushdeer_router

# ---------------------------------------------------------------------------
# Logging — shared console + file handlers attached to root + uvicorn loggers
# so the timestamp format stays consistent across access/error/app logs.
# Timestamps are local time, matching event_log.append() and PushDeer messages.
# The file lives under STORE_DIR so it survives restarts and Docker updates.
# ---------------------------------------------------------------------------


_LOG_DIR = STORE_DIR / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_HANDLERS: list[logging.Handler] = [
    logging.StreamHandler(),
    RotatingFileHandler(_LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
]
for _handler in _LOG_HANDLERS:
    _handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
logging.basicConfig(level=logging.INFO, handlers=_LOG_HANDLERS, force=True)
for _name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    _uv = logging.getLogger(_name)
    _uv.handlers = list(_LOG_HANDLERS)
    _uv.propagate = False


# ---------------------------------------------------------------------------
# Background tasks — event broadcasting + daily PushDeer heartbeat
# ---------------------------------------------------------------------------


async def _broadcast_events():
    log = logging.getLogger("broadcast")
    while True:
        try:
            event = await state.event_queue.get()
            aid = event.get("account_id")
            dead: list[asyncio.Queue] = []
            with state.subscribers_lock:
                queues = list(state.subscribers.get(aid, ()))
            for q in queues:
                if q.full():
                    dead.append(q)
                else:
                    q.put_nowait(event)
            if dead:
                with state.subscribers_lock:
                    bucket = state.subscribers.get(aid)
                    if bucket:
                        for q in dead:
                            bucket.discard(q)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("event broadcast failed")


_HEARTBEAT_TZ = ZoneInfo("Asia/Shanghai")
_HEARTBEAT_HOUR = 7


def _seconds_until_next_heartbeat() -> float:
    now = datetime.now(_HEARTBEAT_TZ)
    target = now.replace(hour=_HEARTBEAT_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _pushdeer_heartbeat_scheduler():
    log = logging.getLogger("heartbeat")
    while True:
        try:
            wait_s = _seconds_until_next_heartbeat()
            log.info(f"next PushDeer heartbeat in {wait_s:.0f}s")
            await asyncio.sleep(wait_s)
            for summary in list_accounts_summary():
                if not summary["logged_in"]:
                    continue
                try:
                    await asyncio.to_thread(pushdeer.send_liveness, summary["id"])
                except Exception as e:
                    log.warning(f"heartbeat for {summary['id']} failed: {e}")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("heartbeat scheduler error")
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    broadcaster = asyncio.create_task(_broadcast_events())
    heartbeat_task = asyncio.create_task(_pushdeer_heartbeat_scheduler())

    startup_log = logging.getLogger("startup")
    for summary in list_accounts_summary():
        aid = summary["id"]
        if not summary["logged_in"]:
            continue
        try:
            refresh_local_cache(aid)
        except Exception as e:
            startup_log.warning(f"refresh cache failed for {aid}: {e}")
        start_monitor(aid)

    yield

    broadcaster.cancel()
    heartbeat_task.cancel()

    with state.monitors_lock:
        ms = list(state.monitors.values())
        state.monitors.clear()
    for m in ms:
        m.stop()


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


app = FastAPI(title="Yuketang Helper API", lifespan=lifespan)
app.include_router(domains.router)
app.include_router(accounts.router)
app.include_router(login.router)
app.include_router(courses.router)
app.include_router(ai.router)
app.include_router(pushdeer_router.router)
app.include_router(events.router)


# ---------------------------------------------------------------------------
# Static file serving (production)
# ---------------------------------------------------------------------------


# Windows resolves MIME types through the registry, where other software can
# leave `.js` mapped to text/plain. Browsers enforce the MIME type of
# `type="module"` scripts strictly, so the bundle silently never executes and
# the page renders blank. Pin the two types the SPA needs.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


def _get_static_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "static"
    return Path(__file__).resolve().parent / "static"


_STATIC_DIR = _get_static_dir()

if _STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    _STATIC_ROOT = _STATIC_DIR.resolve()

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Unknown API/WS paths 404 as JSON instead of falling through to the
        # SPA shell — a fetch().json() against a typo'd endpoint would
        # otherwise receive index.html and fail to parse.
        if full_path.split("/", 1)[0] in ("api", "ws"):
            raise HTTPException(status_code=404, detail="not found")
        # Resolve, then confirm the target stays inside the static root so a
        # crafted `..` path can't escape and serve arbitrary files off disk.
        candidate = (_STATIC_ROOT / full_path).resolve()
        if candidate.is_file() and _STATIC_ROOT in candidate.parents:
            return FileResponse(candidate)
        # Anything with a file extension is a static asset, not an SPA route:
        # 404 it rather than returning index.html, which turns a missing file
        # into a baffling parse error instead of an obvious one.
        if "." in candidate.name:
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(_STATIC_ROOT / "index.html")

import asyncio
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

import requests
import websocket
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import event_log
from config import (
    get_config, save_config, get_course_config, update_course_config,
    get_ai_config, update_ai_config,
    DEFAULT_COURSE_CONFIG,
)
from monitor import Monitor
from domains import TSINGHUA_DOMAIN
from utils import get_user_info, get_all_courses

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

event_queue: asyncio.Queue = asyncio.Queue()
_subscribers: set[asyncio.Queue] = set()
monitor: Optional[Monitor] = None


def get_monitor() -> Optional[Monitor]:
    return monitor


def set_monitor(m: Optional[Monitor]) -> None:
    global monitor
    monitor = m


# ---------------------------------------------------------------------------
# Startup cache helper
# ---------------------------------------------------------------------------


def _refresh_local_cache(sessionid: str) -> None:
    """Fetch user info and course list from Yuketang, cache locally, and
    ensure every course has default settings in config.json."""
    cfg = get_config()

    try:
        cfg["user"] = get_user_info(sessionid)
        logger.info("Cached user info for %s", cfg["user"].get("name", "?"))
    except Exception as e:
        logger.warning("Failed to cache user info: %s", e)

    try:
        raw_courses = get_all_courses(sessionid)
        course_list = [
            {
                "classroom_id": str(c.get("classroom_id", "")),
                "name": (c.get("course") or {}).get("name", c.get("name", "")),
                "classroom_name": c.get("name", ""),
                "teacher_name": (c.get("teacher") or {}).get("name"),
            }
            for c in raw_courses
        ]
        cfg["course_list"] = course_list

        # Ensure every course has default settings
        courses = cfg.setdefault("courses", {})
        for c in course_list:
            cid = c["classroom_id"]
            if not cid:
                continue
            if cid not in courses:
                courses[cid] = {"name": c["name"], **DEFAULT_COURSE_CONFIG}
            elif courses[cid].get("name") != c["name"]:
                courses[cid]["name"] = c["name"]

        logger.info("Cached %d courses and initialised default settings", len(course_list))
    except Exception as e:
        logger.warning("Failed to cache courses: %s", e)

    save_config(cfg)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


async def _broadcast_events():
    """Read from the single event_queue and fan out to all subscriber queues."""
    while True:
        event = await event_queue.get()
        dead: list[asyncio.Queue] = []
        for q in _subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            _subscribers.discard(q)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_event_loop()

    broadcaster = asyncio.create_task(_broadcast_events())

    cfg = get_config()
    if cfg.get("sessionid"):
        _refresh_local_cache(cfg["sessionid"])
        m = Monitor(sessionid=cfg["sessionid"], event_queue=event_queue)
        set_monitor(m)
        m.start(loop)
        logger.info("Monitor auto-started on startup")

    yield

    broadcaster.cancel()

    m = get_monitor()
    if m:
        m.stop()
        logger.info("Monitor stopped on shutdown")


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Yuketang Helper API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class NotificationSub(BaseModel):
    enabled: bool = True
    signin: bool = True
    problem: bool = True
    call: bool = True
    danmu: bool = False


class CourseConfig(BaseModel):
    type1: str = "random"   # single choice: "random" | "ai" | "off"
    type2: str = "random"   # multiple choice: "random" | "ai" | "off"
    type3: str = "random"   # vote: "random" | "off"
    type4: str = "off"      # fill-in-blank: reserved
    type5: str = "off"      # short answer: "ai" | "off"
    answer_delay_min: int = 3
    answer_delay_max: int = 10
    auto_danmu: bool = True
    danmu_threshold: int = 3
    notification: NotificationSub = NotificationSub()
    voice_notification: NotificationSub = NotificationSub(enabled=False)


class AIKeyEntry(BaseModel):
    name: str
    provider: str = "gemini"
    key: str

class AIActiveKey(BaseModel):
    active_key: int


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------


@app.get("/api/auth/status")
async def auth_status():
    cfg = get_config()
    if not cfg.get("sessionid"):
        return {"logged_in": False, "user": None}
    return {"logged_in": True, "user": cfg.get("user", {})}


@app.post("/api/auth/logout")
async def auth_logout():
    cfg = get_config()
    cfg["sessionid"] = ""
    cfg["user"] = {}
    cfg["course_list"] = []
    save_config(cfg)

    m = get_monitor()
    if m:
        m.stop()
        set_monitor(None)

    return {"ok": True}


# ---------------------------------------------------------------------------
# Login WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws/login")
async def ws_login(ws: WebSocket):
    await ws.accept()
    loop = asyncio.get_event_loop()
    login_queue: asyncio.Queue = asyncio.Queue()

    # --- WebSocket callbacks run in a background thread ---

    def on_open(wsapp):
        data = {
            "op": "requestlogin",
            "role": "web",
            "version": 1.4,
            "type": "qrcode",
            "from": "web",
        }
        wsapp.send(json.dumps(data))

    def on_message(wsapp, message):
        data = json.loads(message)
        op = data.get("op", "")

        if op == "requestlogin":
            ticket = data.get("ticket", "")
            import base64
            resp = requests.get(
                url=ticket,
                proxies={"http": None, "https": None},
                timeout=10,
            )
            img_b64 = base64.b64encode(resp.content).decode()
            content_type = resp.headers.get("Content-Type", "image/png").split(";")[0]
            data_url = "data:%s;base64,%s" % (content_type, img_b64)
            asyncio.run_coroutine_threadsafe(
                login_queue.put({"type": "qr", "url": data_url}), loop
            )

        elif op == "loginsuccess":
            web_login_url = "https://%s/pc/web_login" % TSINGHUA_DOMAIN
            login_data = json.dumps(
                {"UserID": data["UserID"], "Auth": data["Auth"]}
            )
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:104.0) "
                    "Gecko/20100101 Firefox/104.0"
                )
            }
            r = requests.post(
                url=web_login_url,
                data=login_data,
                headers=headers,
                proxies={"http": None, "https": None},
                timeout=10,
            )
            sessionid = dict(r.cookies).get("sessionid", "")
            if not sessionid:
                raise ValueError("sessionid not found in login response cookies")

            cfg = get_config()
            cfg["sessionid"] = sessionid
            save_config(cfg)

            _refresh_local_cache(sessionid)
            user = get_config().get("user", {})

            asyncio.run_coroutine_threadsafe(
                login_queue.put(
                    {"type": "success", "sessionid": sessionid, "user": user}
                ),
                loop,
            )
            wsapp.close()

    def on_error(wsapp, error):
        logger.error("Login WS error: %s", error)
        asyncio.run_coroutine_threadsafe(
            login_queue.put({"type": "error", "message": str(error)}), loop
        )

    def on_close(wsapp, *args):
        pass

    def qr_refresh_loop(wsapp_ref):
        """Re-request login every 55 seconds to keep QR fresh."""
        count = 0
        while getattr(wsapp_ref, "_keep_running", True):
            if count >= 55:
                count = 0
                wsapp_ref.send(
                    json.dumps(
                        {
                            "op": "requestlogin",
                            "role": "web",
                            "version": 1.4,
                            "type": "qrcode",
                            "from": "web",
                        }
                    )
                )
            else:
                time.sleep(1)
                count += 1

    wsapp = websocket.WebSocketApp(
        url="wss://%s/wsapp/" % TSINGHUA_DOMAIN,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    ws_thread = threading.Thread(
        target=wsapp.run_forever, daemon=True, name="login-ws"
    )
    ws_thread.start()

    refresh_thread = threading.Thread(
        target=qr_refresh_loop, args=(wsapp,), daemon=True, name="login-ws-refresh"
    )
    refresh_thread.start()

    try:
        while True:
            msg = await login_queue.get()
            await ws.send_json(msg)

            if msg["type"] == "success":
                new_sid = msg["sessionid"]
                m = get_monitor()
                if m:
                    m.stop()
                m = Monitor(sessionid=new_sid, event_queue=event_queue)
                set_monitor(m)
                m.start(loop)
                break

            if msg["type"] == "error":
                break
    except WebSocketDisconnect:
        logger.info("Login WebSocket client disconnected")
    finally:
        wsapp._keep_running = False
        wsapp.close()


# ---------------------------------------------------------------------------
# Course routes
# ---------------------------------------------------------------------------


@app.get("/api/courses/active")
async def get_active_courses():
    m = get_monitor()
    if not m:
        return {"lessons": []}
    return {"lessons": m.get_active_lessons()}


@app.get("/api/courses/all")
async def get_all_courses_endpoint():
    cfg = get_config()
    if not cfg.get("sessionid"):
        return []
    cached = cfg.get("course_list", [])
    m = get_monitor()
    # classroomid (str) -> lessonid
    active_map: dict = {}
    if m:
        for lesson in m.get_active_lessons():
            active_map[str(lesson["classroomid"])] = lesson["lessonid"]
    result = []
    for c in cached:
        cid = str(c.get("classroom_id", ""))
        result.append({
            "classroom_id": cid,
            "name": c.get("name", ""),
            "classroom_name": c.get("classroom_name", ""),
            "teacher_name": c.get("teacher_name"),
            "active": cid in active_map,
        })
    return result


@app.get("/api/courses/settings")
async def get_all_course_settings():
    cfg = get_config()
    result = {}
    for course_id in cfg.get("courses", {}):
        result[course_id] = get_course_config(course_id)
    return result


@app.get("/api/courses/settings/{course_id}")
async def get_course_settings(course_id: str):
    return get_course_config(course_id)


@app.put("/api/courses/settings/{course_id}")
async def update_course_settings(course_id: str, body: CourseConfig):
    data = body.model_dump()
    update_course_config(course_id, data)

    # Update the running lesson's config if it's active
    m = get_monitor()
    if m:
        with m._lock:
            lesson = next(
                (l for l in m._active_lessons.values() if str(l.classroomid) == course_id),
                None,
            )
        if lesson:
            lesson.course_config.update(data)

    return {"ok": True, "course_id": course_id, "config": data}


# ---------------------------------------------------------------------------
# AI settings routes
# ---------------------------------------------------------------------------


@app.get("/api/ai/settings")
async def get_ai_settings():
    cfg = get_ai_config()
    # Mask keys for security
    masked_keys = []
    for entry in cfg.get("keys", []):
        raw = entry.get("key", "")
        masked = raw[:4] + "****" + raw[-4:] if len(raw) > 8 else "****" if raw else ""
        masked_keys.append({**entry, "key": masked})
    return {"keys": masked_keys, "active_key": cfg.get("active_key", -1)}


@app.post("/api/ai/keys")
async def add_ai_key(body: AIKeyEntry):
    cfg = get_ai_config()
    keys = cfg.get("keys", [])
    keys.append(body.model_dump())
    # Auto-select if it's the first key
    active = cfg.get("active_key", -1)
    if active < 0:
        active = 0
    update_ai_config({"keys": keys, "active_key": active})
    return {"ok": True, "index": len(keys) - 1}


@app.delete("/api/ai/keys/{index}")
async def delete_ai_key(index: int):
    cfg = get_ai_config()
    keys = cfg.get("keys", [])
    if index < 0 or index >= len(keys):
        return {"ok": False, "message": "Invalid index"}
    keys.pop(index)
    active = cfg.get("active_key", -1)
    if active >= len(keys):
        active = len(keys) - 1
    elif active > index:
        active -= 1
    elif active == index:
        active = 0 if keys else -1
    update_ai_config({"keys": keys, "active_key": active})
    return {"ok": True}


@app.put("/api/ai/active")
async def set_active_ai_key(body: AIActiveKey):
    cfg = get_ai_config()
    keys = cfg.get("keys", [])
    idx = body.active_key
    if idx < -1 or idx >= len(keys):
        return {"ok": False, "message": "Invalid index"}
    update_ai_config({"active_key": idx})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Events WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    logger.info("Events WebSocket client connected")

    # Send persisted history so the dashboard is populated immediately
    history = event_log.load_recent(50)
    if history:
        await ws.send_json({"type": "history", "events": history})

    # Each client gets its own queue fed by the broadcaster
    client_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(client_queue)

    async def heartbeat():
        try:
            while True:
                await asyncio.sleep(30)
                await ws.send_json({"type": "heartbeat"})
        except (WebSocketDisconnect, RuntimeError):
            pass

    hb_task = asyncio.create_task(heartbeat())
    try:
        while True:
            event = await client_queue.get()
            await ws.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        logger.info("Events WebSocket client disconnected")
    finally:
        hb_task.cancel()
        _subscribers.discard(client_queue)

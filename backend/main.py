import asyncio
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

import requests
import websocket
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import event_log
from config import (
    get_config, save_config, get_course_config, update_course_config,
    get_ai_config, update_ai_config,
    get_domain, set_domain,
    DEFAULT_COURSE_CONFIG, DOMAIN_OPTIONS,
)
from monitor import Monitor
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
    cfg = get_config()

    cfg["user"] = get_user_info(sessionid)

    raw_courses = get_all_courses(sessionid)
    course_list = [
        {
            "classroom_id": str(c["classroom_id"]),
            "name": c["course"]["name"],
            "classroom_name": c["name"],
            "teacher_name": c["teacher"]["name"],
        }
        for c in raw_courses
    ]
    cfg["course_list"] = course_list

    courses = cfg.setdefault("courses", {})
    for c in course_list:
        cid = c["classroom_id"]
        if cid not in courses:
            courses[cid] = {"name": c["name"], **DEFAULT_COURSE_CONFIG}
        elif courses[cid].get("name") != c["name"]:
            courses[cid]["name"] = c["name"]

    save_config(cfg)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


async def _broadcast_events():
    while True:
        event = await event_queue.get()
        dead: list[asyncio.Queue] = []
        for q in _subscribers:
            if q.full():
                dead.append(q)
            else:
                q.put_nowait(event)
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

    yield

    broadcaster.cancel()

    m = get_monitor()
    if m:
        m.stop()


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
    enabled: bool
    signin: bool
    problem: bool
    call: bool
    danmu: bool


class CourseConfig(BaseModel):
    type1: str
    type2: str
    type3: str
    type4: str
    type5: str
    answer_delay_min: int
    answer_delay_max: int
    auto_danmu: bool
    danmu_threshold: int
    notification: NotificationSub
    voice_notification: NotificationSub


class AIKeyEntry(BaseModel):
    name: str
    provider: str
    key: str

class AIActiveKey(BaseModel):
    active_key: int


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------


@app.get("/api/domain")
async def get_domain_setting():
    return {"domain": get_domain(), "options": DOMAIN_OPTIONS}


@app.put("/api/domain")
async def update_domain_setting(body: dict):
    domain = body.get("domain", "")
    valid_keys = {opt["key"] for opt in DOMAIN_OPTIONS}
    if domain not in valid_keys:
        return {"ok": False, "error": "Invalid domain"}
    set_domain(domain)
    return {"ok": True, "domain": domain}


@app.get("/api/auth/status")
async def auth_status():
    cfg = get_config()
    if not cfg.get("sessionid"):
        return {"logged_in": False, "user": None}
    return {"logged_in": True, "user": cfg["user"]}


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

    def on_open(wsapp):
        wsapp.send(json.dumps({
            "op": "requestlogin",
            "role": "web",
            "version": 1.4,
            "type": "qrcode",
            "from": "web",
        }))

    def on_message(wsapp, message):
        data = json.loads(message)
        op = data["op"]

        if op == "requestlogin":
            import base64
            resp = requests.get(
                url=data["ticket"],
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
            r = requests.post(
                url="https://%s/pc/web_login" % get_domain(),
                data=json.dumps({"UserID": data["UserID"], "Auth": data["Auth"]}),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:104.0) "
                        "Gecko/20100101 Firefox/104.0"
                    )
                },
                proxies={"http": None, "https": None},
                timeout=10,
            )
            sessionid = dict(r.cookies)["sessionid"]

            cfg = get_config()
            cfg["sessionid"] = sessionid
            save_config(cfg)

            _refresh_local_cache(sessionid)
            user = get_config()["user"]

            asyncio.run_coroutine_threadsafe(
                login_queue.put({"type": "success", "sessionid": sessionid, "user": user}),
                loop,
            )
            wsapp.close()

    def on_error(wsapp, error):
        asyncio.run_coroutine_threadsafe(
            login_queue.put({"type": "error", "message": str(error)}), loop
        )

    def on_close(wsapp, *args):
        pass

    def qr_refresh_loop(wsapp_ref):
        count = 0
        while getattr(wsapp_ref, "_keep_running", True):
            if count >= 55:
                count = 0
                wsapp_ref.send(json.dumps({
                    "op": "requestlogin",
                    "role": "web",
                    "version": 1.4,
                    "type": "qrcode",
                    "from": "web",
                }))
            else:
                time.sleep(1)
                count += 1

    wsapp = websocket.WebSocketApp(
        url="wss://%s/wsapp/" % get_domain(),
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    threading.Thread(target=wsapp.run_forever, daemon=True, name="login-ws").start()
    threading.Thread(target=qr_refresh_loop, args=(wsapp,), daemon=True, name="login-ws-refresh").start()

    while True:
        msg = await login_queue.get()
        await ws.send_json(msg)

        if msg["type"] in ("success", "error"):
            break

    if msg["type"] == "success":
        m = get_monitor()
        if m:
            m.stop()
        m = Monitor(sessionid=msg["sessionid"], event_queue=event_queue)
        set_monitor(m)
        m.start(loop)

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
    active_map: dict = {}
    if m:
        for lesson in m.get_active_lessons():
            active_map[str(lesson["classroomid"])] = lesson["lessonid"]
    return [
        {
            "classroom_id": c["classroom_id"],
            "name": c["name"],
            "classroom_name": c["classroom_name"],
            "teacher_name": c["teacher_name"],
            "active": c["classroom_id"] in active_map,
        }
        for c in cached
    ]


@app.get("/api/courses/settings")
async def get_all_course_settings():
    cfg = get_config()
    return {cid: get_course_config(cid) for cid in cfg.get("courses", {})}


@app.get("/api/courses/settings/{course_id}")
async def get_course_settings(course_id: str):
    return get_course_config(course_id)


@app.put("/api/courses/settings/{course_id}")
async def update_course_settings(course_id: str, body: CourseConfig):
    data = body.model_dump()
    update_course_config(course_id, data)

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
    masked_keys = []
    for entry in cfg["keys"]:
        raw = entry["key"]
        masked = raw[:4] + "****" + raw[-4:] if len(raw) > 8 else "****"
        masked_keys.append({**entry, "key": masked})
    return {"keys": masked_keys, "active_key": cfg["active_key"]}


@app.post("/api/ai/keys")
async def add_ai_key(body: AIKeyEntry):
    cfg = get_ai_config()
    keys = cfg["keys"]
    keys.append(body.model_dump())
    active = cfg["active_key"]
    if active < 0:
        active = 0
    update_ai_config({"keys": keys, "active_key": active})
    return {"ok": True, "index": len(keys) - 1}


@app.delete("/api/ai/keys/{index}")
async def delete_ai_key(index: int):
    cfg = get_ai_config()
    keys = cfg["keys"]
    keys.pop(index)
    active = cfg["active_key"]
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
    update_ai_config({"active_key": body.active_key})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Events WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()

    history = event_log.load_recent(50)
    if history:
        await ws.send_json({"type": "history", "events": history})

    client_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(client_queue)

    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            await ws.send_json({"type": "heartbeat"})

    hb_task = asyncio.create_task(heartbeat())

    while True:
        event = await client_queue.get()
        await ws.send_json(event)

"""Login bookkeeping shared by /api/accounts/{id}/auth/password-login and
/ws/accounts/{id}/login.

Both flows boil down to "given a sessionid, persist a real account keyed by
user.id, refresh its cached state, and kick off a monitor". This module
isolates that plumbing so the route handlers stay small."""

import asyncio
import logging
from typing import Optional

import state
from config import (
    DEFAULT_DOMAIN,
    CourseModel,
    account_exists,
    api_url,
    config_lock,
    delete_account,
    get_account,
    get_config,
    http_request,
    make_headers,
    new_empty_account,
    save_config,
    set_active_account_id,
    update_account,
    upsert_account,
)
from monitor import Monitor

logger = logging.getLogger(__name__)

URL_USER_INFO = "https://{domain}/api/v3/user/basic-info"
URL_COURSE_LIST = "https://{domain}/v2/api/web/courses/list?identity=2"


def handle_session_expired(account_id: str) -> None:
    """Called by Monitor when its poll detects an expired sessionid. Only
    touches existing accounts (no silent materialization)."""
    if account_exists(account_id):
        update_account(account_id, {"sessionid": "", "user": {}, "course_list": []})
    state.set_monitor(account_id, None)


def start_monitor(account_id: str) -> None:
    loop = asyncio.get_running_loop()
    existing = state.get_monitor(account_id)
    if existing:
        existing.stop()
    m = Monitor(account_id=account_id, event_queue=state.event_queue, on_session_expired=handle_session_expired)
    state.set_monitor(account_id, m)
    m.start(loop)


def stop_monitor(account_id: str) -> None:
    m = state.get_monitor(account_id)
    if m:
        m.stop()
        state.set_monitor(account_id, None)


def refresh_local_cache(account_id: str, user: Optional[dict] = None) -> None:
    """Populate user info + course_list for `account_id`. If `user` is supplied
    it's used as-is; otherwise fetched. Individual HTTP failures are logged
    but do not raise so partial-success state (sessionid saved, courses missing)
    is still written."""
    acc = get_account(account_id) or {}
    domain = acc.get("domain") or DEFAULT_DOMAIN
    sessionid = acc.get("sessionid", "")
    if not sessionid:
        return
    headers = make_headers(domain, sessionid)

    if user is None:
        try:
            user = http_request("GET", api_url(domain, URL_USER_INFO), headers=headers).json()["data"]
        except Exception as e:
            logger.warning(f"[{account_id}] user info fetch failed: {e}")
            user = {}

    try:
        raw_courses = http_request("GET", api_url(domain, URL_COURSE_LIST), headers=headers).json()["data"]["list"]
    except Exception as e:
        logger.warning(f"[{account_id}] course list fetch failed: {e}")
        raw_courses = None  # leave existing courses alone

    course_list = None
    if raw_courses is not None:
        course_list = [
            {
                "classroom_id": str(c["classroom_id"]),
                "name": c["course"]["name"],
                "classroom_name": c["name"],
                "teacher_name": c["teacher"]["name"],
            }
            for c in raw_courses
        ]

    with config_lock:  # one atomic transaction for the cache update
        cfg = get_config()
        if account_id not in cfg["accounts"]:
            cfg["accounts"][account_id] = new_empty_account(domain)
        acc = cfg["accounts"][account_id]
        if user:
            acc["user"] = user
            acc["name"] = user.get("name") or acc.get("name", "")
        if course_list is not None:
            acc["course_list"] = course_list
            courses = acc.setdefault("courses", {})
            for c in course_list:
                cid = c["classroom_id"]
                if cid not in courses:
                    courses[cid] = CourseModel(name=c["name"]).model_dump()
                elif courses[cid].get("name") != c["name"]:
                    courses[cid]["name"] = c["name"]
        save_config(cfg)


def finalize_login(pending_id: str, sessionid: str) -> str:
    """Resolve the pending domain (in-memory or persistent), look up user.id,
    persist a real account keyed by user.id, then refresh cache + start monitor."""
    with state.pending_lock:
        pending = state.pending.pop(pending_id, None)
    if pending:
        domain = pending.get("domain") or DEFAULT_DOMAIN
    else:
        existing = get_account(pending_id) or {}
        domain = existing.get("domain") or DEFAULT_DOMAIN

    headers = make_headers(domain, sessionid)
    user = http_request("GET", api_url(domain, URL_USER_INFO), headers=headers).json()["data"]
    raw_id = user.get("id")
    user_id = str(raw_id) if raw_id else pending_id

    if account_exists(user_id):
        update_account(user_id, {"sessionid": sessionid, "domain": domain})
    else:
        acc = new_empty_account(domain)
        acc["sessionid"] = sessionid
        upsert_account(user_id, acc)

    if pending_id != user_id and account_exists(pending_id):
        delete_account(pending_id)

    try:
        refresh_local_cache(user_id, user=user)
    except Exception as e:
        logger.warning(f"[{user_id}] refresh after login failed: {e}")
    set_active_account_id(user_id)
    start_monitor(user_id)
    return user_id


def resolve_domain(account_id: str) -> Optional[str]:
    """Return the effective domain for an account_id, looking first at the
    in-memory pending table and falling back to the persisted account."""
    with state.pending_lock:
        p = state.pending.get(account_id)
    if p:
        return p.get("domain") or DEFAULT_DOMAIN
    acc = get_account(account_id)
    return acc["domain"] if acc else None

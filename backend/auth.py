"""Login bookkeeping shared by /api/accounts/{id}/auth/password-login and
/ws/accounts/{id}/login.

Both flows boil down to "given a sessionid, persist a real account keyed by
user.id, refresh its cached state, and kick off a monitor". This module
isolates that plumbing so the route handlers stay small."""

import asyncio
import logging

import state
from config import (
    account_exists,
    api_url,
    config_lock,
    get_account,
    get_config,
    http_request,
    make_headers,
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
    """Called by Monitor when its poll detects an expired sessionid."""
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


def refresh_local_cache(account_id: str, user: dict | None = None) -> None:
    """Populate user info + course_list for `account_id`. If `user` is supplied
    it's used as-is; otherwise fetched. A failed fetch is logged and leaves
    that half of the cache as it was."""
    acc = get_account(account_id)
    if not acc or not acc["sessionid"]:
        return
    domain = acc["domain"]
    headers = make_headers(domain, acc["sessionid"])

    if user is None:
        try:
            user = http_request("GET", api_url(domain, URL_USER_INFO), headers=headers).json()["data"]
        except Exception as e:
            logger.warning(f"[{account_id}] user info fetch failed: {e}")

    course_list = None
    try:
        raw_courses = http_request("GET", api_url(domain, URL_COURSE_LIST), headers=headers).json()["data"]["list"]
        course_list = [
            {
                "classroom_id": str(c["classroom_id"]),
                "name": c["course"]["name"],
                "classroom_name": c["name"],
                "teacher_name": (c.get("teacher") or {}).get("name"),
            }
            for c in raw_courses
        ]
    except Exception as e:
        logger.warning(f"[{account_id}] course list fetch failed: {e}")

    with config_lock:  # one atomic transaction for the cache update
        cfg = get_config()
        acc = cfg["accounts"].get(account_id)
        if acc is None:
            return  # deleted while we were fetching
        if user:
            acc["user"] = user
            acc["name"] = user.get("name") or acc["name"]
        if course_list is not None:
            acc["course_list"] = course_list
        save_config(cfg)


def pending_domain(pending_id: str) -> str | None:
    """Domain picked for a login in progress, or None if the slot is unknown."""
    with state.pending_lock:
        return state.pending.get(pending_id)


def _persist_login(pending_id: str, domain: str, sessionid: str) -> str:
    """Look up the user behind `sessionid`, persist the account keyed by
    user.id and refresh its cache. Returns the account id."""
    headers = make_headers(domain, sessionid)
    user = http_request("GET", api_url(domain, URL_USER_INFO), headers=headers).json()["data"]
    user_id = str(user["id"])

    if account_exists(user_id):
        update_account(user_id, {"sessionid": sessionid, "domain": domain})
    else:
        upsert_account(user_id, {"sessionid": sessionid, "domain": domain})
    with state.pending_lock:
        state.pending.pop(pending_id, None)

    try:
        refresh_local_cache(user_id, user=user)
    except Exception as e:
        logger.warning(f"[{user_id}] refresh after login failed: {e}")
    set_active_account_id(user_id)
    return user_id


async def finalize_login(pending_id: str, domain: str, sessionid: str) -> str:
    """Turn a fresh sessionid into a monitored account. The Yuketang
    round-trips run on a worker thread, so the server keeps answering every
    other page and event stream while they are in flight."""
    user_id = await asyncio.to_thread(_persist_login, pending_id, domain, sessionid)
    start_monitor(user_id)
    return user_id

import json
import logging
import sys
import threading
from functools import lru_cache
from pathlib import Path

from config import (
    PushdeerLanguage, account_display_name,
    get_account, get_course_config, get_pushdeer_config, get_sessionid, http_request,
)

logger = logging.getLogger(__name__)

# Event type -> per-course config subkey (matches Dashboard.tsx NOTIF_SUBKEY).
# Also the set of event types that trigger a push at all.
_EVENT_SUBKEY = {
    "signin": "signin",
    "problem": "problem",
    "problem_received": "problem",
    "call": "call",
    "danmu": "danmu",
    "red_packet": "red_packet",
}


# ---------------------------------------------------------------------------
# i18n strings — loaded from the same JSON files the frontend uses, so we have
# a single source of truth.
# ---------------------------------------------------------------------------


class _Strings(dict):
    """Dict that returns the key itself for missing entries, so a missing
    translation degrades to the raw event-type key instead of a KeyError."""

    def __missing__(self, key: str) -> str:
        return key


@lru_cache(maxsize=2)
def _load_events(language: PushdeerLanguage) -> _Strings:
    """The `events` strings of frontend/src/locales/<language>.json, wherever
    this runtime mode keeps them."""
    here = Path(__file__).resolve().parent
    for locales in (
        here / "locales",                                # Docker: copied during build
        here.parent / "frontend" / "src" / "locales",    # Python source
        Path(getattr(sys, "_MEIPASS", "")) / "locales",  # PyInstaller: bundled by the spec
    ):
        path = locales / f"{language}.json"
        if path.is_file():
            return _Strings(json.loads(path.read_text(encoding="utf-8"))["events"])
    logger.warning(f"Could not locate locales/{language}.json; pushes will show raw keys")
    return _Strings()


# ---------------------------------------------------------------------------
# Message formatting — mirrors Dashboard.tsx formatEventLabel exactly
# ---------------------------------------------------------------------------


def _answers_text(answers) -> str:
    if answers is None:
        return ""
    if isinstance(answers, list):
        return ", ".join(str(a) for a in answers)
    return str(answers)


def _format_badge(event_type: str, data: dict, s: _Strings) -> str:
    """Mirror Dashboard badge text (same logic as the JSX that picks
    problemType{n} for 'problem' events)."""
    if event_type == "problem" and data.get("problemtype"):
        return s[f"problemType{data['problemtype']}"]
    return s[event_type]


def _format_detail(event_type: str, data: dict, s: _Strings) -> str:
    status = data.get("status") or "success"
    if event_type in ("signin", "red_packet"):
        return s[status]
    if event_type == "problem":
        if status == "ai_failed":
            return s["ai_failed"]
        if status == "skipped":
            return s[f"skip_{data.get('message')}"]
        answer_text = _answers_text(data.get("answers"))
        source = data.get("source")
        source_text = f" [{s[f'source_{source}']}]" if source else ""
        answer_suffix = f", {s['answer']}: {answer_text}" if answer_text else ""
        return f"{s[status]}{answer_suffix}{source_text}"
    if event_type == "danmu":
        return f'"{data.get("content") or ""}" — {s[status]}'
    return ""  # problem_received, call: the badge says it all


def _format_label(event_type: str, data: dict, s: _Strings) -> str:
    """Port of Dashboard.tsx formatEventLabel: lesson plus details. The event
    type itself is the badge (the push title), so it isn't repeated here."""
    detail = _format_detail(event_type, data, s)
    lesson = data.get("lesson") or ""
    if not lesson:
        return detail
    return f"{lesson}: {detail}" if detail else lesson


def format_event(event_type: str, data: dict, language: PushdeerLanguage) -> tuple[str, str]:
    """Return (title, body) for a PushDeer push of one of the `_EVENT_SUBKEY`
    event types. Matches the Dashboard event row: title = badge text, body =
    formatEventLabel output plus the failure reason shown under the row."""
    s = _load_events(language)
    body = _format_label(event_type, data, s)
    reason = data.get("message") if data.get("status") in ("error", "ai_failed") else None
    if reason:
        body += f"\n{s['reason']}: {reason}"
    return _format_badge(event_type, data, s), body


# ---------------------------------------------------------------------------
# HTTP send
# ---------------------------------------------------------------------------


def _send(endpoint: str, push_key: str, title: str, body: str) -> tuple[bool, str]:
    url = endpoint.rstrip("/") + "/message/push"
    payload = {
        "pushkey": push_key,
        "text": title,
        "desp": body,
        "type": "text",
    }
    try:
        # PushDeer expects form-encoded body.
        r = http_request("POST", url, attempts=2, timeout=10, data=payload)
        result = r.json()
    except Exception as e:
        logger.warning(f"PushDeer send failed: {e}")
        return False, str(e)

    if result.get("code") == 0:
        return True, "ok"
    err = result.get("error") or result.get("content") or str(result)
    logger.warning(f"PushDeer API error: {err}")
    return False, str(err)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _active_entry(cfg: dict) -> dict | None:
    keys = cfg["keys"]
    idx = cfg["active_key"]
    if idx < 0 or idx >= len(keys):
        return None
    return keys[idx]


def _account_name(account_id: str) -> str:
    return account_display_name(get_account(account_id) or {}, account_id)


def _credentials(entry: dict | None) -> tuple[str, str] | None:
    """(endpoint, push_key) of a key entry, or None if it can't be used."""
    if entry is None:
        return None
    endpoint = entry.get("endpoint", "").strip()
    push_key = entry.get("push_key", "").strip()
    return (endpoint, push_key) if endpoint and push_key else None


def _title(language: PushdeerLanguage, label: str, account_id: str) -> str:
    brand = "Yuketang Helper" if language == "en" else "雨课堂助手"
    return f"{brand}-{label}-{_account_name(account_id)}"


def _send_async(endpoint: str, push_key: str, title: str, body: str) -> None:
    threading.Thread(target=_send, args=(endpoint, push_key, title, body), daemon=True, name="pushdeer-send").start()


def dispatch(account_id: str, classroom_id: str, event_type: str, data: dict) -> None:
    """Fire-and-forget push for one event. Safe to call from any thread."""
    subkey = _EVENT_SUBKEY.get(event_type)
    if subkey is None:
        return

    pd_cfg = get_pushdeer_config(account_id)
    creds = _credentials(_active_entry(pd_cfg))
    if creds is None:
        return

    course_cfg = get_course_config(account_id, classroom_id)
    notif_cfg = course_cfg.get("pushdeer_notification", {})
    if not notif_cfg.get("enabled"):
        return
    if not notif_cfg.get(subkey, True):
        return

    language = pd_cfg["language"]
    label, body = format_event(event_type, data, language)
    _send_async(*creds, _title(language, label, account_id), body)


def send_session_expired(account_id: str) -> None:
    """Account-level push, not gated by any course toggle: an expired session
    silently stops answering in every later lesson, so it must reach the user."""
    pd_cfg = get_pushdeer_config(account_id)
    creds = _credentials(_active_entry(pd_cfg))
    if creds is None:
        return
    language = pd_cfg["language"]
    s = _load_events(language)
    _send_async(*creds, _title(language, s["session_expired"], account_id), s["session_expired_hint"])


def send_liveness(account_id: str, entry: dict | None = None) -> tuple[bool, str]:
    """Send a liveness notification via a PushDeer key.

    If ``entry`` is None, the account's active key is used (daily heartbeat).
    Pass an explicit entry to target a specific row without mutating the
    persisted active_key (used by the per-row Test button).
    """
    pd_cfg = get_pushdeer_config(account_id)
    if entry is None:
        entry = _active_entry(pd_cfg)
        if entry is None:
            return False, "no active pushdeer key"
    creds = _credentials(entry)
    if creds is None:
        return False, "endpoint and push_key required"
    if not get_sessionid(account_id):
        return False, "session expired"
    language = pd_cfg["language"]
    if language == "en":
        label = "Heartbeat"
        body = "If you see this message, PushDeer and Yuketang Helper are running normally and this account's session has not expired."
    else:
        label = "心跳"
        body = "当你看到这条消息，说明 PushDeer 和雨课堂助手正常运行且该账号 session 未过期。"
    return _send(*creds, _title(language, label, account_id), body)

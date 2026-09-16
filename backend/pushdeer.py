import json
import logging
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Optional

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
# a single source of truth. If locale files can't be found, missing keys
# degrade to the raw key via _Strings.__missing__, except for these few
# strings that are interpolated into prose and would read badly raw.
# ---------------------------------------------------------------------------


_FALLBACK_EVENTS_ZH = {
    "success": "成功",
    "error": "失败",
    "ai_failed": "AI 答题失败",
    "answer": "答案",
}
_FALLBACK_EVENTS_EN = {
    "success": "success",
    "error": "error",
    "ai_failed": "AI answering failed",
    "answer": "answer(s)",
}


class _Strings(dict):
    """Dict that returns the key itself for missing entries, so a missing
    translation degrades to the raw event-type key instead of a KeyError."""

    def __missing__(self, key: str) -> str:
        return key


def _locales_dir() -> Optional[Path]:
    """Locate frontend/src/locales/*.json across runtime modes (searched in order):
      - Docker: ``backend/locales`` (copied during build)
      - Python source: ``../frontend/src/locales``
      - PyInstaller frozen: ``<_MEIPASS>/locales`` (the spec file bundles them there)
    """
    here = Path(__file__).resolve().parent
    for candidate in (
        here / "locales",                                # docker bundle
        here.parent / "frontend" / "src" / "locales",    # source mode
        Path(getattr(sys, "_MEIPASS", "")) / "locales",  # frozen bundle
    ):
        if candidate.is_dir() and (candidate / "zh.json").exists():
            return candidate
    return None


@lru_cache(maxsize=2)
def _load_events(language: PushdeerLanguage) -> _Strings:
    fallback = _FALLBACK_EVENTS_EN if language == "en" else _FALLBACK_EVENTS_ZH
    locales = _locales_dir()
    if not locales:
        logger.warning("Could not locate frontend locales; using fallback i18n")
        return _Strings(fallback)
    try:
        data = json.loads((locales / f"{language}.json").read_text(encoding="utf-8"))
        return _Strings({**fallback, **data.get("events", {})})
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to load locales/{language}.json: {e}")
        return _Strings(fallback)


# ---------------------------------------------------------------------------
# Message formatting — mirrors Dashboard.tsx formatEventLabel exactly
# ---------------------------------------------------------------------------


def _answers_text(answers) -> str:
    if answers is None:
        return ""
    if isinstance(answers, list):
        return ", ".join(str(a) for a in answers)
    if isinstance(answers, dict):
        return json.dumps(answers, ensure_ascii=False)
    return str(answers)


def _format_badge(event_type: str, data: dict, s: _Strings) -> str:
    """Mirror Dashboard badge text (same logic as the JSX that picks
    problemType{n} for 'problem' events)."""
    if event_type == "problem" and data.get("problemtype"):
        return s[f"problemType{data['problemtype']}"]
    return s[event_type]


def _format_label(event_type: str, data: dict, s: _Strings) -> str:
    """Port of Dashboard.tsx formatEventLabel."""
    type_name = s[event_type]
    lesson = f"[{data['lesson']}] " if data.get("lesson") else ""
    status = data.get("status") or "success"

    if event_type == "signin":
        return f"{lesson}{type_name}: {s[status]}"

    if event_type == "problem_received":
        return f"{lesson}{type_name}"

    if event_type == "problem":
        ptype = data.get("problemtype")
        problem_type_name = s[f"problemType{ptype}"] if ptype else type_name
        if status == "ai_failed":
            return f"{lesson}{problem_type_name}: {s['ai_failed']}"
        status_text = s[status]
        answer_text = _answers_text(data.get("answers"))
        source = data.get("source")
        source_text = f" [{s[f'source_{source}']}]" if source else ""
        answer_suffix = f", {s['answer']}: {answer_text}" if answer_text else ""
        return f"{lesson}{problem_type_name}: {status_text}{answer_suffix}{source_text}"

    if event_type == "danmu":
        content = data.get("content") or ""
        return f'{lesson}{type_name}: "{content}" — {s[status]}'

    if event_type == "call":
        return f"{lesson}{type_name}"

    if event_type == "red_packet":
        return f"{lesson}{type_name}: {s[status]}"

    if event_type == "session_expired":
        return type_name

    if event_type in ("lesson_end", "lesson_start"):
        return f"{lesson}{type_name}"

    message = data.get("message") or ""
    return f"{lesson}{type_name}{': ' + message if message else ''}"


def format_event(event_type: str, data: dict, language: PushdeerLanguage = "zh") -> Optional[tuple[str, str]]:
    """Return (title, body) for a PushDeer push. Matches Dashboard event row:
    title = badge text, body = formatEventLabel output."""
    if event_type not in _EVENT_SUBKEY:
        return None
    s = _load_events(language)
    return _format_badge(event_type, data, s), _format_label(event_type, data, s)


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


def _active_entry(cfg: dict) -> Optional[dict]:
    keys = cfg["keys"]
    idx = cfg["active_key"]
    if idx < 0 or idx >= len(keys):
        return None
    return keys[idx]


def _account_name(account_id: str) -> str:
    return account_display_name(get_account(account_id) or {}, account_id)


def dispatch(account_id: str, classroom_id: str, event_type: str, data: dict) -> None:
    """Fire-and-forget push for one event. Safe to call from any thread."""
    subkey = _EVENT_SUBKEY.get(event_type)
    if subkey is None:
        return

    pd_cfg = get_pushdeer_config(account_id)
    entry = _active_entry(pd_cfg)
    if entry is None:
        return
    endpoint = entry.get("endpoint", "").strip()
    push_key = entry.get("push_key", "").strip()
    if not endpoint or not push_key:
        return

    course_cfg = get_course_config(account_id, classroom_id)
    notif_cfg = course_cfg.get("pushdeer_notification", {})
    if not notif_cfg.get("enabled"):
        return
    if not notif_cfg.get(subkey, True):
        return

    language = pd_cfg["language"]
    formatted = format_event(event_type, data, language)
    if formatted is None:
        return
    title, body = formatted
    brand = "Yuketang Helper" if language == "en" else "雨课堂助手"
    title = f"{brand}-{title}-{_account_name(account_id)}"

    def _worker():
        _send(endpoint, push_key, title, body)

    threading.Thread(target=_worker, daemon=True, name="pushdeer-send").start()


def send_liveness(account_id: str, entry: Optional[dict] = None) -> tuple[bool, str]:
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
    endpoint = entry.get("endpoint", "").strip()
    push_key = entry.get("push_key", "").strip()
    if not endpoint or not push_key:
        return False, "endpoint and push_key required"
    if not get_sessionid(account_id):
        return False, "session expired"
    language = pd_cfg["language"]
    name = _account_name(account_id)
    if language == "en":
        title = f"Yuketang Helper-Heartbeat-{name}"
        body = "If you see this message, PushDeer and Yuketang Helper are running normally and this account's session has not expired."
    else:
        title = f"雨课堂助手-心跳-{name}"
        body = "当你看到这条消息，说明 PushDeer 和雨课堂助手正常运行且该账号 session 未过期。"
    return _send(endpoint, push_key, title, body)

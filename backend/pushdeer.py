import json
import logging
import threading
from typing import Optional, Tuple

from config import get_account, get_course_config, get_pushdeer_config, get_sessionid, http_request

logger = logging.getLogger(__name__)

# Event type -> per-course config subkey (matches Dashboard.tsx VOICE_SUBOPTION)
_EVENT_SUBKEY = {
    "signin": "signin",
    "problem": "problem",
    "problem_received": "problem",
    "call": "call",
    "danmu": "danmu",
    "red_packet": "red_packet",
}


# ---------------------------------------------------------------------------
# i18n strings (copied verbatim from frontend/src/locales/*.json "events")
# ---------------------------------------------------------------------------

_EVENTS_ZH = {
    "signin": "签到",
    "problem": "答题",
    "problem_received": "收到题目",
    "danmu": "弹幕",
    "call": "点名",
    "lesson_end": "下课",
    "lesson_start": "上课",
    "session_expired": "登录过期",
    "red_packet": "红包",
    "problemType1": "单选题",
    "problemType2": "多选题",
    "problemType3": "投票题",
    "problemType4": "填空题",
    "problemType5": "简答题",
    "success": "成功",
    "error": "失败",
    "source_ai": "AI",
    "source_random": "随机",
    "source_blank": "空答案",
    "answer": "答案",
    "ai_failed": "AI答题失败，请手动作答！将按「最后5秒作答」设置提交随机答案（单选/多选）或空答（简答题）作为兜底",
}

_EVENTS_EN = {
    "signin": "Sign-in",
    "problem": "Problem",
    "problem_received": "Problem Received",
    "danmu": "Danmu",
    "call": "Roll Call",
    "lesson_end": "Class Ended",
    "lesson_start": "Class Started",
    "session_expired": "Session Expired",
    "red_packet": "Red Packet",
    "problemType1": "Single Choice",
    "problemType2": "Multiple Choice",
    "problemType3": "Vote",
    "problemType4": "Fill-in-blank",
    "problemType5": "Short Answer",
    "success": "success",
    "error": "error",
    "source_ai": "AI",
    "source_random": "random",
    "source_blank": "blank",
    "answer": "answer(s)",
    "ai_failed": "AI answering failed, please answer manually! A random (choice) or blank (short-answer) fallback will be submitted per the \"Answer in Last 5s\" setting",
}


def _t(strings: dict, key: str) -> str:
    return strings.get(key, key)


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


def _format_badge(event_type: str, data: dict, s: dict) -> str:
    """Mirror Dashboard badge text (same logic as the JSX that picks
    problemType{n} for 'problem' events)."""
    if event_type == "problem" and data.get("problemtype"):
        return _t(s, f"problemType{data['problemtype']}")
    return _t(s, event_type)


def _format_label(event_type: str, data: dict, s: dict) -> str:
    """Port of Dashboard.tsx formatEventLabel."""
    type_name = _t(s, event_type)
    lesson = f"[{data['lesson']}] " if data.get("lesson") else ""
    status = data.get("status") or "success"

    if event_type == "signin":
        return f"{lesson}{type_name}: {_t(s, status)}"

    if event_type == "problem_received":
        return f"{lesson}{type_name}"

    if event_type == "problem":
        ptype = data.get("problemtype")
        problem_type_name = _t(s, f"problemType{ptype}") if ptype else type_name
        if status == "ai_failed":
            return f"{lesson}{problem_type_name}: {_t(s, 'ai_failed')}"
        status_text = _t(s, status)
        answer_text = _answers_text(data.get("answers"))
        source = data.get("source")
        source_text = f" [{_t(s, f'source_{source}')}]" if source else ""
        answer_suffix = f", {_t(s, 'answer')}: {answer_text}" if answer_text else ""
        return f"{lesson}{problem_type_name}: {status_text}{answer_suffix}{source_text}"

    if event_type == "danmu":
        content = data.get("content") or ""
        return f'{lesson}{type_name}: "{content}" — {_t(s, status)}'

    if event_type == "call":
        return f"{lesson}{type_name}"

    if event_type == "red_packet":
        return f"{lesson}{type_name}: {_t(s, status)}"

    if event_type == "session_expired":
        return type_name

    if event_type in ("lesson_end", "lesson_start"):
        return f"{lesson}{type_name}"

    message = data.get("message") or ""
    return f"{lesson}{type_name}{': ' + message if message else ''}"


def format_event(event_type: str, data: dict, language: str = "zh") -> Optional[Tuple[str, str]]:
    """Return (title, body) for a PushDeer push. Matches Dashboard event row:
    title = badge text, body = formatEventLabel output. Returns None if the
    event type isn't supposed to trigger a push."""
    if event_type not in _EVENT_SUBKEY:
        return None
    s = _EVENTS_EN if language == "en" else _EVENTS_ZH
    return _format_badge(event_type, data, s), _format_label(event_type, data, s)


# ---------------------------------------------------------------------------
# HTTP send
# ---------------------------------------------------------------------------


def _send(endpoint: str, push_key: str, text: str, desp: str = "") -> Tuple[bool, str]:
    url = endpoint.rstrip("/") + "/message/push"
    payload = {
        "pushkey": push_key,
        "text": text,
        "desp": desp,
        "type": "text",
    }
    try:
        # PushDeer expects form-encoded body.
        # Don't bypass the user's proxy: self-hosted endpoints may be anywhere.
        r = http_request("POST", url, retries=2, timeout=10, data=payload, proxies=None)
        result = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("PushDeer send failed: %s", e)
        return False, str(e)

    if result.get("code") == 0:
        return True, "ok"
    err = result.get("error") or result.get("content") or str(result)
    logger.warning("PushDeer API error: %s", err)
    return False, str(err)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _get_active_entry(account_id: str) -> Optional[dict]:
    cfg = get_pushdeer_config(account_id)
    keys = cfg.get("keys", [])
    idx = cfg.get("active_key", -1)
    if idx < 0 or idx >= len(keys):
        return None
    return keys[idx]


def _account_name(account_id: str) -> str:
    acc = get_account(account_id) or {}
    user = acc.get("user") or {}
    return user.get("name") or acc.get("name") or account_id


def dispatch(account_id: str, classroom_id: str, event_type: str, data: dict) -> None:
    """Fire-and-forget push for one event. Safe to call from any thread."""
    subkey = _EVENT_SUBKEY.get(event_type)
    if subkey is None:
        return

    entry = _get_active_entry(account_id)
    if entry is None:
        return
    endpoint = entry.get("endpoint", "").strip()
    push_key = entry.get("push_key", "").strip()
    if not endpoint or not push_key:
        return

    course_cfg = get_course_config(account_id, classroom_id)
    pd_cfg = course_cfg.get("pushdeer_notification", {})
    if not pd_cfg.get("enabled"):
        return
    if not pd_cfg.get(subkey, True):
        return

    language = get_pushdeer_config(account_id).get("language", "zh")
    formatted = format_event(event_type, data, language)
    if formatted is None:
        return
    title, body = formatted
    brand = "Yuketang Helper" if language == "en" else "雨课堂助手"
    title = f"{brand}-{title}-{_account_name(account_id)}"

    def _worker():
        _send(endpoint, push_key, title, body)

    threading.Thread(target=_worker, daemon=True, name="pushdeer-send").start()


def send_liveness(account_id: str) -> Tuple[bool, str]:
    """Send a liveness notification via the account's active PushDeer key.
    Used both by /api/pushdeer/test/{index} and the daily heartbeat scheduler."""
    entry = _get_active_entry(account_id)
    if entry is None:
        return False, "no active pushdeer key"
    endpoint = entry.get("endpoint", "").strip()
    push_key = entry.get("push_key", "").strip()
    if not endpoint or not push_key:
        return False, "endpoint and push_key required"
    if not get_sessionid(account_id):
        return False, "session expired"
    language = get_pushdeer_config(account_id).get("language", "zh")
    name = _account_name(account_id)
    if language == "en":
        title = f"Yuketang Helper-Heartbeat-{name}"
        body = "If you see this message, PushDeer and Yuketang Helper are running normally and this account's session has not expired."
    else:
        title = f"雨课堂助手-心跳-{name}"
        body = "当你看到这条消息，说明 PushDeer 和雨课堂助手正常运行且该账号 session 未过期。"
    return _send(endpoint, push_key, title, body)

import json
from pathlib import Path

STORE_DIR = Path(__file__).resolve().parent.parent / "store"
STORE_DIR.mkdir(parents=True, exist_ok=True)

_CONFIG_PATH = STORE_DIR / "config.json"

DEFAULT_COURSE_CONFIG: dict = {
    "type1": "random",
    "type2": "random",
    "type3": "random",
    "type4": "off",
    "type5": "off",
    "answer_delay_min": 3,
    "answer_delay_max": 10,
    "auto_danmu": True,
    "danmu_threshold": 3,
    "notification": {
        "enabled": True,
        "signin": True,
        "problem": True,
        "call": True,
        "danmu": True,
    },
    "voice_notification": {
        "enabled": False,
        "signin": True,
        "problem": True,
        "call": True,
        "danmu": True,
    },
}

DEFAULT_AI_CONFIG: dict = {
    "keys": [],       # [{"name": str, "provider": str, "key": str}, ...]
    "active_key": -1,  # index into keys, -1 = none
}

_EMPTY_CONFIG = {"sessionid": "", "user": {}, "course_list": [], "courses": {}, "ai": dict(DEFAULT_AI_CONFIG)}


def get_config() -> dict:
    if not _CONFIG_PATH.exists():
        save_config(dict(_EMPTY_CONFIG))
        return dict(_EMPTY_CONFIG)
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_course_config(course_id: str) -> dict:
    course = get_config().get("courses", {}).get(str(course_id), {})
    for key, value in DEFAULT_COURSE_CONFIG.items():
        if key not in course:
            course[key] = value
        elif isinstance(value, dict):
            merged = dict(value)
            merged.update(course[key])
            course[key] = merged
    return course


def update_course_config(course_id: str, data: dict) -> None:
    cfg = get_config()
    courses = cfg.setdefault("courses", {})
    courses.setdefault(str(course_id), {}).update(data)
    save_config(cfg)


def get_ai_config() -> dict:
    cfg = get_config()
    ai = cfg.get("ai", {})

    # Migrate old single-key format
    if "gemini_api_key" in ai:
        old_key = ai.pop("gemini_api_key", "")
        old_provider = ai.pop("provider", "gemini")
        keys = ai.get("keys", [])
        if old_key and not keys:
            keys.append({"name": "Default", "provider": old_provider, "key": old_key})
            ai["keys"] = keys
            ai["active_key"] = 0
        cfg["ai"] = ai
        save_config(cfg)

    merged = dict(DEFAULT_AI_CONFIG)
    merged.update(ai)
    # Ensure keys is always a list
    if not isinstance(merged.get("keys"), list):
        merged["keys"] = []
    return merged


def get_active_ai_key() -> tuple:
    """Return (provider, api_key) for the currently active key, or ("", "")."""
    ai = get_ai_config()
    keys = ai.get("keys", [])
    idx = ai.get("active_key", -1)
    if idx < 0 or idx >= len(keys):
        return ("", "")
    entry = keys[idx]
    return (entry.get("provider", ""), entry.get("key", ""))


def update_ai_config(data: dict) -> None:
    cfg = get_config()
    ai = cfg.setdefault("ai", dict(DEFAULT_AI_CONFIG))
    ai.update(data)
    save_config(cfg)

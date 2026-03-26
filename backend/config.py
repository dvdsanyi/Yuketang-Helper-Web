import json
from pathlib import Path

STORE_DIR = Path(__file__).resolve().parent.parent / "store"
STORE_DIR.mkdir(parents=True, exist_ok=True)

_CONFIG_PATH = STORE_DIR / "config.json"

DEFAULT_COURSE_CONFIG: dict = {
    "type1": "ai",
    "type2": "ai",
    "type3": "random",
    "type4": "off",
    "type5": "ai",
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
    "keys": [],
    "active_key": -1,
}

_EMPTY_CONFIG = {"sessionid": "", "domain": "", "user": {}, "course_list": [], "courses": {}, "ai": dict(DEFAULT_AI_CONFIG)}


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
    cfg.setdefault("courses", {}).setdefault(str(course_id), {}).update(data)
    save_config(cfg)


def get_ai_config() -> dict:
    cfg = get_config()
    ai = cfg.get("ai", {})
    merged = dict(DEFAULT_AI_CONFIG)
    merged.update(ai)
    return merged


def get_active_ai_key() -> tuple:
    """Return (provider, api_key) for the currently active key, or ("", "")."""
    ai = get_ai_config()
    keys = ai["keys"]
    idx = ai["active_key"]
    if idx < 0 or idx >= len(keys):
        return ("", "")
    entry = keys[idx]
    return (entry["provider"], entry["key"])


def update_ai_config(data: dict) -> None:
    cfg = get_config()
    ai = cfg.setdefault("ai", dict(DEFAULT_AI_CONFIG))
    ai.update(data)
    save_config(cfg)

import copy
import json
import logging
import os
import sys
import threading
import time as _time
from pathlib import Path
from typing import Any, Optional

# Bypass system proxy for Yuketang and ModelScope domains.
_NO_PROXY_DOMAINS = ",".join([
    "pro.yuketang.cn",
    "www.yuketang.cn",
    "changjiang.yuketang.cn",
    "huanghe.yuketang.cn",
    "api-inference.modelscope.cn",
])
_existing = os.environ.get("NO_PROXY", "")
os.environ["NO_PROXY"] = f"{_existing},{_NO_PROXY_DOMAINS}" if _existing else _NO_PROXY_DOMAINS

import requests


def _resolve_store_dir() -> Path:
    # 1. Explicit override — used by Docker (set in Dockerfile), CI, multi-instance.
    env_path = os.environ.get("YUKETANG_STORE_DIR")
    if env_path:
        return Path(env_path).expanduser()
    # 2. PyInstaller / frozen binary — OS-conventional per-user data dir.
    #    macOS:   ~/Library/Application Support/Yuketang Helper/
    #    Linux:   ~/.local/share/Yuketang Helper/
    #    Windows: %LOCALAPPDATA%\Yuketang Helper\
    if getattr(sys, "frozen", False):
        from platformdirs import user_data_dir
        return Path(user_data_dir("Yuketang Helper", appauthor=False))
    # 3. Python source mode — keep store next to the repo for hackability.
    return Path(__file__).resolve().parent.parent / "store"


STORE_DIR = _resolve_store_dir()
STORE_DIR.mkdir(parents=True, exist_ok=True)

_CONFIG_PATH = STORE_DIR / "config.json"

DEFAULT_COURSE_CONFIG: dict = {
    "type1": "ai",
    "type2": "ai",
    "type3": "ai",
    "type4": "off",
    "type5": "ai",
    "course_enabled": True,
    "answer_last5s": True,
    "auto_danmu": True,
    "auto_redpacket": True,
    "danmu_threshold": 3,
    "notification": {
        "enabled": False,
        "signin": True, "problem": True, "call": True, "danmu": True, "red_packet": True,
    },
    "voice_notification": {
        "enabled": False,
        "signin": True, "problem": True, "call": True, "danmu": True, "red_packet": True,
    },
    "pushdeer_notification": {
        "enabled": False,
        "signin": True, "problem": True, "call": True, "danmu": True, "red_packet": True,
    },
}

DEFAULT_AI_CONFIG: dict = {"keys": [], "active_key": -1, "fallback_keys": True}

DEFAULT_PUSHDEER_CONFIG: dict = {"keys": [], "active_key": -1, "language": "zh"}

# Seconds between polls for active lessons. Configurable per account.
DEFAULT_POLL_INTERVAL = 60
MIN_POLL_INTERVAL = 10
MAX_POLL_INTERVAL = 3600

DOMAIN_OPTIONS = [
    {"key": "www.yuketang.cn", "label": "Yuketang", "label_zh": "雨课堂"},
    {"key": "pro.yuketang.cn", "label": "Hetang Yuketang", "label_zh": "荷塘雨课堂"},
    {"key": "changjiang.yuketang.cn", "label": "Changjiang Yuketang", "label_zh": "长江雨课堂"},
    {"key": "huanghe.yuketang.cn", "label": "Huanghe Yuketang", "label_zh": "黄河雨课堂"},
]

DEFAULT_DOMAIN = "pro.yuketang.cn"


def new_empty_account(domain: str = DEFAULT_DOMAIN) -> dict:
    return {
        "name": "",
        "sessionid": "",
        "domain": domain,
        "user": {},
        "course_list": [],
        "courses": {},
        "ai": copy.deepcopy(DEFAULT_AI_CONFIG),
        "pushdeer": copy.deepcopy(DEFAULT_PUSHDEER_CONFIG),
        "poll_interval": DEFAULT_POLL_INTERVAL,
    }


def get_poll_interval(account_id: str) -> int:
    acc = get_account(account_id) or {}
    try:
        v = int(acc.get("poll_interval", DEFAULT_POLL_INTERVAL))
    except (TypeError, ValueError):
        v = DEFAULT_POLL_INTERVAL
    return max(MIN_POLL_INTERVAL, min(MAX_POLL_INTERVAL, v))


def set_poll_interval(account_id: str, seconds: int) -> int:
    clamped = max(MIN_POLL_INTERVAL, min(MAX_POLL_INTERVAL, int(seconds)))
    update_account(account_id, {"poll_interval": clamped})
    return clamped


_EMPTY_CONFIG = {"active_account_id": None, "accounts": {}}


# ---------------------------------------------------------------------------
# Core load / save
# ---------------------------------------------------------------------------


# Serialize every read-modify-write transaction against store/config.json.
# Using RLock so helpers that call other locked helpers don't deadlock.
_config_lock = threading.RLock()


def get_config() -> dict:
    with _config_lock:
        if not _CONFIG_PATH.exists():
            save_config(dict(_EMPTY_CONFIG))
            return dict(_EMPTY_CONFIG)
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)


def save_config(cfg: dict) -> None:
    # Atomic write: dump to *.tmp then os.replace so concurrent readers never
    # see a half-written file.
    tmp_path = _CONFIG_PATH.with_suffix(".json.tmp")
    with _config_lock:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _CONFIG_PATH)


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------


def get_active_account_id() -> Optional[str]:
    aid = get_config().get("active_account_id")
    return str(aid) if aid else None


def set_active_account_id(account_id: Optional[str]) -> None:
    with _config_lock:
        cfg = get_config()
        cfg["active_account_id"] = str(account_id) if account_id else None
        save_config(cfg)


def list_account_ids() -> list:
    return [str(k) for k in get_config().get("accounts", {}).keys()]


def list_accounts_summary() -> list:
    """UI-friendly summary: no secrets."""
    out = []
    accs = get_config().get("accounts", {})
    for aid, acc in accs.items():
        user = acc.get("user") or {}
        out.append({
            "id": str(aid),
            "name": user.get("name") or acc.get("name") or str(aid),
            "avatar": user.get("avatar") or "",
            "domain": acc.get("domain") or DEFAULT_DOMAIN,
            "logged_in": bool(acc.get("sessionid")),
        })
    return out


def get_account(account_id: str) -> Optional[dict]:
    return get_config().get("accounts", {}).get(str(account_id))


def account_exists(account_id: str) -> bool:
    return str(account_id) in get_config().get("accounts", {})


def upsert_account(account_id: str, data: dict) -> None:
    with _config_lock:
        cfg = get_config()
        cfg.setdefault("accounts", {})[str(account_id)] = data
        save_config(cfg)


def update_account(account_id: str, patch: dict) -> None:
    with _config_lock:
        cfg = get_config()
        acc = cfg.setdefault("accounts", {}).setdefault(str(account_id), new_empty_account())
        acc.update(patch)
        save_config(cfg)


def delete_account(account_id: str) -> None:
    with _config_lock:
        cfg = get_config()
        accs = cfg.setdefault("accounts", {})
        accs.pop(str(account_id), None)
        if cfg.get("active_account_id") == str(account_id):
            cfg["active_account_id"] = next(iter(accs.keys()), None)
        save_config(cfg)


# ---------------------------------------------------------------------------
# Per-account getters (require account_id)
# ---------------------------------------------------------------------------


def get_domain(account_id: str) -> str:
    acc = get_account(account_id) or {}
    return acc.get("domain") or DEFAULT_DOMAIN


def set_domain(account_id: str, domain: str) -> None:
    update_account(account_id, {"domain": domain})


def get_sessionid(account_id: str) -> str:
    acc = get_account(account_id) or {}
    return acc.get("sessionid", "")


def get_course_config(account_id: str, course_id: str) -> dict:
    acc = get_account(account_id) or {}
    course = acc.get("courses", {}).get(str(course_id), {})
    merged = dict(course)
    for key, value in DEFAULT_COURSE_CONFIG.items():
        if key not in merged:
            merged[key] = value
        elif isinstance(value, dict):
            m = dict(value)
            m.update(merged[key])
            merged[key] = m
    return merged


def update_course_config(account_id: str, course_id: str, data: dict) -> None:
    with _config_lock:
        cfg = get_config()
        acc = cfg.setdefault("accounts", {}).setdefault(str(account_id), new_empty_account())
        acc.setdefault("courses", {}).setdefault(str(course_id), {}).update(data)
        save_config(cfg)


def get_ai_config(account_id: str) -> dict:
    acc = get_account(account_id) or {}
    ai = acc.get("ai", {})
    merged = copy.deepcopy(DEFAULT_AI_CONFIG)
    merged.update(ai)
    return merged


def update_ai_config(account_id: str, data: dict) -> None:
    with _config_lock:
        cfg = get_config()
        acc = cfg.setdefault("accounts", {}).setdefault(str(account_id), new_empty_account())
        ai = acc.setdefault("ai", copy.deepcopy(DEFAULT_AI_CONFIG))
        ai.update(data)
        save_config(cfg)


def get_pushdeer_config(account_id: str) -> dict:
    acc = get_account(account_id) or {}
    pd = acc.get("pushdeer", {})
    merged = copy.deepcopy(DEFAULT_PUSHDEER_CONFIG)
    merged.update(pd)
    return merged


def update_pushdeer_config(account_id: str, data: dict) -> None:
    with _config_lock:
        cfg = get_config()
        acc = cfg.setdefault("accounts", {}).setdefault(str(account_id), new_empty_account())
        pd = acc.setdefault("pushdeer", copy.deepcopy(DEFAULT_PUSHDEER_CONFIG))
        pd.update(data)
        save_config(cfg)


# ---------------------------------------------------------------------------
# Shared HTTP helpers
# ---------------------------------------------------------------------------


def make_headers(domain: str, sessionid: str) -> dict:
    return {
        "Cookie": "sessionid=%s" % sessionid,
        "Referer": "https://%s/" % domain,
        "xt-agent": "web",
    }


def api_url(domain: str, template: str, **kwargs: Any) -> str:
    return template.format(domain=domain, **kwargs)


_http_log = logging.getLogger("http")

_DEFAULT_PROXIES = {"http": None, "https": None}
_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:104.0) Gecko/20100101 Firefox/104.0"


def http_request(
    method: str,
    url: str,
    retries: int = 10,
    timeout: int = 5,
    **kwargs: Any,
) -> requests.Response:
    kwargs.setdefault("proxies", _DEFAULT_PROXIES)
    kwargs.setdefault("timeout", timeout)
    headers = kwargs.setdefault("headers", {})
    headers.setdefault("User-Agent", _DEFAULT_UA)

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.request(method, url, **kwargs)
            if r.status_code < 500:
                return r
            _http_log.warning("HTTP %s %s → %s (attempt %d/%d)", method, url, r.status_code, attempt, retries)
        except requests.RequestException as e:
            _http_log.warning("HTTP %s %s failed: %s (attempt %d/%d)", method, url, e, attempt, retries)
            last_exc = e
        if attempt < retries:
            _time.sleep(min(attempt, 3))

    if last_exc:
        raise last_exc
    return r  # type: ignore[possibly-undefined]

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

# Yuketang must not go through a system/VPN proxy: sign-in and answering are
# deadline-bound, and a proxied round-trip is the difference between a late
# answer and none. Everything else (AI providers, PushDeer) still honours the
# user's proxy settings.
_NO_PROXY_DOMAINS = ",".join([
    "pro.yuketang.cn",
    "www.yuketang.cn",
    "changjiang.yuketang.cn",
    "huanghe.yuketang.cn",
    "api-inference.modelscope.cn",
])
_existing_no_proxy = os.environ.get("NO_PROXY", "")
os.environ["NO_PROXY"] = f"{_existing_no_proxy},{_NO_PROXY_DOMAINS}" if _existing_no_proxy else _NO_PROXY_DOMAINS

import requests
from platformdirs import user_data_dir
from pydantic import BaseModel, Field, PositiveInt, ValidationError, field_validator


logger = logging.getLogger(__name__)


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
        return Path(user_data_dir("Yuketang Helper", appauthor=False))
    # 3. Python source mode — keep store next to the repo for hackability.
    return Path(__file__).resolve().parent.parent / "store"


STORE_DIR = _resolve_store_dir()
STORE_DIR.mkdir(parents=True, exist_ok=True)

_CONFIG_PATH = STORE_DIR / "config.json"

DOMAIN_OPTIONS = [
    {"key": "www.yuketang.cn", "label": "Yuketang", "label_zh": "雨课堂"},
    {"key": "pro.yuketang.cn", "label": "Hetang Yuketang", "label_zh": "荷塘雨课堂"},
    {"key": "changjiang.yuketang.cn", "label": "Changjiang Yuketang", "label_zh": "长江雨课堂"},
    {"key": "huanghe.yuketang.cn", "label": "Huanghe Yuketang", "label_zh": "黄河雨课堂"},
]

DEFAULT_DOMAIN = "pro.yuketang.cn"
VALID_DOMAINS = {option["key"] for option in DOMAIN_OPTIONS}

# Per-account poll interval bounds. Lower bound prevents accidental DOS of
# Yuketang; upper bound stops typos turning the monitor into a no-op.
MIN_POLL_INTERVAL = 10
MAX_POLL_INTERVAL = 3600
DEFAULT_POLL_INTERVAL = 60

ChoiceAnswerMode = Literal["ai", "random", "off"]
ShortAnswerMode = Literal["ai", "blank", "off"]
PushdeerLanguage = Literal["zh", "en"]
AIProviderName = Literal["google", "qwen"]


class DomainModel(BaseModel):
    domain: str = DEFAULT_DOMAIN

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, value: str) -> str:
        if value not in VALID_DOMAINS:
            raise ValueError("invalid domain")
        return value


class NotificationSubModel(BaseModel):
    enabled: bool = False
    signin: bool = True
    problem: bool = True
    call: bool = True
    danmu: bool = True
    red_packet: bool = True


class CourseSettingsModel(BaseModel):
    type1: ChoiceAnswerMode = "ai"
    type2: ChoiceAnswerMode = "ai"
    type3: ChoiceAnswerMode = "ai"
    type4: Literal["off"] = "off"
    type5: ShortAnswerMode = "ai"
    course_enabled: bool = True
    answer_last5s: bool = True
    auto_danmu: bool = True
    auto_redpacket: bool = True
    danmu_threshold: PositiveInt = 3
    notification: NotificationSubModel = Field(default_factory=NotificationSubModel)
    voice_notification: NotificationSubModel = Field(default_factory=NotificationSubModel)
    pushdeer_notification: NotificationSubModel = Field(default_factory=NotificationSubModel)


class CourseModel(CourseSettingsModel):
    name: str = ""


class AIKeyModel(BaseModel):
    name: str
    provider: AIProviderName
    key: str


class AIModel(BaseModel):
    keys: list[AIKeyModel] = Field(default_factory=list)
    active_key: int = -1
    fallback_keys: bool = True


class PushdeerKeyModel(BaseModel):
    name: str
    endpoint: str
    push_key: str


class PushdeerModel(BaseModel):
    keys: list[PushdeerKeyModel] = Field(default_factory=list)
    active_key: int = -1
    language: PushdeerLanguage = "zh"


class CourseListItemModel(BaseModel):
    classroom_id: str
    name: str
    classroom_name: str = ""
    teacher_name: Optional[str] = None


class AccountModel(DomainModel):
    name: str = ""
    sessionid: str = ""
    user: dict = Field(default_factory=dict)
    course_list: list[CourseListItemModel] = Field(default_factory=list)
    courses: dict[str, CourseModel] = Field(default_factory=dict)
    ai: AIModel = Field(default_factory=AIModel)
    pushdeer: PushdeerModel = Field(default_factory=PushdeerModel)
    poll_interval: int = Field(default=DEFAULT_POLL_INTERVAL, ge=MIN_POLL_INTERVAL, le=MAX_POLL_INTERVAL)


class ConfigModel(BaseModel):
    active_account_id: Optional[str] = None
    accounts: dict[str, AccountModel] = Field(default_factory=dict)


def new_empty_account(domain: str = DEFAULT_DOMAIN) -> dict:
    return AccountModel(domain=domain).model_dump()


def clamp_poll_interval(seconds: int) -> int:
    return max(MIN_POLL_INTERVAL, min(MAX_POLL_INTERVAL, int(seconds)))


def get_poll_interval(account_id: str) -> int:
    acc = get_account(account_id)
    return acc["poll_interval"] if acc else DEFAULT_POLL_INTERVAL


def set_poll_interval(account_id: str, seconds: int) -> int:
    clamped = clamp_poll_interval(seconds)
    _patch_account(account_id, poll_interval=clamped)
    return clamped


# ---------------------------------------------------------------------------
# Core load / save
# ---------------------------------------------------------------------------


# Serialize every read-modify-write transaction against store/config.json.
# Using RLock so helpers that call other locked helpers don't deadlock.
config_lock = threading.RLock()


def _write_config(cfg: dict) -> None:
    # Atomic write: dump to *.tmp then os.replace so concurrent readers never
    # see a half-written file. Caller must hold config_lock.
    tmp_path = _CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _CONFIG_PATH)


def _backup_invalid_config() -> Optional[Path]:
    """Rename the current config.json out of the way so we don't lose user data
    when the schema can't be parsed. Returns the backup path, or None on failure."""
    if not _CONFIG_PATH.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = _CONFIG_PATH.with_name(f"config.invalid-{stamp}.json")
    try:
        os.rename(_CONFIG_PATH, backup)
        return backup
    except OSError as e:
        logger.warning(f"Could not back up invalid config: {e}")
        return None


def get_config() -> dict:
    with config_lock:
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return ConfigModel.model_validate(raw).model_dump()
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError, ValidationError) as e:
            backup = _backup_invalid_config()
            logger.warning(
                f"Config invalid ({e}); backed up to {backup or '<none>'} and resetting to empty"
            )
        fresh = ConfigModel().model_dump()
        _write_config(fresh)
        return fresh


def save_config(cfg: dict) -> None:
    with config_lock:
        _write_config(ConfigModel.model_validate(cfg).model_dump())


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------


def account_display_name(acc: dict, account_id: str) -> str:
    """Shared formula for "what to show as the human-readable account label"."""
    user = acc.get("user") or {}
    return user.get("name") or acc.get("name") or str(account_id)


def get_active_account_id() -> Optional[str]:
    return get_config()["active_account_id"]


def set_active_account_id(account_id: Optional[str]) -> None:
    with config_lock:
        cfg = get_config()
        cfg["active_account_id"] = account_id or None
        # cfg came from get_config() which already validated; skip re-validation.
        _write_config(cfg)


def list_accounts_summary() -> list[dict]:
    """UI-friendly summary: no secrets."""
    return [
        {
            "id": str(aid),
            "name": account_display_name(acc, aid),
            "avatar": (acc.get("user") or {}).get("avatar") or "",
            "domain": acc["domain"],
            "logged_in": bool(acc["sessionid"]),
        }
        for aid, acc in get_config()["accounts"].items()
    ]


def get_account(account_id: str) -> Optional[dict]:
    return get_config()["accounts"].get(str(account_id))


def account_exists(account_id: str) -> bool:
    return str(account_id) in get_config()["accounts"]


def upsert_account(account_id: str, data: dict) -> None:
    with config_lock:
        cfg = get_config()
        # Only the new account dict is untrusted input — validate just it.
        cfg["accounts"][str(account_id)] = AccountModel.model_validate(data).model_dump()
        _write_config(cfg)


def _patch_account(account_id: str, *, subkey: Optional[str] = None, **patch: Any) -> None:
    """Shared update helper for account, account.ai, account.pushdeer, account.courses.

    Caller decides whether to use `subkey`:
      _patch_account(id, sessionid="...")              → mutates acc directly
      _patch_account(id, subkey="ai", active_key=1)    → mutates acc["ai"]
      _patch_account(id, subkey="pushdeer", ...)       → mutates acc["pushdeer"]

    If the account doesn't exist yet it is materialized as an empty account —
    callers are expected to gate this via require_account / account_exists at
    the route layer so we don't create ghost accounts from typos.
    """
    with config_lock:
        cfg = get_config()
        accounts = cfg["accounts"]
        sid = str(account_id)
        if sid not in accounts:
            accounts[sid] = new_empty_account()
        target = accounts[sid][subkey] if subkey else accounts[sid]
        target.update(patch)
        # Validate just the touched account so we still catch corrupt patches,
        # without paying the cost of validating every other account on disk.
        accounts[sid] = AccountModel.model_validate(accounts[sid]).model_dump()
        _write_config(cfg)


def update_account(account_id: str, patch: dict) -> None:
    _patch_account(account_id, **patch)


def delete_account(account_id: str) -> None:
    with config_lock:
        cfg = get_config()
        cfg["accounts"].pop(str(account_id), None)
        if cfg["active_account_id"] == str(account_id):
            cfg["active_account_id"] = next(iter(cfg["accounts"].keys()), None)
        _write_config(cfg)


# ---------------------------------------------------------------------------
# Per-account getters (require account_id)
# ---------------------------------------------------------------------------


def get_domain(account_id: str) -> str:
    acc = get_account(account_id)
    return acc["domain"] if acc else DEFAULT_DOMAIN


def set_domain(account_id: str, domain: str) -> None:
    _patch_account(account_id, domain=domain)


def get_sessionid(account_id: str) -> str:
    acc = get_account(account_id)
    return acc["sessionid"] if acc else ""


def get_course_config(account_id: str, course_id: str) -> dict:
    acc = get_account(account_id)
    course = acc["courses"].get(str(course_id)) if acc else None
    return CourseModel.model_validate(course or {}).model_dump()


def update_course_config(account_id: str, course_id: str, data: dict) -> None:
    with config_lock:
        cfg = get_config()
        accounts = cfg["accounts"]
        sid = str(account_id)
        if sid not in accounts:
            accounts[sid] = new_empty_account()
        current = accounts[sid]["courses"].get(str(course_id), {})
        accounts[sid]["courses"][str(course_id)] = (
            CourseModel.model_validate({**current, **data}).model_dump()
        )
        _write_config(cfg)


def get_ai_config(account_id: str) -> dict:
    acc = get_account(account_id)
    return acc["ai"] if acc else AIModel().model_dump()


def update_ai_config(account_id: str, data: dict) -> None:
    _patch_account(account_id, subkey="ai", **data)


def get_pushdeer_config(account_id: str) -> dict:
    acc = get_account(account_id)
    return acc["pushdeer"] if acc else PushdeerModel().model_dump()


def update_pushdeer_config(account_id: str, data: dict) -> None:
    _patch_account(account_id, subkey="pushdeer", **data)


# ---------------------------------------------------------------------------
# Shared HTTP helpers
# ---------------------------------------------------------------------------


def make_headers(domain: str, sessionid: str) -> dict:
    return {
        "Cookie": f"sessionid={sessionid}",
        "Referer": f"https://{domain}/",
        "xt-agent": "web",
    }


def api_url(domain: str, template: str, **kwargs: Any) -> str:
    return template.format(domain=domain, **kwargs)


_http_log = logging.getLogger(f"{__name__}.http")

# Yuketang serves a desktop layout when the UA looks like a desktop browser.
# The exact version doesn't matter; keep this fixed so traffic patterns don't
# shift just because a dependency was bumped.
_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/120.0"


def http_request(
    method: str,
    url: str,
    attempts: int = 10,
    timeout: int = 5,
    **kwargs: Any,
) -> requests.Response:
    """Issue an HTTP request with bounded retry. ``attempts`` is the *total*
    number of tries (so attempts=1 means no retry). 2xx/3xx/4xx return
    immediately; 5xx and connection errors retry with a 1–3s backoff."""
    kwargs.setdefault("timeout", timeout)
    headers = kwargs.setdefault("headers", {})
    headers.setdefault("User-Agent", _DEFAULT_UA)

    last_exc: Optional[Exception] = None
    last_response: Optional[requests.Response] = None
    for attempt in range(1, attempts + 1):
        try:
            r = requests.request(method, url, **kwargs)
            if r.status_code < 500:
                return r
            last_response = r
            _http_log.warning(f"HTTP {method} {url} → {r.status_code} (attempt {attempt}/{attempts})")
        except requests.RequestException as e:
            _http_log.warning(f"HTTP {method} {url} failed: {e} (attempt {attempt}/{attempts})")
            last_exc = e
        if attempt < attempts:
            time.sleep(min(attempt, 3))

    if last_exc is not None:
        raise last_exc
    # All attempts returned 5xx — surface that to the caller instead of
    # handing them a 5xx response they'll probably mis-parse as JSON.
    assert last_response is not None  # attempts >= 1 guarantees one branch ran
    last_response.raise_for_status()
    return last_response

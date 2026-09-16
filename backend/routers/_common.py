"""Helpers shared across routers — kept here so cross-router imports don't
turn into a tangle (e.g. pushdeer_router importing ActiveKeyBody from ai)."""

from typing import Any, Callable

from fastapi import Depends, HTTPException, Path
from pydantic import BaseModel

import state


class ActiveKeyBody(BaseModel):
    active_key: int


def mask_secret(raw: str) -> str:
    """Render a secret for UI display: keep the first/last 4 chars, mask the rest."""
    if not raw:
        return ""
    if len(raw) > 8:
        return f"{raw[:4]}****{raw[-4:]}"
    return "****"


def _require_account(account_id: str = Path(...)) -> str:
    """FastAPI dependency — 404 unless `account_id` exists in the config."""
    state.require_account(account_id)
    return account_id


# Re-exported as a Depends-decorated parameter so route handlers can write
# `account_id: str = AccountDep` instead of repeating Depends(...) themselves.
AccountDep = Depends(_require_account)


# ---------------------------------------------------------------------------
# Shared add / delete / set_active behavior for AI keys and PushDeer keys.
# Both sections share the same `{keys: list, active_key: int}` schema, so
# the index-rebalancing rules around add / delete are identical.
# ---------------------------------------------------------------------------


def validate_index(keys: list, index: int) -> None:
    """Raise 400 unless `index` is a valid position in `keys`."""
    if index < 0 or index >= len(keys):
        raise HTTPException(status_code=400, detail="invalid index")


def add_key(
    get_cfg: Callable[[str], dict],
    update_cfg: Callable[[str, dict], None],
    account_id: str,
    entry: dict[str, Any],
) -> int:
    """Append a key entry. If no active key was set yet, make this one active.
    Returns the new entry's index."""
    cfg = get_cfg(account_id)
    keys = cfg["keys"]
    keys.append(entry)
    active = cfg["active_key"]
    if active < 0:
        active = 0
    update_cfg(account_id, {"keys": keys, "active_key": active})
    return len(keys) - 1


def delete_key(
    get_cfg: Callable[[str], dict],
    update_cfg: Callable[[str, dict], None],
    account_id: str,
    index: int,
) -> None:
    """Remove the entry at `index`. The active pointer is rebalanced so it
    keeps pointing at the "same" key when possible (or 0 when the deleted
    key was the active one, or -1 when the list is now empty)."""
    cfg = get_cfg(account_id)
    keys = cfg["keys"]
    validate_index(keys, index)
    keys.pop(index)
    active = cfg["active_key"]
    if active >= len(keys):
        active = len(keys) - 1
    elif active > index:
        active -= 1
    elif active == index:
        active = 0 if keys else -1
    update_cfg(account_id, {"keys": keys, "active_key": active})


def set_active_key(
    get_cfg: Callable[[str], dict],
    update_cfg: Callable[[str, dict], None],
    account_id: str,
    active_key: int,
) -> None:
    """Point the active key at `active_key`, rejecting out-of-range values so we
    never persist a dangling pointer (mirrors delete_key's validation)."""
    validate_index(get_cfg(account_id)["keys"], active_key)
    update_cfg(account_id, {"active_key": active_key})

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import event_log
import state
from auth import stop_monitor
from config import (
    DEFAULT_POLL_INTERVAL,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    DomainModel,
    account_exists,
    delete_account,
    get_active_account_id,
    get_domain,
    get_poll_interval,
    list_accounts_summary,
    set_active_account_id,
    set_domain,
    set_poll_interval,
    update_account,
)
from routers._common import AccountDep

router = APIRouter()


class SetActiveAccount(BaseModel):
    account_id: Optional[str]


class PollIntervalBody(BaseModel):
    poll_interval: int


@router.get("/api/accounts")
async def list_accounts_route():
    return {
        "active_account_id": get_active_account_id(),
        "accounts": list_accounts_summary(),
    }


@router.put("/api/accounts/active")
async def set_active_route(body: SetActiveAccount):
    if body.account_id and not account_exists(body.account_id):
        raise HTTPException(status_code=404, detail="account not found")
    set_active_account_id(body.account_id)
    return {"ok": True, "active_account_id": get_active_account_id()}


@router.post("/api/accounts")
async def create_account_route(body: DomainModel):
    """Allocate an in-memory pending slot; returns its id. Not persisted until
    login completes."""
    aid = f"pending-{uuid.uuid4().hex[:8]}"
    with state.pending_lock:
        state.pending[aid] = {"domain": body.domain}
    return {"ok": True, "account_id": aid}


@router.delete("/api/accounts/{account_id}")
async def delete_account_route(account_id: str):
    # All four cleanup calls are no-ops when their target is absent, so we
    # can run them unconditionally for both pending-only and real accounts.
    with state.pending_lock:
        state.pending.pop(account_id, None)
    stop_monitor(account_id)
    state.kick_subscribers(account_id)
    event_log.clear(account_id)
    delete_account(account_id)
    return {"ok": True, "active_account_id": get_active_account_id()}


@router.post("/api/accounts/{account_id}/logout")
async def logout_account_route(account_id: str = AccountDep):
    stop_monitor(account_id)
    update_account(account_id, {"sessionid": "", "user": {}, "course_list": []})
    return {"ok": True}


@router.get("/api/accounts/{account_id}/domain")
async def get_account_domain(account_id: str = AccountDep):
    return {"domain": get_domain(account_id)}


@router.put("/api/accounts/{account_id}/domain")
async def set_account_domain(body: DomainModel, account_id: str = AccountDep):
    set_domain(account_id, body.domain)
    return {"ok": True, "domain": body.domain}


@router.get("/api/accounts/{account_id}/poll-interval")
async def get_account_poll_interval(account_id: str = AccountDep):
    return {
        "poll_interval": get_poll_interval(account_id),
        "default": DEFAULT_POLL_INTERVAL,
        "min": MIN_POLL_INTERVAL,
        "max": MAX_POLL_INTERVAL,
    }


@router.put("/api/accounts/{account_id}/poll-interval")
async def set_account_poll_interval(body: PollIntervalBody, account_id: str = AccountDep):
    clamped = set_poll_interval(account_id, body.poll_interval)
    m = state.get_monitor(account_id)
    if m:
        m.wake()
    return {"ok": True, "poll_interval": clamped}

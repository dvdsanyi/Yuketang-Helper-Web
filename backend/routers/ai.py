from fastapi import APIRouter
from pydantic import BaseModel

from config import AIKeyModel, get_ai_config, update_ai_config
from routers._common import (
    AccountDep, ActiveKeyBody, add_key, delete_key, mask_secret, set_active_key,
)

router = APIRouter()


class FallbackBody(BaseModel):
    fallback_keys: bool = True


@router.get("/api/accounts/{account_id}/ai/settings")
async def get_ai_settings(account_id: str = AccountDep):
    cfg = get_ai_config(account_id)
    masked_keys = [{**entry, "key": mask_secret(entry["key"])} for entry in cfg["keys"]]
    return {"keys": masked_keys, "active_key": cfg["active_key"], "fallback_keys": cfg["fallback_keys"]}


@router.post("/api/accounts/{account_id}/ai/keys")
async def add_ai_key(body: AIKeyModel, account_id: str = AccountDep):
    index = add_key(get_ai_config, update_ai_config, account_id, body.model_dump())
    return {"ok": True, "index": index}


@router.delete("/api/accounts/{account_id}/ai/keys/{index}")
async def delete_ai_key(index: int, account_id: str = AccountDep):
    delete_key(get_ai_config, update_ai_config, account_id, index)
    return {"ok": True}


@router.put("/api/accounts/{account_id}/ai/active")
async def set_active_ai_key(body: ActiveKeyBody, account_id: str = AccountDep):
    set_active_key(get_ai_config, update_ai_config, account_id, body.active_key)
    return {"ok": True}


@router.put("/api/accounts/{account_id}/ai/fallback")
async def set_ai_fallback(body: FallbackBody, account_id: str = AccountDep):
    update_ai_config(account_id, body.model_dump())
    return {"ok": True}

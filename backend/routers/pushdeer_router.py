from fastapi import APIRouter
from pydantic import BaseModel

import pushdeer
from config import (
    PushdeerKeyModel, PushdeerLanguage,
    get_pushdeer_config, update_pushdeer_config,
)
from routers._common import (
    AccountDep, ActiveKeyBody, add_key, delete_key, mask_secret, set_active_key, validate_index,
)

router = APIRouter()


class LanguageBody(BaseModel):
    language: PushdeerLanguage


@router.get("/api/accounts/{account_id}/pushdeer/settings")
async def get_pushdeer_settings(account_id: str = AccountDep):
    cfg = get_pushdeer_config(account_id)
    masked_keys = [
        {
            "name": entry["name"],
            "endpoint": entry["endpoint"],
            "push_key": mask_secret(entry["push_key"]),
        }
        for entry in cfg["keys"]
    ]
    return {
        "keys": masked_keys,
        "active_key": cfg["active_key"],
        "language": cfg["language"],
    }


@router.post("/api/accounts/{account_id}/pushdeer/keys")
async def add_pushdeer_key(body: PushdeerKeyModel, account_id: str = AccountDep):
    index = add_key(get_pushdeer_config, update_pushdeer_config, account_id, body.model_dump())
    return {"ok": True, "index": index}


@router.delete("/api/accounts/{account_id}/pushdeer/keys/{index}")
async def delete_pushdeer_key(index: int, account_id: str = AccountDep):
    delete_key(get_pushdeer_config, update_pushdeer_config, account_id, index)
    return {"ok": True}


@router.put("/api/accounts/{account_id}/pushdeer/active")
async def set_active_pushdeer_key(body: ActiveKeyBody, account_id: str = AccountDep):
    set_active_key(get_pushdeer_config, update_pushdeer_config, account_id, body.active_key)
    return {"ok": True}


@router.put("/api/accounts/{account_id}/pushdeer/language")
async def set_pushdeer_language(body: LanguageBody, account_id: str = AccountDep):
    update_pushdeer_config(account_id, body.model_dump())
    return {"ok": True}


@router.post("/api/accounts/{account_id}/pushdeer/test/{index}")
async def pushdeer_test(index: int, account_id: str = AccountDep):
    cfg = get_pushdeer_config(account_id)
    keys = cfg["keys"]
    validate_index(keys, index)
    ok, msg = pushdeer.send_liveness(account_id, entry=keys[index])
    return {"ok": ok, "message": msg}

from fastapi import APIRouter

from config import DEFAULT_DOMAIN, DOMAIN_OPTIONS

router = APIRouter()


@router.get("/api/domains")
async def list_domains():
    return {"options": DOMAIN_OPTIONS, "default": DEFAULT_DOMAIN}

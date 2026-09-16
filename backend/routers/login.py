import asyncio
import base64
import json
import logging
import threading

import websocket
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA as CryptoRSA
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from auth import finalize_login, resolve_domain
from config import api_url, get_account, http_request

router = APIRouter()
log = logging.getLogger(__name__)

URL_WSS = "wss://{domain}/wsapp/"
URL_WEB_LOGIN = "https://{domain}/pc/web_login"
URL_PASSWORD_LOGIN = "https://{domain}/pc/login/verify_pwd_login/"
URL_GET_PUBLIC_KEY = "https://{domain}/pc/register/get_pws_public_key/"

# Sent to wsapp at QR-login start and again on each refresh tick.
_REQUEST_LOGIN_PAYLOAD = json.dumps({
    "op": "requestlogin",
    "role": "web",
    "version": 1.4,
    "type": "qrcode",
    "from": "web",
})
_QR_REFRESH_INTERVAL_S = 55


class PasswordLoginBody(BaseModel):
    phone: str
    password: str
    ticket: str
    randstr: str


@router.post("/api/accounts/{account_id}/auth/password-login")
async def password_login(account_id: str, body: PasswordLoginBody):
    domain = resolve_domain(account_id)
    if domain is None:
        raise HTTPException(status_code=404, detail="account not found")

    key_r = http_request("GET", api_url(domain, URL_GET_PUBLIC_KEY),
        headers={"Referer": f"https://{domain}/"},
    )
    pub_pem = key_r.json()["data"]["public_key"]
    cipher = PKCS1_v1_5.new(CryptoRSA.import_key(pub_pem))
    encrypted = base64.b64encode(cipher.encrypt(body.password.encode())).decode()

    login_url = api_url(domain, URL_PASSWORD_LOGIN)
    payload = {
        "name": body.phone,
        "pwd": encrypted,
        "type": "PP",
        "ticket": body.ticket,
        "randstr": body.randstr,
        "hcaptcha_token": "",
    }
    log.info(f"[{account_id}] Sending password login to {login_url}")

    r = http_request("POST", login_url,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Referer": f"https://{domain}/",
        },
    )

    data = r.json()
    if not data.get("success"):
        return {"ok": False, "message": data.get("msg") or str(data)}

    sessionid = r.cookies.get("sessionid")
    if not sessionid:
        return {"ok": False, "message": "Yuketang did not return a session cookie"}
    final_id = finalize_login(account_id, sessionid)
    user = (get_account(final_id) or {}).get("user")
    return {"ok": True, "account_id": final_id, "user": user}


@router.websocket("/ws/accounts/{account_id}/login")
async def ws_login(ws: WebSocket, account_id: str):
    await ws.accept()
    domain = resolve_domain(account_id)
    if domain is None:
        await ws.send_json({"type": "error", "message": "account not found"})
        await ws.close()
        return
    loop = asyncio.get_running_loop()
    login_queue: asyncio.Queue = asyncio.Queue()

    def on_open(wsapp):
        wsapp.send(_REQUEST_LOGIN_PAYLOAD)

    def _put(msg: dict) -> None:
        asyncio.run_coroutine_threadsafe(login_queue.put(msg), loop)

    def _fetch_qr(ticket: str) -> None:
        try:
            resp = http_request("GET", ticket)
            img_b64 = base64.b64encode(resp.content).decode()
            content_type = resp.headers.get("Content-Type", "image/png").split(";")[0]
            _put({"type": "qr", "url": f"data:{content_type};base64,{img_b64}"})
        except Exception as e:
            _put({"type": "error", "message": f"QR fetch failed: {e}"})

    def _exchange_session(user_id: str, auth: str) -> None:
        try:
            r = http_request("POST", api_url(domain, URL_WEB_LOGIN),
                data=json.dumps({"UserID": user_id, "Auth": auth}),
            )
            sessionid = r.cookies.get("sessionid")
            if not sessionid:
                _put({"type": "error", "message": "Yuketang did not return a session cookie"})
            else:
                _put({"type": "success", "sessionid": sessionid})
        except Exception as e:
            _put({"type": "error", "message": f"web_login failed: {e}"})

    def on_message(wsapp, message):
        # Yuketang's wsapp callback runs on the websocket read thread; offload
        # any synchronous HTTP so a slow upstream can't stall ping handling.
        data = json.loads(message)
        op = data["op"]

        if op == "requestlogin":
            threading.Thread(target=_fetch_qr, args=(data["ticket"],), daemon=True).start()

        elif op == "loginsuccess":
            threading.Thread(
                target=_exchange_session, args=(data["UserID"], data["Auth"]), daemon=True,
            ).start()
            wsapp.close()

    def on_error(wsapp, error):
        asyncio.run_coroutine_threadsafe(
            login_queue.put({"type": "error", "message": str(error)}), loop
        )

    stop_refresh = threading.Event()

    def qr_refresh_loop(wsapp_ref):
        # Re-send `requestlogin` every 55s; Event.wait returns True when
        # stop_refresh is set, so the loop exits promptly on close.
        while not stop_refresh.wait(_QR_REFRESH_INTERVAL_S):
            try:
                wsapp_ref.send(_REQUEST_LOGIN_PAYLOAD)
            except Exception:
                return

    wsapp = websocket.WebSocketApp(
        url=api_url(domain, URL_WSS),
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
    )

    threading.Thread(target=wsapp.run_forever, daemon=True, name="login-ws").start()
    threading.Thread(target=qr_refresh_loop, args=(wsapp,), daemon=True, name="login-ws-refresh").start()

    try:
        while True:
            msg = await login_queue.get()
            if msg["type"] == "success":
                final_id = finalize_login(account_id, msg["sessionid"])
                user = (get_account(final_id) or {}).get("user")
                await ws.send_json({"type": "success", "account_id": final_id, "user": user})
                break
            await ws.send_json(msg)
            if msg["type"] == "error":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass

    stop_refresh.set()
    wsapp.close()

from __future__ import annotations

import logging
import time

import requests

from ecs.app.config import (
    WX_AGENT_ID,
    WX_CORP_ID,
    WX_GET_TOKEN_URL,
    WX_SECRET,
    WX_SEND_MSG_URL,
)

log = logging.getLogger("ecs.wecom.service")
_token_cache: dict[str, str | float] = {"token": "", "expires": 0.0}


def get_access_token() -> str:
    token = str(_token_cache["token"])
    expires = float(_token_cache["expires"])
    if token and time.time() < expires:
        return token
    response = requests.get(
        WX_GET_TOKEN_URL,
        params={"corpid": WX_CORP_ID, "corpsecret": WX_SECRET},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"WeCom access token failed: {data}")
    token = data["access_token"]
    _token_cache["token"] = token
    _token_cache["expires"] = time.time() + int(data.get("expires_in", 7200)) - 300
    return token


def send_wecom_message(to_user: str, content: str) -> bool:
    try:
        token = get_access_token()
        response = requests.post(
            WX_SEND_MSG_URL,
            params={"access_token": token},
            json={
                "touser": to_user,
                "msgtype": "text",
                "agentid": int(WX_AGENT_ID),
                "text": {"content": content},
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errcode") != 0:
            log.error("WeCom send failed: %s", data)
            return False
        return True
    except Exception:
        log.exception("WeCom send exception")
        return False

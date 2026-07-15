from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import PlainTextResponse

from ecs.app.config import WX_AES_KEY, WX_CORP_ID, WX_TOKEN
from ecs.app.gateway import gateway
from ecs.app.wecom_crypto import WXBizMsgCrypt
from ecs.app.wecom_service import send_wecom_message

router = APIRouter()
log = logging.getLogger("ecs.wecom")
_message_dedup: dict[str, float] = {}


async def answer_and_reply(from_user: str, question: str) -> None:
    try:
        answer = await gateway.ask(
            question,
            conversation_id=f"wecom:{from_user}",
            language="zh-CN",
        )
        await asyncio.to_thread(send_wecom_message, from_user, answer)
    except Exception:
        log.exception("WeCom reply failed for user %s", from_user)


@router.get("/wecom/callback")
async def verify_wecom(
    msg_signature: str = Query(),
    timestamp: str = Query(),
    nonce: str = Query(),
    echostr: str = Query(),
):
    try:
        crypt = WXBizMsgCrypt(WX_TOKEN, WX_AES_KEY, WX_CORP_ID)
        expected = crypt.generate_signature(timestamp, nonce, echostr)
        if expected != msg_signature:
            return PlainTextResponse("signature mismatch", status_code=400)
        return PlainTextResponse(crypt.decrypt(echostr))
    except Exception:
        log.exception("WeCom URL verification failed")
        return PlainTextResponse("error", status_code=400)


@router.post("/wecom/callback")
async def receive_wecom(
    request: Request,
    background_tasks: BackgroundTasks,
    msg_signature: str = Query(),
    timestamp: str = Query(),
    nonce: str = Query(),
):
    try:
        xml_text = (await request.body()).decode("utf-8")
        crypt = WXBizMsgCrypt(WX_TOKEN, WX_AES_KEY, WX_CORP_ID)
        encrypted = WXBizMsgCrypt.parse_encrypt_xml(xml_text)["encrypt"]
        if crypt.generate_signature(timestamp, nonce, encrypted) != msg_signature:
            return PlainTextResponse("signature mismatch", status_code=400)
        message = WXBizMsgCrypt.parse_decrypted_msg(crypt.decrypt(encrypted))

        message_id = message.get("MsgId") or message.get("MsgID") or ""
        now = time.time()
        if message_id:
            previous = _message_dedup.get(message_id)
            if previous and now - previous < 600:
                return PlainTextResponse("success")
            _message_dedup[message_id] = now

        if message.get("MsgType") == "text":
            content = str(message.get("Content") or "").strip()
            from_user = str(message.get("FromUserName") or "").strip()
            if content and from_user:
                background_tasks.add_task(answer_and_reply, from_user, content)
        return PlainTextResponse("success")
    except Exception:
        log.exception("WeCom callback processing failed")
        return PlainTextResponse("success")

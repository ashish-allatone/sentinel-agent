import os
import json
from typing import Optional

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.db import get_async_db
from models.channel_account_model import ChannelAccount
from models.user_model import CommunicationChannel   
import channel_providers as providers

channel_account_router = APIRouter()
static_key=b"3ro6WjqwAl5LUvZSJ06fh9y2Po0pltR8Z-_8xzKQTGc="
_fernet = Fernet(static_key)


def _encrypt(d: dict) -> str:
    return _fernet.encrypt(json.dumps(d).encode()).decode()


def _decrypt(s: str) -> dict:
    return json.loads(_fernet.decrypt(s.encode()).decode())


class AddAccountRequest(BaseModel):
    label: str
    channel_type: str         
    credentials: dict          

class SendRequest(BaseModel):
    recipient: Optional[str] = None               
    communication_channel_id: Optional[int] = None  
    subject: str = ""
    body: str = "message"


def _check_fields(channel_type: str, creds: dict):
    required = providers.REQUIRED_FIELDS.get(channel_type)
    if required is None:
        raise HTTPException(status_code=400,
                            detail=f"Unknown channel_type. Use one of {list(providers.REQUIRED_FIELDS)}")
    missing = [f for f in required if not creds.get(f)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing credential fields: {missing}")


# ── add + VERIFY LIVE ─────────────────────────────────────────────────
@channel_account_router.post("/channel-accounts")
async def add_channel_account(req: AddAccountRequest, db: AsyncSession = Depends(get_async_db)):
    _check_fields(req.channel_type, req.credentials)

    verify_fn = providers.VERIFY[req.channel_type]
    try:
        identifier = await verify_fn(req.credentials)   # <-- the real live check
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Credential verification failed: {e}")

    row = ChannelAccount(
        label=req.label, channel_type=req.channel_type, identifier=identifier,
        credentials_enc=_encrypt(req.credentials), is_verified=True, is_active=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "label": row.label, "channel_type": row.channel_type,
            "identifier": row.identifier, "is_verified": row.is_verified}


@channel_account_router.get("/channel-accounts")
async def list_channel_accounts(db: AsyncSession = Depends(get_async_db)):
    rows = (await db.execute(select(ChannelAccount))).scalars().all()
    return {"accounts": [
        {"id": r.id, "label": r.label, "channel_type": r.channel_type,
         "identifier": r.identifier, "is_verified": r.is_verified, "is_active": r.is_active}
        for r in rows
    ]}


@channel_account_router.delete("/channel-accounts/{account_id}")
async def delete_channel_account(account_id: int, db: AsyncSession = Depends(get_async_db)):
    row = await db.get(ChannelAccount, account_id)
    if not row:
        raise HTTPException(status_code=404, detail="Account not found.")
    await db.delete(row)
    await db.commit()
    return {"id": account_id, "deleted": True}


# ── send: account (sender) + recipient (destination) -> message ───────
@channel_account_router.post("/channel-accounts/{account_id}/send")
async def send_via_account(account_id: int, req: SendRequest,
                           db: AsyncSession = Depends(get_async_db)):
    account = await db.get(ChannelAccount, account_id)
    if not account or not account.is_active:
        raise HTTPException(status_code=404, detail="Account not found or inactive.")

    recipient = req.recipient
    if not recipient and req.communication_channel_id:
        ch = await db.get(CommunicationChannel, req.communication_channel_id)
        if not ch:
            raise HTTPException(status_code=404, detail="Recipient channel not found.")
        recipient = ch.value
    if not recipient:
        raise HTTPException(status_code=400,
                            detail="Provide either 'recipient' or 'communication_channel_id'.")

    creds = _decrypt(account.credentials_enc)
    send_fn = providers.SEND[account.channel_type]
    try:
        await send_fn(creds, recipient, req.subject, req.body)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Send failed: {e}")

    return {"sent_from": account.identifier, "sent_to": recipient,
            "channel_type": account.channel_type, "status": "ok"}


# ── send WITH A FILE (e.g. the SOC2 PDF) attached ──────────────────────
@channel_account_router.post("/channel-accounts/{account_id}/send-file")
async def send_file_via_account(
    account_id: int,
    subject: str = Form(""),
    body: str = Form("message"),
    recipient: Optional[str] = Form(None),
    communication_channel_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
):
    account = await db.get(ChannelAccount, account_id)
    if not account or not account.is_active:
        raise HTTPException(status_code=404, detail="Account not found or inactive.")

    send_fn = providers.SEND_WITH_ATTACHMENT.get(account.channel_type)
    if not send_fn:
        raise HTTPException(
            status_code=400,
            detail=f"'{account.channel_type}' does not support file attachments. "
                   f"Supported: {list(providers.SEND_WITH_ATTACHMENT)}")

    dest = recipient
    if not dest and communication_channel_id:
        ch = await db.get(CommunicationChannel, communication_channel_id)
        if not ch:
            raise HTTPException(status_code=404, detail="Recipient channel not found.")
        dest = ch.value
    if not dest:
        raise HTTPException(status_code=400,
                            detail="Provide either 'recipient' or 'communication_channel_id'.")

    file_bytes = await file.read()
    creds = _decrypt(account.credentials_enc)
    try:
        await send_fn(creds, dest, subject, body, file_bytes, file.filename)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Send failed: {e}")

    return {"sent_from": account.identifier, "sent_to": dest,
            "channel_type": account.channel_type, "attachment": file.filename,
            "status": "ok"}
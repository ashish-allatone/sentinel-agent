import base64
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.db import get_async_db
from models.user_model import CommunicationChannel
from schemas.v1.standard_schema import standard_success_response

import notifier

communication_send_router = APIRouter()

TYPE_MAP = {
    "email": "email", "Email": "email", "gmail": "email", "Gmail": "email",
    "outlook": "outlook", "Outlook": "outlook",
    "whatsapp": "whatsapp", "WhatsApp": "whatsapp",
    "sms": "sms", "SMS": "sms",
    "slack": "slack", "Slack": "slack",
    "discord": "discord", "Discord": "discord",
    "teams": "teams", "Microsoft Teams": "teams", "MicrosoftTeams": "teams",
    "telegram": "telegram", "Telegram": "telegram",
}

WEBHOOK_KINDS = {"slack", "discord", "telegram"}          # no sender creds needed
PDF_CAPABLE = {"email", "outlook", "telegram", "discord"}  # can actually carry a file

READY_CHECK = {
    "email": notifier.gmail_ready,
    "outlook": notifier.outlook_ready,
    "whatsapp": notifier.whatsapp_ready,
    "sms": notifier.sms_ready,
    "teams": notifier.teams_graph_ready,
}


class SendRequest(BaseModel):
    title: str
    message: str
    severity: str = "high"
    types: Optional[List[str]] = None         # target every row of these DB `type`s
    channel_ids: Optional[List[int]] = None    # target ONLY these specific rows (by id)
    pdf_base64: Optional[str] = None           # optional PDF to attach where supported
    pdf_filename: str = "report.pdf"


def _text(title, severity, message) -> str:
    return f"[{severity.upper()}] {title}\n\n{message}"


def _ready(kind: str) -> bool:
    if kind in WEBHOOK_KINDS:
        return True
    check = READY_CHECK.get(kind)
    return bool(check and check())


@communication_send_router.get("/test-config")
async def test_config(db: AsyncSession = Depends(get_async_db)):
    rows = (await db.execute(select(CommunicationChannel))).scalars().all()
    out = []
    for ch in rows:
        kind = TYPE_MAP.get(ch.type, "unknown")
        ready = _ready(kind)
        note = ""
        if not ready:
            note = ("type not mapped in TYPE_MAP" if kind == "unknown"
                    else f"{kind} sender not configured in .env")
        out.append({"id": ch.id, "name": ch.name, "type": ch.type,
                    "kind": kind, "ready": ready,
                    "supports_pdf": kind in PDF_CAPABLE, "note": note})
    return standard_success_response(data={"channels": out},
                                     message="Channel send-readiness")


@communication_send_router.post("/send")
async def send(req: SendRequest, db: AsyncSession = Depends(get_async_db)):
    rows = (await db.execute(select(CommunicationChannel))).scalars().all()

    if req.channel_ids:
        wanted_ids = set(req.channel_ids)
        rows = [r for r in rows if r.id in wanted_ids]
        found_ids = {r.id for r in rows}
        missing = wanted_ids - found_ids
        if missing:
            raise HTTPException(status_code=404,
                                detail=f"channel_ids not found: {sorted(missing)}")
    elif req.types:
        wanted_types = set(req.types)
        rows = [r for r in rows if r.type in wanted_types]

    pdf_bytes = None
    if req.pdf_base64:
        try:
            pdf_bytes = base64.b64decode(req.pdf_base64)
        except Exception:
            raise HTTPException(status_code=400, detail="pdf_base64 is not valid base64")

    text = _text(req.title, req.severity, req.message)
    subject = f"[{req.severity.upper()}] {req.title}"
    results = []

    for ch in rows:
        kind = TYPE_MAP.get(ch.type, "unknown")
        attach = pdf_bytes if (pdf_bytes and kind in PDF_CAPABLE) else None
        try:
            if kind == "email":
                await notifier.send_gmail(ch.value, subject, text, attach, req.pdf_filename)
            elif kind == "outlook":
                await notifier.send_outlook(ch.value, subject, text, attach, req.pdf_filename)
            elif kind == "whatsapp":
                await notifier.send_whatsapp(ch.value, text, title=req.title, severity=req.severity)
            elif kind == "sms":
                await notifier.send_sms(ch.value, text, title=req.title, severity=req.severity)
            elif kind == "slack":
                await notifier.send_slack(ch.value, text)
            elif kind == "discord":
                await notifier.send_discord(ch.value, text, attach, req.pdf_filename)
            elif kind == "teams":
                # await notifier.send_teams(ch.value,text) #or 
                await notifier.send_teams_graph(ch.value, text)
            elif kind == "telegram":
                await notifier.send_telegram(ch.value, text, attach, req.pdf_filename)
            else:
                results.append({"id": ch.id, "name": ch.name, "type": ch.type,
                                "status": "not_mapped"})
                continue
            status = "ok"
            if pdf_bytes and kind not in PDF_CAPABLE:
                status = "ok (pdf not attached: channel type doesn't support files)"
        except Exception as e:
            status = f"error: {e}"
        results.append({"id": ch.id, "name": ch.name, "type": ch.type, "status": status})

    delivered = [r for r in results if r["status"].startswith("ok")]
    return standard_success_response(
        data={"sent": len(delivered), "total": len(results), "results": results},
        message="Send attempted")
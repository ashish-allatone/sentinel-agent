import ssl
import asyncio
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

import httpx


# ─────────────────────────────── Gmail ──────────────────────────────────
def _smtp_login_sync(host, port, user, password):
    with smtplib.SMTP(host, port, timeout=15) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, password)


def _smtp_send_sync(host, port, user, password, sender, recipient, subject, body):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    with smtplib.SMTP(host, port, timeout=20) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, password)
        s.send_message(msg)


def _smtp_send_with_attachment_sync(host, port, user, password, sender, recipient,
                                    subject, body, attachment_bytes, attachment_filename):
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(body, "plain", "utf-8"))

    part = MIMEApplication(attachment_bytes, _subtype="pdf")
    part.add_header("Content-Disposition", "attachment", filename=attachment_filename)
    msg.attach(part)

    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, password)
        s.send_message(msg)


async def verify_gmail(creds: dict) -> str:
    await asyncio.to_thread(_smtp_login_sync, "smtp.gmail.com", 587,
                            creds["email"], creds["password"])
    return creds["email"]


async def send_gmail(creds: dict, recipient: str, subject: str, body: str) -> None:
    await asyncio.to_thread(_smtp_send_sync, "smtp.gmail.com", 587,
                            creds["email"], creds["password"], creds["email"],
                            recipient, subject, body)


async def send_gmail_with_attachment(creds: dict, recipient: str, subject: str, body: str,
                                     attachment_bytes: bytes, attachment_filename: str) -> None:
    await asyncio.to_thread(_smtp_send_with_attachment_sync, "smtp.gmail.com", 587,
                            creds["email"], creds["password"], creds["email"], recipient,
                            subject, body, attachment_bytes, attachment_filename)


# ───────────────────────── Outlook / 365 (SMTP) ─────────────────────────
async def verify_outlook365(creds: dict) -> str:
    await asyncio.to_thread(_smtp_login_sync, "smtp.office365.com", 587,
                            creds["email"], creds["password"])
    return creds["email"]


async def send_outlook365(creds: dict, recipient: str, subject: str, body: str) -> None:
    await asyncio.to_thread(_smtp_send_sync, "smtp.office365.com", 587,
                            creds["email"], creds["password"], creds["email"],
                            recipient, subject, body)


async def send_outlook365_with_attachment(creds: dict, recipient: str, subject: str, body: str,
                                          attachment_bytes: bytes, attachment_filename: str) -> None:
    await asyncio.to_thread(_smtp_send_with_attachment_sync, "smtp.office365.com", 587,
                            creds["email"], creds["password"], creds["email"], recipient,
                            subject, body, attachment_bytes, attachment_filename)


# ──────────────────────────────── Telegram ──────────────────────────────
async def verify_telegram(creds: dict) -> str:
    """Calls Telegram's getMe — proves the bot token is real and returns its username."""
    token = creds["bot_token"]
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"https://api.telegram.org/bot{token}/getMe")
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(j.get("description", "Invalid bot token"))
    return "@" + j["result"]["username"]


async def send_telegram(creds: dict, recipient: str, subject: str, body: str) -> None:
    # recipient here is the chat_id (from CommunicationChannel.value)
    token = creds["bot_token"]
    text = f"{subject}\n\n{body}" if subject else body
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                         json={"chat_id": recipient, "text": text})
        r.raise_for_status()


async def send_telegram_with_attachment(creds: dict, recipient: str, subject: str, body: str,
                                        attachment_bytes: bytes, attachment_filename: str) -> None:
    token = creds["bot_token"]
    caption = f"{subject}\n\n{body}" if subject else body
    files = {"document": (attachment_filename, attachment_bytes, "application/pdf")}
    data = {"chat_id": recipient, "caption": caption[:1024]}   # Telegram caption limit
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"https://api.telegram.org/bot{token}/sendDocument",
                         data=data, files=files)
        r.raise_for_status()


# ───────────────────────── WhatsApp (Twilio) ────────────────────────────
async def verify_whatsapp(creds: dict) -> str:
    """Checks the Twilio account itself is real (fetches account info)."""
    sid, token = creds["account_sid"], creds["auth_token"]
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, auth=(sid, token))
    if r.status_code != 200:
        raise RuntimeError("Invalid Twilio Account SID / Auth Token")
    return creds.get("from_number", sid)


async def send_whatsapp(creds: dict, recipient: str, subject: str, body: str) -> None:
    sid, token = creds["account_sid"], creds["auth_token"]
    from_num = creds["from_number"]
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = {
        "From": from_num if from_num.startswith("whatsapp:") else f"whatsapp:{from_num}",
        "To": recipient if recipient.startswith("whatsapp:") else f"whatsapp:{recipient}",
        "Body": body[:1500],
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, data=data, auth=(sid, token))
        r.raise_for_status()


# ──────────────────────────── SMS (Twilio) ──────────────────────────────
async def verify_sms(creds: dict) -> str:
    return await verify_whatsapp(creds)   # same Twilio account check


async def send_sms(creds: dict, recipient: str, subject: str, body: str) -> None:
    sid, token = creds["account_sid"], creds["auth_token"]
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = {"From": creds["from_number"], "To": recipient, "Body": body[:1500]}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, data=data, auth=(sid, token))
        r.raise_for_status()


# ────────────────────────────── Jira ─────────────────────────────────
# creds = {"base_url": "https://company.atlassian.net", "email": "...",
#          "api_token": "...", "project_key": "SEC"}
def _jira_auth(creds: dict):
    return (creds["email"], creds["api_token"])


async def verify_jira(creds: dict) -> str:
    """Confirms the base_url/email/api_token are valid AND the project_key exists."""
    base = creds["base_url"].rstrip("/")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{base}/rest/api/3/myself", auth=_jira_auth(creds))
        if r.status_code == 401:
            raise RuntimeError("Invalid Jira email / API token")
        r.raise_for_status()
        who = r.json().get("displayName", creds["email"])

        pr = await c.get(f"{base}/rest/api/3/project/{creds['project_key']}",
                         auth=_jira_auth(creds))
        if pr.status_code == 404:
            raise RuntimeError(f"Project key '{creds['project_key']}' not found")
        pr.raise_for_status()
    return f"{who} @ {creds['project_key']}"


async def send_jira(creds: dict, recipient: str, subject: str, body: str) -> None:
    """'Send' for Jira = create an issue. `recipient` is ignored (Jira has no
    recipient concept) — kept only so the call shape matches every other
    channel's send_fn(creds, recipient, subject, body)."""
    base = creds["base_url"].rstrip("/")
    payload = {
        "fields": {
            "project": {"key": creds["project_key"]},
            "summary": subject or "Security Alert",
            "description": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph",
                            "content": [{"type": "text", "text": body}]}],
            },
            "issuetype": {"name": "Task"},
        }
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{base}/rest/api/3/issue", json=payload, auth=_jira_auth(creds))
        r.raise_for_status()   # 201 Created; response has the new issue key (e.g. "SEC-123")


# ─────────────────── Webhooks: Slack / Discord (no account) ────────────
# These need no ChannelAccount at all — the webhook URL in CommunicationChannel
# IS the credential. Kept here for a consistent call shape from the send API.
async def send_slack_webhook(webhook_url: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(webhook_url, json={"text": text})
        r.raise_for_status()


async def send_discord_webhook(webhook_url: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(webhook_url, json={"content": text})
        r.raise_for_status()


# ── registries the API layer uses ────────────────────────────────────
VERIFY = {
    "gmail": verify_gmail, "outlook365": verify_outlook365,
    "telegram": verify_telegram, "whatsapp": verify_whatsapp, "sms": verify_sms,
    "jira": verify_jira,
}

SEND = {
    "gmail": send_gmail, "outlook365": send_outlook365,
    "telegram": send_telegram, "whatsapp": send_whatsapp, "sms": send_sms,
    "jira": send_jira,
}

REQUIRED_FIELDS = {
    "gmail": ["email", "password"],
    "outlook365": ["email", "password"],
    "telegram": ["bot_token"],
    "whatsapp": ["account_sid", "auth_token", "from_number"],
    "sms": ["account_sid", "auth_token", "from_number"],
    "jira": ["base_url", "email", "api_token", "project_key"],
}

# Channels that can send a PDF/file directly (function signature:
# fn(creds, recipient, subject, body, attachment_bytes, attachment_filename)).
# WhatsApp/SMS need a public media URL instead of raw bytes (Twilio can't take
# an upload directly) — not included here; ask if you need that variant.
# Slack/Discord webhooks cannot upload files at all — only a bot-token API can.
SEND_WITH_ATTACHMENT = {
    "gmail": send_gmail_with_attachment,
    "outlook365": send_outlook365_with_attachment,
    "telegram": send_telegram_with_attachment,
}
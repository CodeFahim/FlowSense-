"""Transactional email (verification and password codes, account and password notices).

Providers, server-side only:
- "smtp": any SMTP server over TLS, e.g. Gmail (smtp.gmail.com:465) with an app password from
  LIFELINE_SMTP_USERNAME / LIFELINE_SMTP_PASSWORD. The password never leaves the server and is never logged.
- "brevo": Brevo HTTPS API with LIFELINE_BREVO_API_KEY (for hosts that block outgoing SMTP ports, such as
  Render's free plan); the sender address must be verified in Brevo.
- "resend": Resend HTTP API with LIFELINE_RESEND_API_KEY.
When email is not configured nothing is sent and callers report "not configured"; delivery is never
simulated as successful. Messages come from "FlowSense - No Reply"; the footer says the inbox isn't
monitored.

Links in emails are built only from LIFELINE_PUBLIC_APP_URL (a trusted origin),
never from request headers. Tokens are never logged or stored in the delivery log.
"""

from __future__ import annotations

import contextlib
import html
import logging
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import EmailDelivery

log = logging.getLogger("lifeline.email")
RESEND_URL = "https://api.resend.com/emails"
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


@dataclass
class SendResult:
    status: str  # sent | failed | not_configured
    detail: str = ""


def _post_resend(to: str, subject: str, text: str, body_html: str) -> str:
    s = get_settings()
    assert s.resend_api_key is not None
    resp = httpx.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {s.resend_api_key.get_secret_value()}", "Content-Type": "application/json"},
        json={"from": s.email_from, "to": [to], "subject": subject, "text": text, "html": body_html},
        timeout=s.email_timeout_seconds,
        follow_redirects=False,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return str(resp.json().get("id", ""))[:128]


def _post_brevo(to: str, subject: str, text: str, body_html: str) -> str:
    s = get_settings()
    assert s.brevo_api_key is not None and s.email_from
    resp = httpx.post(
        BREVO_URL,
        headers={"api-key": s.brevo_api_key.get_secret_value().strip(), "Content-Type": "application/json", "Accept": "application/json"},
        json={
            "sender": {"name": s.email_from_name, "email": s.email_from.strip()},
            "to": [{"email": to}],
            "subject": subject,
            "textContent": text,
            "htmlContent": body_html,
        },
        timeout=s.email_timeout_seconds,
        follow_redirects=False,
    )
    if resp.status_code >= 400:
        # Status and Brevo's error code only (e.g. "unauthorized"); never the key or the message.
        code = ""
        with contextlib.suppress(ValueError):
            code = str(resp.json().get("code", ""))[:40]
        raise RuntimeError(f"Brevo HTTP {resp.status_code} {code}".strip())
    return str(resp.json().get("messageId", ""))[:128]


def _send_smtp(to: str, subject: str, text: str, body_html: str) -> str:
    s = get_settings()
    assert s.smtp_password is not None and s.email_from
    msg = EmailMessage()
    msg["From"] = formataddr((s.email_from_name, s.email_from))
    msg["To"] = to
    msg["Subject"] = subject
    msg_id = make_msgid(domain=s.email_from.split("@")[-1])
    msg["Message-ID"] = msg_id
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")
    tls = ssl.create_default_context()
    user, pw = (s.smtp_username or "").strip(), s.smtp_password.get_secret_value().replace(" ", "").strip()
    if s.smtp_port == 465:
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.email_timeout_seconds, context=tls) as smtp:
            smtp.login(user, pw)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.email_timeout_seconds) as smtp:
            smtp.starttls(context=tls)
            smtp.login(user, pw)
            smtp.send_message(msg)
    return msg_id.strip("<>")[:128]


def _deliver(to: str, subject: str, text: str, body_html: str) -> str:
    provider = get_settings().email_provider
    send = {"smtp": _send_smtp, "brevo": _post_brevo}.get(provider, _post_resend)
    return send(to, subject, text, body_html)


transport = _deliver  # replaced in tests; tests never call the network

FOOTER = "This email was sent by FlowSense (a sandbox cash-flow app). This inbox isn't monitored, so replies won't be read. If you didn't ask for this, you can ignore it."


def send(db: Session, *, user_id: uuid.UUID | None, purpose: str, to: str, subject: str, lines: list[str], link: str | None = None) -> SendResult:
    s = get_settings()
    if not s.email_configured:
        db.add(EmailDelivery(user_id=user_id, purpose=purpose, provider="none", status="not_configured", detail="Email delivery is not configured"))
        db.flush()
        return SendResult("not_configured", "Email delivery is not configured on this server.")
    text = "\n\n".join(lines + ([link] if link else []) + [FOOTER])
    parts = "".join(
        f"<p style='font-size:28px;font-weight:700;letter-spacing:6px;margin:16px 0'>{html.escape(line)}</p>" if line.isdigit() and len(line) == 6 else f"<p>{html.escape(line)}</p>"
        for line in lines
    )
    if link:
        safe = html.escape(link, quote=True)
        parts += f'<p><a href="{safe}">{safe}</a></p>'
    body_html = parts + f"<p style='color:#666;font-size:12px'>{html.escape(FOOTER)}</p>"
    try:
        msg_id = transport(to, subject, text, body_html)
        db.add(EmailDelivery(user_id=user_id, purpose=purpose, provider=s.email_provider, status="sent", provider_message_id=msg_id or None))
        db.flush()
        return SendResult("sent")
    except Exception as exc:  # report the failure; never pretend the email went out
        # Class and a numeric reason only (never the message text, which can contain addresses): the OS
        # errno (101 = network unreachable when a host blocks SMTP ports) or the mail server's reply code.
        reason = getattr(exc, "smtp_code", None) or getattr(exc, "errno", None) or ""
        log.warning("Email delivery failed for purpose=%s via %s (%s %s)", purpose, s.email_provider, type(exc).__name__, reason)
        db.add(EmailDelivery(user_id=user_id, purpose=purpose, provider=s.email_provider, status="failed", detail=type(exc).__name__[:200]))
        db.flush()
        return SendResult("failed", "The email provider did not accept the message.")


def send_in_background(user_id: uuid.UUID | None, purpose: str, to: str, subject: str, lines: list[str], link: str | None = None) -> None:
    """Run after the HTTP response is sent (BackgroundTasks), so response time does not reveal
    whether an email went out. Uses its own database session."""
    from app.db import get_sessionmaker

    with get_sessionmaker()() as db:
        send(db, user_id=user_id, purpose=purpose, to=to, subject=subject, lines=lines, link=link)
        db.commit()


def app_link(path: str, token: str) -> str:
    base = (get_settings().public_app_url or "").rstrip("/")
    return f"{base}{path}#token={token}"  # fragment: never sent to servers or logged in access logs

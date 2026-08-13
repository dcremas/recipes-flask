"""Feedback delivery.

Mirrors the contact form on dustincremascoli.com: try SES, then SMTP, then
append the message to a file. Deliberately the same shape and the same
environment variables, so both sites are operated the same way.

The important property is the return value of `deliver()`: it is True only when a
transport actually accepted the message, so the page never tells a visitor their
message is "on its way" when it isn't. The file write happens on every failure
path, so a message is never silently lost — a delivery outage becomes a file to
read later rather than feedback that evaporated.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from flask import current_app, request


def enabled() -> bool:
    return bool(current_app.config.get("FEEDBACK_ENABLED"))


def _build_message(name: str, email: str, message: str) -> EmailMessage:
    cfg = current_app.config
    msg = EmailMessage()
    msg["Subject"] = f"recipes.dustincremascoli.com — feedback from {name}"
    msg["From"] = cfg["MAIL_FROM"] or cfg["SMTP_FROM"] or cfg["SMTP_USER"] or ""
    msg["To"] = cfg["MAIL_TO"]
    # So hitting reply in a mail client answers the visitor, not yourself.
    msg["Reply-To"] = email
    msg.set_content(
        f"From: {name} <{email}>\n"
        f"Sent: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
        f"{message}\n"
    )
    return msg


def _send_ses(msg: EmailMessage) -> bool:
    """Send through Amazon SES using the EC2 instance profile for credentials.

    boto3 is imported lazily so a local checkout without it still boots and
    still works — it just falls through to the file backend.
    """
    cfg = current_app.config
    if not cfg["MAIL_FROM"]:
        current_app.logger.warning(
            "SES: MAIL_FROM is unset; it must be an SES-verified identity"
        )
        return False
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        current_app.logger.warning("SES: boto3 is not installed")
        return False
    try:
        ses = boto3.client("ses", region_name=cfg["SES_REGION"])
        # Source and Destinations passed explicitly, as the main site does: SES
        # otherwise parses them out of the raw headers, and a Reply-To pointing
        # at the visitor makes that parsing the wrong thing to depend on.
        ses.send_raw_email(
            Source=msg["From"],
            Destinations=[msg["To"]],
            RawMessage={"Data": msg.as_bytes()},
        )
        return True
    except (BotoCoreError, ClientError):
        current_app.logger.exception("SES delivery failed")
        return False


def _send_smtp(msg: EmailMessage) -> bool:
    import smtplib

    cfg = current_app.config
    host = cfg["SMTP_HOST"]
    if not host:
        return False
    try:
        with smtplib.SMTP(host, cfg["SMTP_PORT"], timeout=15) as smtp:
            smtp.starttls()
            if cfg["SMTP_USER"]:
                smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"] or "")
            smtp.send_message(msg)
        return True
    except Exception:
        current_app.logger.exception("SMTP delivery failed")
        return False


def _persist(name: str, email: str, message: str) -> bool:
    """Append the message as one JSON line. Returns False if even this failed.

    The path must be somewhere the service can write — the systemd unit sets
    ProtectSystem=strict, so it has to sit under a ReadWritePaths entry
    (/var/lib/recipes). A path inside the app tree fails with EROFS.
    """
    record = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "name": name,
        "email": email,
        "message": message,
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
    }
    try:
        path = Path(current_app.config["MESSAGES_FILE"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        current_app.logger.exception("could not persist feedback message")
        return False


def deliver(name: str, email: str, message: str) -> bool:
    """SES, then SMTP, then the file. True only if a transport accepted it."""
    backend = current_app.config["MAIL_BACKEND"]
    msg = _build_message(name, email, message)

    if backend in ("auto", "ses") and _send_ses(msg):
        return True
    if backend in ("auto", "smtp") and _send_smtp(msg):
        return True

    if not _persist(name, email, message):
        # Nothing accepted it and it isn't on disk either. Log loudly with the
        # body included — this is the only remaining copy.
        current_app.logger.error(
            "FEEDBACK LOST — no transport and no file. from=%r <%s>: %s",
            name, email, message,
        )
    return False

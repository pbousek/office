"""Outgoing mail for account e-mails (password reset), configured from the environment.

    OFFICE_SMTP_HOST      relay host; empty = mail disabled (no "forgot password")
    OFFICE_SMTP_PORT      25 (default) / 587 / 465
    OFFICE_SMTP_USER      optional login
    OFFICE_SMTP_PASSWORD
    OFFICE_SMTP_FROM      sender address
"""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid


def _cfg(name: str, default: str = "") -> str:
    return os.environ.get(f"OFFICE_SMTP_{name}", default).strip()


def enabled() -> bool:
    return bool(_cfg("HOST") and _cfg("FROM"))


def send(to: str, subject: str, body: str):
    msg = EmailMessage()
    msg["From"] = _cfg("FROM")
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=_cfg("FROM").rpartition("@")[2] or None)
    msg.set_content(body)

    host, port = _cfg("HOST"), int(_cfg("PORT", "25"))
    context = ssl.create_default_context()
    if port == 465:
        smtp = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
    else:
        smtp = smtplib.SMTP(host, port, timeout=15)
    with smtp:
        smtp.ehlo()
        if port != 465 and smtp.has_extn("starttls"):
            smtp.starttls(context=context)
            smtp.ehlo()
        if _cfg("USER"):
            smtp.login(_cfg("USER"), _cfg("PASSWORD"))
        smtp.send_message(msg)

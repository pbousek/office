"""SMTP email sending for fakturace."""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders


def send_invoice_email(
    to: str,
    subject: str,
    body: str,
    pdf_bytes: bytes,
    filename: str,
    settings: dict,
):
    host = settings.get("smtp_host", "").strip()
    if not host:
        raise ValueError("SMTP host není nakonfigurován — nastavte v Nastavení.")

    port = int(settings.get("smtp_port") or 587)
    user = settings.get("smtp_user", "").strip()
    password = settings.get("smtp_pass", "")
    from_addr = settings.get("smtp_from", "").strip() or user

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    part = MIMEBase("application", "pdf")
    part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    msg.attach(part)

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)

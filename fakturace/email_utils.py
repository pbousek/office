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
    isdoc_bytes: bytes = None,
    isdoc_filename: str = None,
    report_bytes: bytes = None,
    report_filename: str = None,
):
    host = settings.get("smtp_host", "").strip()
    if not host:
        raise ValueError("SMTP host není nakonfigurován — nastavte v Nastavení.")

    port = int(settings.get("smtp_port") or 587)
    user = settings.get("smtp_user", "").strip()
    password = settings.get("smtp_pass", "")
    from_addr = settings.get("smtp_from", "").strip() or user

    bcc = settings.get("smtp_bcc", "").strip()

    to_list = [a.strip() for a in to.split(",") if a.strip()]

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    part = MIMEBase("application", "pdf")
    part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    msg.attach(part)

    if isdoc_bytes and isdoc_filename:
        ipart = MIMEBase("application", "octet-stream")
        ipart.set_payload(isdoc_bytes)
        encoders.encode_base64(ipart)
        ipart.add_header("Content-Disposition", f'attachment; filename="{isdoc_filename}"')
        msg.attach(ipart)

    if report_bytes and report_filename:
        rpart = MIMEBase("application", "pdf")
        rpart.set_payload(report_bytes)
        encoders.encode_base64(rpart)
        rpart.add_header("Content-Disposition", f'attachment; filename="{report_filename}"')
        msg.attach(rpart)

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        if user:
            smtp.login(user, password)
        rcpt = to_list + ([bcc] if bcc else [])
        smtp.send_message(msg, to_addrs=rcpt)

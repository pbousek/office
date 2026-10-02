"""TOTP (RFC 6238) — 6 digits, 30 s step, SHA-1: what authenticator apps expect."""
import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6
WINDOW = 1  # accept the previous/next step too (clock drift)


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step() -> int:
    return int(time.time()) // STEP


def match_step(secret: str, code: str) -> int | None:
    """Time step the code belongs to, or None if it matches none in the window."""
    code = "".join(code.split())
    if not (code.isdigit() and len(code) == DIGITS):
        return None
    now = current_step()
    for step in range(now - WINDOW, now + WINDOW + 1):
        if hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, username: str, issuer: str) -> str:
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP}"


def qr_svg(data: str) -> str:
    """QR code as inline SVG markup."""
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode()
    return svg[svg.index("<svg"):]

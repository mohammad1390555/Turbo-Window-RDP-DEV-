"""
Shared utility functions: formatting, security checks, QR codes, etc.
"""

import io
import re
import time
import string
import secrets
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

import qrcode
from telegram.error import BadRequest

from bot.config import RATE_LIMIT, VIP_LEVELS

logger = logging.getLogger("BOT.helpers")

_rate_tracker: Dict[int, List[float]] = defaultdict(list)


# ── Safe edit wrappers ────────────────────────────

async def safe_edit(msg_or_query, text: str, **kwargs):
    try:
        if hasattr(msg_or_query, "edit_message_text"):
            await msg_or_query.edit_message_text(text, **kwargs)
        else:
            await msg_or_query.edit_text(text, **kwargs)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def safe_edit_caption(q, caption: str, **kwargs):
    try:
        await q.edit_message_caption(caption=caption, **kwargs)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


# ── Security ──────────────────────────────────────

def rate_limit_check(uid: int) -> bool:
    now = time.time()
    calls = _rate_tracker[uid]
    calls[:] = [t for t in calls if now - t < 60.0]
    if len(calls) >= RATE_LIMIT:
        return True
    calls.append(now)
    return False


def sanitize_text(text: str, max_len: int = 500) -> str:
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:max_len]


def is_valid_tg_id(val: str) -> bool:
    return bool(re.match(r"^\d{5,15}$", val.strip()))


def generate_secure_code(length: int = 10) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def is_social_url(url: str) -> bool:
    return bool(
        re.search(
            r"(instagram\.com|instagr\.am|tiktok\.com|vm\.tiktok\.com"
            r"|twitter\.com|x\.com|facebook\.com|fb\.watch)",
            url,
        )
    )


def is_yt_url(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be|yt\.be)", url))


# ── Formatting ────────────────────────────────────

def fmt_time(iso: str) -> str:
    if not iso:
        return "N/A"
    try:
        return datetime.fromisoformat(iso).strftime("%Y/%m/%d %H:%M")
    except ValueError:
        return "N/A"


def fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    if b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    return f"{b / 1024 ** 3:.1f} GB"


def fmt_duration(secs: int) -> str:
    if not secs:
        return "N/A"
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def vip_level_info(user: dict) -> str:
    if not user.get("is_vip"):
        return "--"
    level = user.get("vip_level", "silver")
    info = VIP_LEVELS.get(level, {})
    name = info.get("name", level)
    if user.get("vip_expiry"):
        try:
            exp = datetime.fromisoformat(user["vip_expiry"])
            days_left = (exp - datetime.now()).days
            return f"{name} | {days_left} days left"
        except ValueError:
            pass
    return name


def make_progress_bar(pct: int, width: int = 10) -> str:
    filled = int(width * pct / 100)
    return "\u2588" * filled + "\u2591" * (width - filled)


# ── QR Code ───────────────────────────────────────

def generate_qr(text: str) -> io.BytesIO:
    qr = qrcode.QRCode(
        version=1,
        box_size=7,
        border=3,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1a1a2e", back_color="white")
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio

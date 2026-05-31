"""
/start command handler.
"""

from datetime import datetime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.config import WELCOME_MSG, REFERRAL_BONUS
from bot.keyboards import kb_main
from bot.helpers import fmt_size


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    db = context.bot_data["db"]

    args = context.args or []
    ref_code = args[0] if args else None

    u, referred_by = await db.create_user(
        user.id, user.username or "", user.full_name or "", ref_code
    )

    if referred_by:
        referrer = await db.get_user(referred_by)
        try:
            await context.bot.send_message(
                referred_by,
                f"*Referral success!*\n\n"
                f"*{user.full_name or user.username or 'New user'}* joined via your link!\n"
                f"*{REFERRAL_BONUS:,}* added to your wallet\n"
                f"Balance: *{(referrer['balance'] + REFERRAL_BONUS):,}*",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    if ref_code and ref_code.startswith("dl_"):
        file_uid = ref_code[3:]
        file_data = await db.get_file(file_uid)
        if file_data:
            if file_data.get("expires_at") and datetime.now() > datetime.fromisoformat(
                file_data["expires_at"]
            ):
                await update.message.reply_text("This link has expired.")
                return
            try:
                send_map = {
                    "document": context.bot.send_document,
                    "video": context.bot.send_video,
                    "audio": context.bot.send_audio,
                    "photo": context.bot.send_photo,
                    "voice": context.bot.send_voice,
                }
                fn = send_map.get(file_data["file_type"], context.bot.send_document)
                await fn(
                    user.id,
                    file_data["file_id"],
                    caption=(
                        f"*{file_data['file_name']}*\n"
                        f"Size: {fmt_size(file_data['file_size'])}"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                await update.message.reply_text("Failed to send file.")
            return

    role = u["role"] if u else "user"
    is_new = referred_by is not None
    text = (
        f"{'Welcome!' if is_new else 'Hello!'} *{user.full_name or user.username or 'Friend'}*\n\n"
        f"{WELCOME_MSG}\n\n"
        f"{'Your referrer got a bonus!' if is_new else ''}"
    )
    await update.message.reply_text(
        text.strip(),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_main(user.id, role),
    )

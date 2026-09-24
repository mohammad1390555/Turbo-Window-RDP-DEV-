"""
File-to-link handler.
"""

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import MAX_FILE_SIZE, BASE_DOWNLOAD_URL, STATE_WAITING_FILE
from bot.decorators import guard
from bot.helpers import safe_edit, fmt_size
from bot.keyboards import kb_back_main, kb_cancel


@guard()
async def cb_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await safe_edit(
        q,
        f"*File to Link*\n\nSend a file (max {MAX_FILE_SIZE} MB):",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_cancel(),
    )
    return STATE_WAITING_FILE


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = msg.from_user.id
    db = context.bot_data["db"]

    user = await db.get_user(uid)
    if user and user.get("banned"):
        return ConversationHandler.END

    fid = uid_unique = ftype = name = mime = None
    size = 0

    if msg.document:
        d = msg.document
        fid, uid_unique, ftype = d.file_id, d.file_unique_id, "document"
        name, size, mime = d.file_name or "file", d.file_size or 0, d.mime_type or "application/octet-stream"
    elif msg.video:
        v = msg.video
        fid, uid_unique, ftype = v.file_id, v.file_unique_id, "video"
        name, size, mime = v.file_name or "video.mp4", v.file_size or 0, v.mime_type or "video/mp4"
    elif msg.audio:
        a = msg.audio
        fid, uid_unique, ftype = a.file_id, a.file_unique_id, "audio"
        name, size, mime = a.file_name or "audio.mp3", a.file_size or 0, a.mime_type or "audio/mpeg"
    elif msg.photo:
        p = msg.photo[-1]
        fid, uid_unique, ftype = p.file_id, p.file_unique_id, "photo"
        name, size, mime = "photo.jpg", p.file_size or 0, "image/jpeg"
    elif msg.voice:
        v2 = msg.voice
        fid, uid_unique, ftype = v2.file_id, v2.file_unique_id, "voice"
        name, size, mime = "voice.ogg", v2.file_size or 0, "audio/ogg"
    else:
        await msg.reply_text("Unsupported file type.", reply_markup=kb_cancel())
        return STATE_WAITING_FILE

    if size and size > MAX_FILE_SIZE * 1024 * 1024:
        await msg.reply_text(
            f"File too large ({fmt_size(size)} > {MAX_FILE_SIZE} MB).",
            reply_markup=kb_cancel(),
        )
        return STATE_WAITING_FILE

    await db.save_file(uid_unique, fid, ftype, name, size, mime, uploader_id=uid)
    await db.add_log(uid, "file_upload", f"{name} ({fmt_size(size)})")

    bot_info = await context.bot.get_me()
    bot_link = f"https://t.me/{bot_info.username}?start=dl_{uid_unique}"
    stream_link = f"{BASE_DOWNLOAD_URL}/stream/{uid_unique}"

    text = (
        f"*File saved!*\n\n"
        f"Name: `{name}`\n"
        f"Size: {fmt_size(size)}\n"
        f"Expiry: 7 days\n\n"
        f"Telegram link:\n`{bot_link}`\n\n"
        f"Direct link:\n`{stream_link}`"
    )
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_back_main())
    return ConversationHandler.END

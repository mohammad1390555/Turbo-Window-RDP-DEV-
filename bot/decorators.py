"""
Guard decorator: ban check, rate limit, forced channel membership, permission check.
"""

from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.config import FORCE_CHANNEL, SUPER_ADMIN_ID
from bot.helpers import rate_limit_check


def guard(perm: str = None):
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            uid = update.effective_user.id if update.effective_user else 0
            db = context.bot_data["db"]

            user = await db.get_user(uid)
            if user and user.get("banned"):
                text = "Your account is suspended."
                if update.callback_query:
                    await update.callback_query.answer(text, show_alert=True)
                else:
                    await update.message.reply_text(text)
                return

            if rate_limit_check(uid):
                text = "Too many requests. Please wait."
                if update.callback_query:
                    await update.callback_query.answer(text, show_alert=True)
                else:
                    await update.message.reply_text(text)
                return

            if FORCE_CHANNEL and uid != SUPER_ADMIN_ID:
                role = await db.get_role(uid)
                if role not in ("admin", "super_admin"):
                    try:
                        member = await context.bot.get_chat_member(FORCE_CHANNEL, uid)
                        if member.status in ("left", "kicked"):
                            raise Exception
                    except Exception:
                        kb = InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                "Join Channel",
                                url=f"https://t.me/{FORCE_CHANNEL.lstrip('@')}",
                            ),
                            InlineKeyboardButton("I Joined", callback_data="check_join"),
                        ]])
                        text = f"Please join {FORCE_CHANNEL} first."
                        if update.callback_query:
                            await update.callback_query.answer(
                                "Join the channel first!", show_alert=True
                            )
                        else:
                            await update.message.reply_text(text, reply_markup=kb)
                        return

            if perm and not await db.has_perm(uid, perm):
                text = "Permission denied."
                if update.callback_query:
                    await update.callback_query.answer(text, show_alert=True)
                else:
                    await update.message.reply_text(text)
                return

            await db.update_last_seen(uid)
            return await func(update, context, *args, **kwargs)

        return wrapper
    return decorator

"""
Bot entry point: initializes DB, registers handlers, starts polling.
"""

import logging
import sys
from datetime import datetime

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from bot.config import (
    BOT_TOKEN,
    SUPER_ADMIN_ID,
    CONFIG,
    PROXY_UPDATE_INTERVAL,
    STATE_WAITING_YT_URL,
    STATE_WAITING_FILE,
    STATE_WAITING_RECEIPT,
    STATE_WAITING_DISCOUNT,
    STATE_WAITING_CONFIG_TEXT,
    STATE_DELETING_CONFIG,
    STATE_SET_COOKIE,
    STATE_BAN_USER,
    STATE_UNBAN_USER,
    STATE_SEARCHING_USER,
    STATE_BROADCASTING,
    STATE_GIVE_VIP,
    STATE_MANAGE_ADMIN,
    STATE_ADD_DISCOUNT,
    STATE_CHECKING_PROXY,
    STATE_ADMIN_WALLET_CHARGE,
)
from bot.database import Database
from bot.keyboards import kb_main
from bot.server import start_web_server

from bot.handlers.start import cmd_start
from bot.handlers.main_menu import (
    cb_main,
    cb_check_join,
    cb_help,
    cb_profile,
    cb_referral,
    cb_claim,
    cb_vip,
    cb_buy_vip,
    cb_receipt_prompt,
    handle_receipt,
    cb_apply_discount,
    handle_discount_code,
    cb_wallet,
    cb_wallet_buy_vip,
    cb_wallet_pay,
)
from bot.handlers.youtube import (
    cb_yt,
    handle_yt_url,
    cb_yt_quality,
    cb_yt_dl,
)
from bot.handlers.file import cb_file, handle_file
from bot.handlers.proxy import (
    cb_proxy_menu,
    cb_proxy_list,
    cb_proxy_copy,
    cb_proxy_stats,
    cb_proxy_refresh,
    cb_proxy_check,
    handle_proxy_check,
    cb_speed_test,
    cb_admin_proxy,
    cb_admin_proxy_clean,
    scheduled_proxy_update,
)
from bot.handlers.admin import (
    cb_admin,
    cmd_admin,
    cb_admin_configs,
    cb_add_config_prompt,
    handle_add_config,
    cb_del_config_prompt,
    handle_del_config,
    cb_list_configs,
    cb_admin_users,
    cb_ban_prompt,
    handle_ban_user,
    cb_unban_prompt,
    handle_unban_user,
    cb_search_user_prompt,
    handle_search_user,
    cb_banned_list,
    cb_admin_give_vip,
    handle_give_vip,
    cb_admin_broadcast,
    handle_broadcast,
    cb_admin_payments,
    cb_pay_action,
    cb_admin_discount,
    cb_add_discount_prompt,
    handle_add_discount,
    cb_super_admin,
    cb_add_admin_prompt,
    cb_remove_admin_prompt,
    handle_admin_action,
    cb_full_stats,
    cb_admin_backup,
    cb_set_cookie,
    handle_cookie,
    cmd_confirm_payment,
    cmd_reject_payment,
    cb_admin_wallet_charge,
    handle_wallet_charge,
)

# ── Logging ───────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(
            f"logs/bot_{datetime.now():%Y%m%d}.log", encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("BOT")


# ── Daily report ─────────────────────────────────
async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
    if not SUPER_ADMIN_ID:
        return
    db = context.bot_data["db"]
    try:
        from telegram.constants import ParseMode
        stats = await db.get_daily_stats()
        text = (
            f"*Daily Report -- {datetime.now().strftime('%Y/%m/%d')}*\n"
            f"--------------------\n"
            f"New users today: *{stats['new_users_today']}*\n"
            f"New users yesterday: {stats['new_users_yesterday']}\n"
            f"Active VIPs: {stats['active_vips']}\n"
            f"Payments today: {stats['payments_today']} ({stats['revenue_today']:,})\n"
            f"Downloads today: {stats['downloads_today']}\n"
            f"Configs today: {stats['claims_today']}\n"
            f"Total users: {stats['total_users']:,}"
        )
        await context.bot.send_message(SUPER_ADMIN_ID, text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Daily report error: {e}")


# ── Error handler ────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Unhandled error: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("An error occurred. Please try again.")
        except Exception:
            pass


# ── Cancel ───────────────────────────────────────
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    db = context.bot_data["db"]
    uid = update.effective_user.id if update.effective_user else 0
    role = await db.get_role(uid) if uid else "user"
    kb = kb_main(uid, role) if update.effective_user else None
    if update.message:
        await update.message.reply_text("Cancelled.", reply_markup=kb)
    elif update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text("Cancelled.", reply_markup=kb)
        except BadRequest:
            pass
    return ConversationHandler.END


# ── Post-init ────────────────────────────────────
async def post_init(app):
    db = Database()
    await db.init_db()
    app.bot_data["db"] = db

    await db.clean_expired_files()
    await start_web_server(app.bot, db)

    # daily report
    hour = CONFIG.get("daily_report_hour", 8)
    app.job_queue.run_daily(
        send_daily_report,
        time=datetime.now().replace(hour=hour, minute=0, second=0).time(),
    )

    # periodic proxy update
    app.job_queue.run_repeating(
        scheduled_proxy_update,
        interval=PROXY_UPDATE_INTERVAL * 60,
        first=10,
    )

    logger.info("Bot initialized!")


def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )
    app.add_error_handler(error_handler)

    # ── Commands ──────────────────────────────────
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.Regex(r"^/confirm_\d+$"), cmd_confirm_payment))
    app.add_handler(MessageHandler(filters.Regex(r"^/reject_\d+$"), cmd_reject_payment))

    # ── Callbacks ─────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_main, pattern="^main$"))
    app.add_handler(CallbackQueryHandler(cb_check_join, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(cb_help, pattern="^help$"))
    app.add_handler(CallbackQueryHandler(cb_profile, pattern="^profile$"))
    app.add_handler(CallbackQueryHandler(cb_referral, pattern="^referral$"))
    app.add_handler(CallbackQueryHandler(cb_claim, pattern="^claim$"))
    app.add_handler(CallbackQueryHandler(cb_vip, pattern="^vip$"))
    app.add_handler(CallbackQueryHandler(cb_buy_vip, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(cb_wallet, pattern="^wallet$"))
    app.add_handler(CallbackQueryHandler(cb_wallet_buy_vip, pattern="^wallet_buy_vip$"))
    app.add_handler(CallbackQueryHandler(cb_wallet_pay, pattern="^wallet_pay_"))
    app.add_handler(CallbackQueryHandler(cb_admin, pattern="^admin$"))
    app.add_handler(CallbackQueryHandler(cb_admin_configs, pattern="^admin_configs$"))
    app.add_handler(CallbackQueryHandler(cb_list_configs, pattern="^list_configs_(free|vip)$"))
    app.add_handler(CallbackQueryHandler(cb_admin_payments, pattern="^admin_payments$"))
    app.add_handler(CallbackQueryHandler(cb_admin_users, pattern="^admin_users$"))
    app.add_handler(CallbackQueryHandler(cb_banned_list, pattern="^banned_list$"))
    app.add_handler(CallbackQueryHandler(cb_admin_give_vip, pattern="^admin_give_vip$"))
    app.add_handler(CallbackQueryHandler(cb_admin_discount, pattern="^admin_discount$"))
    app.add_handler(CallbackQueryHandler(cb_add_discount_prompt, pattern="^add_discount_code$"))
    app.add_handler(CallbackQueryHandler(cb_super_admin, pattern="^super_admin$"))
    app.add_handler(CallbackQueryHandler(cb_full_stats, pattern="^full_stats$"))
    app.add_handler(CallbackQueryHandler(cb_admin_backup, pattern="^admin_backup$"))
    app.add_handler(CallbackQueryHandler(cb_yt_quality, pattern="^yt_q_"))
    app.add_handler(CallbackQueryHandler(cb_yt_dl, pattern="^yt_dl_(video|audio)$"))
    app.add_handler(CallbackQueryHandler(cb_pay_action, pattern=r"^pay_(confirm|reject)_\d+$"))

    # ── Proxy callbacks ───────────────────────────
    app.add_handler(CallbackQueryHandler(cb_proxy_menu, pattern="^proxy_menu$"))
    app.add_handler(CallbackQueryHandler(cb_proxy_list, pattern="^proxy_(http|socks4|socks5|all)$"))
    app.add_handler(CallbackQueryHandler(cb_proxy_copy, pattern="^proxy_copy_"))
    app.add_handler(CallbackQueryHandler(cb_proxy_stats, pattern="^proxy_stats$"))
    app.add_handler(CallbackQueryHandler(cb_proxy_refresh, pattern="^proxy_refresh$"))
    app.add_handler(CallbackQueryHandler(cb_speed_test, pattern="^speed_test$"))
    app.add_handler(CallbackQueryHandler(cb_admin_proxy, pattern="^admin_proxy$"))
    app.add_handler(CallbackQueryHandler(cb_admin_proxy_clean, pattern="^admin_proxy_clean$"))

    # ── Conversation handler ──────────────────────
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_yt, pattern="^yt$"),
            CallbackQueryHandler(cb_file, pattern="^file$"),
            CallbackQueryHandler(cb_receipt_prompt, pattern="^send_receipt$"),
            CallbackQueryHandler(cb_apply_discount, pattern="^apply_discount$"),
            CallbackQueryHandler(cb_add_config_prompt, pattern="^add_config$"),
            CallbackQueryHandler(cb_del_config_prompt, pattern="^del_config$"),
            CallbackQueryHandler(cb_set_cookie, pattern="^set_cookie$"),
            CallbackQueryHandler(cb_ban_prompt, pattern="^ban_user$"),
            CallbackQueryHandler(cb_unban_prompt, pattern="^unban_user$"),
            CallbackQueryHandler(cb_search_user_prompt, pattern="^search_user$"),
            CallbackQueryHandler(cb_admin_broadcast, pattern="^admin_broadcast$"),
            CallbackQueryHandler(cb_admin_give_vip, pattern="^admin_give_vip$"),
            CallbackQueryHandler(cb_add_admin_prompt, pattern="^add_admin$"),
            CallbackQueryHandler(cb_remove_admin_prompt, pattern="^remove_admin$"),
            CallbackQueryHandler(cb_add_discount_prompt, pattern="^add_discount_code$"),
            CallbackQueryHandler(cb_proxy_check, pattern="^proxy_check$"),
            CallbackQueryHandler(cb_admin_wallet_charge, pattern="^admin_wallet_charge$"),
        ],
        states={
            STATE_WAITING_YT_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_yt_url)],
            STATE_WAITING_FILE: [MessageHandler(filters.ALL & ~filters.COMMAND, handle_file)],
            STATE_WAITING_RECEIPT: [MessageHandler(filters.PHOTO, handle_receipt)],
            STATE_WAITING_DISCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_discount_code)],
            STATE_WAITING_CONFIG_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_config)],
            STATE_DELETING_CONFIG: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_del_config)],
            STATE_SET_COOKIE: [MessageHandler(filters.Document.ALL, handle_cookie)],
            STATE_BAN_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ban_user)],
            STATE_UNBAN_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unban_user)],
            STATE_SEARCHING_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_user)],
            STATE_BROADCASTING: [MessageHandler(filters.ALL & ~filters.COMMAND, handle_broadcast)],
            STATE_GIVE_VIP: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_give_vip)],
            STATE_MANAGE_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_action)],
            STATE_ADD_DISCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_discount)],
            STATE_CHECKING_PROXY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_proxy_check)],
            STATE_ADMIN_WALLET_CHARGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_wallet_charge)],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cmd_cancel, pattern="^main$"),
        ],
        allow_reentry=True,
        per_user=True,
        per_chat=True,
    )
    app.add_handler(conv)

    logger.info("Bot v4.0 running!")
    logger.info(f"Super Admin: {SUPER_ADMIN_ID}")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

"""
Main menu callback handlers: profile, help, referral, claim, VIP, wallet.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.config import (
    SUPER_ADMIN_ID,
    PAYMENT_CARD,
    REFERRAL_BONUS,
    VIP_LEVELS,
    WELCOME_MSG,
    STATE_WAITING_RECEIPT,
    STATE_WAITING_DISCOUNT,
    STATE_WALLET_CHARGE,
)
from bot.decorators import guard
from bot.helpers import safe_edit, fmt_time, vip_level_info, generate_qr
from bot.keyboards import kb_main, kb_back_main, kb_cancel


@guard()
async def cb_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    db = context.bot_data["db"]
    role = await db.get_role(uid)
    await safe_edit(
        q,
        "*Main Menu*\nSelect an option:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_main(uid, role),
    )


@guard()
async def cb_check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.config import FORCE_CHANNEL
    q = update.callback_query
    uid = q.from_user.id
    db = context.bot_data["db"]
    try:
        member = await context.bot.get_chat_member(FORCE_CHANNEL, uid)
        if member.status in ("left", "kicked"):
            await q.answer("You haven't joined yet!", show_alert=True)
            return
    except Exception:
        await q.answer("Error checking membership.", show_alert=True)
        return
    await q.answer("Membership confirmed!")
    role = await db.get_role(uid)
    await safe_edit(q, "Membership confirmed! You can now use the bot.", reply_markup=kb_main(uid, role))


@guard()
async def cb_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text = (
        "*Bot Guide*\n"
        "--------------------\n\n"
        "*Config:* Get a free VPN config every 6 hours\n\n"
        "*Download:* Send a YouTube/Instagram/TikTok link to download\n\n"
        "*File2Link:* Send any file, get a download link (7 day expiry)\n\n"
        "*VIP:* Premium configs, priority downloads, and more\n\n"
        "*Wallet:* Balance for VIP purchases\n\n"
        "*Referral:* Earn rewards for inviting friends\n\n"
        "*Proxy:* Get free HTTP/SOCKS proxies\n\n"
        "--------------------\n"
        "`/cancel` - Cancel any operation"
    )
    await safe_edit(q, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_back_main())


@guard()
async def cb_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    db = context.bot_data["db"]
    user = await db.get_user(uid)
    if not user:
        await safe_edit(q, "User not found.", reply_markup=kb_back_main())
        return
    role_map = {"super_admin": "Super Admin", "admin": "Admin", "user": "User"}
    role_text = role_map.get(user["role"], "User")
    vip_text = vip_level_info(user) if user["is_vip"] else "None"
    text = (
        f"*Your Profile*\n"
        f"--------------------\n"
        f"ID: `{uid}`\n"
        f"Name: {user['full_name'] or '--'}\n"
        f"Role: {role_text}\n"
        f"VIP: {vip_text}\n"
        f"Balance: *{user['balance']:,}*\n"
        f"Configs claimed: {user['total_claims']}\n"
        f"Referrals: {user['total_referrals']}\n"
        f"Total spent: {user.get('total_spent', 0):,}\n"
        f"Joined: {fmt_time(user['joined_at'])}\n"
        f"Last seen: {fmt_time(user['last_seen'])}"
    )
    await safe_edit(q, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_back_main())


@guard()
async def cb_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    db = context.bot_data["db"]
    user = await db.get_user(uid)
    bot_info = await context.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={user['referral_code']}"
    text = (
        f"*Referral System*\n"
        f"--------------------\n\n"
        f"For each friend who joins via your link:\n"
        f"*{REFERRAL_BONUS:,}* is added to your wallet\n\n"
        f"Your link:\n`{link}`\n\n"
        f"Successful referrals: *{user['total_referrals']}*\n"
        f"Wallet balance: *{user['balance']:,}*"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Share Link", switch_inline_query=f"Check out this bot: {link}")],
        [InlineKeyboardButton("Home", callback_data="main")],
    ])
    await safe_edit(q, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


@guard()
async def cb_claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    db = context.bot_data["db"]
    can, remaining = await db.can_claim(uid)
    if not can:
        h, m = divmod(remaining, 3600)
        await q.answer(f"Wait {h}h {m // 60}m", show_alert=True)
        return

    is_vip = await db.is_vip(uid)
    category = "vip" if is_vip else "free"
    configs = await db.get_active_configs(category, limit=3)

    if not configs:
        if is_vip:
            configs = await db.get_active_configs("free", limit=3)
        if not configs:
            await safe_edit(q, "No configs available right now.", reply_markup=kb_back_main())
            return

    await db.update_claim(uid)
    lines = ["*Your Configs:*\n--------------------\n"]
    for c in configs:
        await db.increment_usage(c["id"])
        await db.add_log(uid, "claim_config", f"config_id={c['id']}")
        lines.append(f"`{c['config_text']}`\n")

    await safe_edit(q, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_back_main())


@guard()
async def cb_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lines = ["*VIP Plans*\n--------------------\n"]
    btns = []
    for key, info in VIP_LEVELS.items():
        lines.append(
            f"*{info['name']}*\n"
            f"Price: {info['price']:,} | Duration: {info['days']} days\n"
            f"Max quality: {info['max_yt_quality']}p | Configs: {info['max_configs']}\n"
        )
        btns.append(InlineKeyboardButton(f"Buy {info['name']}", callback_data=f"buy_{key}"))

    kb_rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
    kb_rows.append([InlineKeyboardButton("Home", callback_data="main")])
    await safe_edit(
        q, "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb_rows),
    )


@guard()
async def cb_buy_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    plan = q.data.replace("buy_", "")
    info = VIP_LEVELS.get(plan)
    if not info:
        await safe_edit(q, "Invalid plan.", reply_markup=kb_back_main())
        return
    context.user_data["vip_plan"] = plan
    context.user_data["vip_price"] = info["price"]
    text = (
        f"*{info['name']}*\n"
        f"--------------------\n"
        f"Price: *{info['price']:,}*\n"
        f"Duration: {info['days']} days\n\n"
        f"Card: `{PAYMENT_CARD}`\n\n"
        f"After payment, send your receipt photo."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Send Receipt", callback_data="send_receipt")],
        [InlineKeyboardButton("Apply Discount", callback_data="apply_discount")],
        [InlineKeyboardButton("Home", callback_data="main")],
    ])
    await safe_edit(q, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


@guard()
async def cb_receipt_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_edit(q, "Send your payment receipt (photo):", reply_markup=kb_cancel())
    return STATE_WAITING_RECEIPT


async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = context.bot_data["db"]
    if not update.message.photo:
        await update.message.reply_text("Please send a photo.", reply_markup=kb_cancel())
        return STATE_WAITING_RECEIPT
    plan = context.user_data.get("vip_plan", "silver")
    price = context.user_data.get("vip_price", VIP_LEVELS.get(plan, {}).get("price", 0))
    discount = context.user_data.get("discount_code")
    receipt_id = update.message.photo[-1].file_id
    pay_id = await db.create_payment(uid, price, plan, receipt_id, discount)
    if discount:
        await db.use_discount(discount)
    await db.add_log(uid, "payment_submit", f"plan={plan} amount={price} id={pay_id}")
    await update.message.reply_text(
        f"Receipt submitted (#{pay_id}). Awaiting admin review.",
        reply_markup=kb_back_main(),
    )
    context.user_data.pop("vip_plan", None)
    context.user_data.pop("vip_price", None)
    context.user_data.pop("discount_code", None)
    from telegram.ext import ConversationHandler
    return ConversationHandler.END


@guard()
async def cb_apply_discount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_edit(q, "Enter your discount code:", reply_markup=kb_cancel())
    return STATE_WAITING_DISCOUNT


async def handle_discount_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.helpers import sanitize_text
    from telegram.ext import ConversationHandler

    uid = update.effective_user.id
    db = context.bot_data["db"]
    code = sanitize_text(update.message.text, 30).strip().upper()
    disc = await db.validate_discount(code)
    if not disc:
        await update.message.reply_text("Invalid or expired code.", reply_markup=kb_cancel())
        return STATE_WAITING_DISCOUNT
    plan = context.user_data.get("vip_plan", "silver")
    original = VIP_LEVELS.get(plan, {}).get("price", 0)
    new_price = int(original * (100 - disc["percent"]) / 100)
    context.user_data["vip_price"] = new_price
    context.user_data["discount_code"] = code
    await update.message.reply_text(
        f"Discount applied! {disc['percent']}% off\n"
        f"Original: {original:,} -> New: *{new_price:,}*\n\n"
        f"Now send your receipt photo.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_cancel(),
    )
    return STATE_WAITING_RECEIPT


@guard()
async def cb_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    db = context.bot_data["db"]
    user = await db.get_user(uid)
    history = await db.get_wallet_history(uid, limit=5)
    lines = [
        f"*Wallet*\n--------------------\n",
        f"Balance: *{user['balance']:,}*\n",
    ]
    if history:
        lines.append("\nRecent transactions:")
        for tx in history:
            icon = "+" if tx["type"] == "credit" else "-"
            lines.append(f"  {icon}{abs(tx['amount']):,} | {tx['description'][:30]}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Buy VIP with Wallet", callback_data="wallet_buy_vip")],
        [InlineKeyboardButton("Home", callback_data="main")],
    ])
    await safe_edit(q, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


@guard()
async def cb_wallet_buy_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    btns = []
    for key, info in VIP_LEVELS.items():
        btns.append(
            InlineKeyboardButton(
                f"{info['name']} ({info['price']:,})",
                callback_data=f"wallet_pay_{key}",
            )
        )
    kb_rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
    kb_rows.append([InlineKeyboardButton("Back", callback_data="wallet")])
    await safe_edit(
        q, "*Buy VIP with wallet balance:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb_rows),
    )


@guard()
async def cb_wallet_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    db = context.bot_data["db"]
    plan = q.data.replace("wallet_pay_", "")
    info = VIP_LEVELS.get(plan)
    if not info:
        await q.answer("Invalid plan.", show_alert=True)
        return
    ok = await db.deduct_balance(uid, info["price"], f"VIP purchase ({info['name']})")
    if not ok:
        await q.answer("Insufficient balance!", show_alert=True)
        return
    await db.give_vip(uid, info["days"], plan)
    await db.add_log(uid, "wallet_vip", f"plan={plan} price={info['price']}")
    await q.answer(f"VIP {info['name']} activated!")
    await safe_edit(
        q,
        f"*VIP Activated!*\n\n"
        f"Plan: {info['name']}\n"
        f"Duration: {info['days']} days",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_back_main(),
    )

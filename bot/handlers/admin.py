"""
Admin panel handlers: config management, user management, broadcast,
VIP grants, discount codes, backup, cookie, stats.
"""

import os
import asyncio
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import (
    SUPER_ADMIN_ID,
    COOKIE_FILE,
    VIP_LEVELS,
    CONFIG,
    STATE_WAITING_CONFIG_TEXT,
    STATE_DELETING_CONFIG,
    STATE_BROADCASTING,
    STATE_SET_COOKIE,
    STATE_BAN_USER,
    STATE_UNBAN_USER,
    STATE_SEARCHING_USER,
    STATE_GIVE_VIP,
    STATE_MANAGE_ADMIN,
    STATE_ADD_DISCOUNT,
)
from bot.decorators import guard
from bot.helpers import safe_edit, sanitize_text, is_valid_tg_id, fmt_time, make_progress_bar
from bot.keyboards import kb_admin, kb_cancel, kb_back_main


@guard()
async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id if q else update.effective_user.id
    db = context.bot_data["db"]
    role = await db.get_role(uid)
    if role not in ("admin", "super_admin") and uid != SUPER_ADMIN_ID:
        if q:
            await q.answer("Permission denied.", show_alert=True)
        return
    if q:
        await q.answer()
    stats = await db.get_stats()
    text = (
        "*Admin Panel*\n"
        "--------------------\n"
        f"Users: {stats['total_users']:,} | Today: {stats['today_users']:,}\n"
        f"VIP: {stats['total_vips']:,} | Banned: {stats['total_banned']:,}\n"
        f"Revenue: {stats['total_revenue']:,}\n"
        f"Pending payments: {stats['pending_payments']:,}\n"
        f"Free configs: {stats['total_configs_free']:,}\n"
        f"VIP configs: {stats['total_configs_vip']:,}"
    )
    if q:
        await safe_edit(q, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin(uid))
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin(uid))


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if user and user.get("banned"):
        return
    await cb_admin(update, context)


# ── Config management ─────────────────────────────

@guard(perm="can_manage_configs")
async def cb_admin_configs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.bot_data["db"]
    stats = await db.get_stats()
    text = (
        "*Config Management*\n\n"
        f"Free: {stats['total_configs_free']}\n"
        f"VIP: {stats['total_configs_vip']}"
    )
    await safe_edit(q, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("Add", callback_data="add_config")],
        [InlineKeyboardButton("Free List", callback_data="list_configs_free"),
         InlineKeyboardButton("VIP List", callback_data="list_configs_vip")],
        [InlineKeyboardButton("Delete", callback_data="del_config")],
        [InlineKeyboardButton("Back", callback_data="admin")],
    ]))


@guard(perm="can_manage_configs")
async def cb_add_config_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_edit(
        q,
        "*Add Config*\n\n"
        "Format:\n`config_text | category | remark`\n\n"
        "Example:\n`vmess://base64 | vip | US server`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_cancel(),
    )
    return STATE_WAITING_CONFIG_TEXT


async def handle_add_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = context.bot_data["db"]
    if not await db.has_perm(uid, "can_manage_configs"):
        return ConversationHandler.END
    raw = sanitize_text(update.message.text, 5000)
    parts = [p.strip() for p in raw.split("|")]
    config_text = parts[0]
    category = parts[1].lower() if len(parts) > 1 and parts[1].lower() in ("free", "vip") else "free"
    remark = parts[2] if len(parts) > 2 else ""

    cfg_id = await db.add_config(config_text, category, remark)
    await db.add_log(uid, "add_config", f"id={cfg_id} cat={category}")
    await update.message.reply_text(
        f"Config #{cfg_id} added ({category}).",
        reply_markup=kb_admin(uid),
    )
    return ConversationHandler.END


@guard(perm="can_manage_configs")
async def cb_del_config_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_edit(q, "Enter config ID to delete:", reply_markup=kb_cancel())
    return STATE_DELETING_CONFIG


async def handle_del_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = context.bot_data["db"]
    if not await db.has_perm(uid, "can_manage_configs"):
        return ConversationHandler.END
    raw = sanitize_text(update.message.text, 20)
    if not raw.isdigit():
        await update.message.reply_text("Enter a valid numeric ID.", reply_markup=kb_cancel())
        return STATE_DELETING_CONFIG
    cfg_id = int(raw)
    ok = await db.delete_config(cfg_id)
    if ok:
        await db.add_log(uid, "del_config", f"id={cfg_id}")
        await update.message.reply_text(f"Config #{cfg_id} deleted.", reply_markup=kb_admin(uid))
    else:
        await update.message.reply_text("Config not found.", reply_markup=kb_admin(uid))
    return ConversationHandler.END


@guard(perm="can_manage_configs")
async def cb_list_configs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.bot_data["db"]
    cat = "free" if "free" in q.data else "vip"
    configs = await db.get_all_configs(limit=10, category=cat)
    if not configs:
        await safe_edit(q, f"No {cat} configs.", reply_markup=kb_admin(q.from_user.id))
        return
    lines = [f"*{cat.upper()} Configs:*\n"]
    for c in configs:
        lines.append(f"#{c['id']} | {c['protocol']} | used: {c['usage_count']} | {c['remark'][:30]}")
    await safe_edit(q, "\n".join(lines)[:4000], parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin(q.from_user.id))


# ── User management ──────────────────────────────

@guard()
async def cb_admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Search User", callback_data="search_user")],
        [InlineKeyboardButton("Ban User", callback_data="ban_user"),
         InlineKeyboardButton("Unban User", callback_data="unban_user")],
        [InlineKeyboardButton("Banned List", callback_data="banned_list")],
        [InlineKeyboardButton("Back", callback_data="admin")],
    ])
    await safe_edit(q, "*User Management*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


@guard(perm="can_block_users")
async def cb_ban_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_edit(q, "Enter user ID to ban:", reply_markup=kb_cancel())
    return STATE_BAN_USER


async def handle_ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = context.bot_data["db"]
    if not await db.has_perm(uid, "can_block_users"):
        return ConversationHandler.END
    raw = sanitize_text(update.message.text, 60)
    parts = raw.split(maxsplit=1)
    if not parts[0].isdigit():
        await update.message.reply_text("Enter a valid user ID.", reply_markup=kb_cancel())
        return STATE_BAN_USER
    target = int(parts[0])
    reason = parts[1] if len(parts) > 1 else ""
    await db.ban_user(target, reason)
    await db.add_log(uid, "ban_user", f"target={target} reason={reason}")
    await update.message.reply_text(f"User `{target}` banned.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin(uid))
    return ConversationHandler.END


@guard(perm="can_block_users")
async def cb_unban_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_edit(q, "Enter user ID to unban:", reply_markup=kb_cancel())
    return STATE_UNBAN_USER


async def handle_unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = context.bot_data["db"]
    if not await db.has_perm(uid, "can_block_users"):
        return ConversationHandler.END
    raw = sanitize_text(update.message.text, 20).strip()
    if not raw.isdigit():
        await update.message.reply_text("Enter a valid user ID.", reply_markup=kb_cancel())
        return STATE_UNBAN_USER
    target = int(raw)
    ok = await db.unban_user(target)
    if ok:
        await db.add_log(uid, "unban_user", f"target={target}")
        await update.message.reply_text(f"User `{target}` unbanned.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin(uid))
    else:
        await update.message.reply_text("User not found.", reply_markup=kb_admin(uid))
    return ConversationHandler.END


@guard()
async def cb_search_user_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_edit(q, "Enter user ID or username to search:", reply_markup=kb_cancel())
    return STATE_SEARCHING_USER


async def handle_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.bot_data["db"]
    uid = update.effective_user.id
    raw = sanitize_text(update.message.text, 60)
    users = await db.search_user(raw)
    if not users:
        await update.message.reply_text("No users found.", reply_markup=kb_admin(uid))
        return ConversationHandler.END
    lines = ["*Search Results:*\n"]
    for u in users:
        lines.append(
            f"ID: `{u['telegram_id']}` | @{u['username'] or '--'}\n"
            f"Name: {u['full_name'] or '--'} | Role: {u['role']}\n"
            f"VIP: {'Yes' if u['is_vip'] else 'No'} | Balance: {u['balance']:,}\n"
        )
    await update.message.reply_text("\n".join(lines)[:4000], parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin(uid))
    return ConversationHandler.END


@guard()
async def cb_banned_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.bot_data["db"]
    banned = await db.get_banned_users(limit=20)
    if not banned:
        await safe_edit(q, "No banned users.", reply_markup=kb_admin(q.from_user.id))
        return
    lines = ["*Banned Users:*\n"]
    for u in banned:
        lines.append(f"`{u['telegram_id']}` @{u['username'] or '--'} | {u.get('ban_reason', '')[:30]}")
    await safe_edit(q, "\n".join(lines)[:4000], parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin(q.from_user.id))


# ── VIP grant ────────────────────────────────────

@guard()
async def cb_admin_give_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_edit(
        q,
        "*Give VIP*\n\nFormat: `user_id days [level]`\n\nExample: `123456 30 gold`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_cancel(),
    )
    return STATE_GIVE_VIP


async def handle_give_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = context.bot_data["db"]
    if not await db.has_perm(uid, "can_give_vip"):
        return ConversationHandler.END
    raw = sanitize_text(update.message.text, 60)
    parts = raw.split()
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        await update.message.reply_text("Format: `user_id days [level]`", parse_mode=ParseMode.MARKDOWN)
        return STATE_GIVE_VIP
    target, days = int(parts[0]), int(parts[1])
    level = parts[2].lower() if len(parts) > 2 and parts[2].lower() in VIP_LEVELS else "silver"
    if days <= 0 or days > 3650:
        await update.message.reply_text("Days must be between 1 and 3650.")
        return STATE_GIVE_VIP
    if not await db.get_user(target):
        await update.message.reply_text(f"User {target} not found.")
        return ConversationHandler.END
    await db.give_vip(target, days, level)
    await db.add_log(uid, "give_vip", f"target={target} days={days} level={level}")
    level_info = VIP_LEVELS.get(level, {})
    try:
        await context.bot.send_message(
            target,
            f"*Congratulations!*\n\nYou received {days} days of VIP {level_info.get('name', level)}!",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass
    await update.message.reply_text(
        f"Granted {days} days VIP {level_info.get('name', level)} to `{target}`.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_admin(uid),
    )
    return ConversationHandler.END


# ── Broadcast ────────────────────────────────────

@guard(perm="can_broadcast")
async def cb_admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_edit(q, "*Broadcast*\n\nSend your message (text, photo, video):", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_cancel())
    return STATE_BROADCASTING


async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = context.bot_data["db"]
    if not await db.has_perm(uid, "can_broadcast"):
        return ConversationHandler.END
    users = await db.get_all_users()
    total = len(users)
    if not total:
        await update.message.reply_text("No users in database.")
        return ConversationHandler.END
    msg = update.message
    status = await msg.reply_text(f"Broadcasting to {total:,} users...")
    success = failed = 0
    for i, user_id in enumerate(users):
        try:
            if msg.text:
                await context.bot.send_message(user_id, msg.text, parse_mode=ParseMode.HTML)
            elif msg.photo:
                await context.bot.send_photo(user_id, msg.photo[-1].file_id, caption=msg.caption or "", parse_mode=ParseMode.HTML)
            elif msg.video:
                await context.bot.send_video(user_id, msg.video.file_id, caption=msg.caption or "", parse_mode=ParseMode.HTML)
            elif msg.document:
                await context.bot.send_document(user_id, msg.document.file_id, caption=msg.caption or "", parse_mode=ParseMode.HTML)
            success += 1
        except Exception:
            failed += 1
        if (i + 1) % 50 == 0:
            try:
                pct = int((i + 1) / total * 100)
                bar = make_progress_bar(pct)
                await safe_edit(status, f"{bar} {pct}%\nSent: {success} | Failed: {failed}")
            except Exception:
                pass
        await asyncio.sleep(CONFIG.get("max_broadcast_delay", 0.05))
    await safe_edit(
        status,
        f"*Broadcast complete!*\n\n"
        f"Total: {total:,}\nSent: {success:,}\nFailed: {failed:,}\n"
        f"Success rate: {success / total * 100:.1f}%",
        parse_mode=ParseMode.MARKDOWN,
    )
    await db.add_broadcast(uid, msg.text or msg.caption or "", success, failed)
    return ConversationHandler.END


# ── Payments ─────────────────────────────────────

@guard(perm="can_manage_payments")
async def cb_admin_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    db = context.bot_data["db"]
    pays = await db.get_pending_payments()
    if not pays:
        await safe_edit(q, "No pending payments.", reply_markup=kb_admin(uid))
        return
    lines = [f"*Pending Payments ({len(pays)}):*\n--------------------"]
    for p in pays[:10]:
        uname = f"@{p['username']}" if p["username"] else p["full_name"] or str(p["user_id"])
        lines.append(
            f"#{p['id']} | {uname}\n"
            f"{VIP_LEVELS.get(p['plan'], {}).get('name', p['plan'])} | "
            f"{p['amount']:,}"
            + (f" | code: {p.get('discount_code', '')}" if p.get("discount_code") else "")
            + f"\n/confirm\\_{p['id']}  /reject\\_{p['id']}"
        )
    await safe_edit(q, "\n\n".join(lines)[:4000], parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin(uid))


@guard(perm="can_manage_payments")
async def cb_pay_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    db = context.bot_data["db"]
    parts = q.data.split("_")
    action = parts[1]
    pay_id = int(parts[2])

    if action == "confirm":
        pay = await db.confirm_payment(pay_id, uid)
        if not pay:
            await q.answer("Not found or already reviewed.", show_alert=True)
            return
        try:
            await context.bot.send_message(pay["user_id"], "Payment confirmed! VIP activated!")
        except Exception:
            pass
        await q.answer(f"Payment #{pay_id} confirmed.")
    else:
        pay = await db.reject_payment(pay_id, uid)
        if not pay:
            await q.answer("Not found.", show_alert=True)
            return
        try:
            await context.bot.send_message(pay["user_id"], "Payment rejected.")
        except Exception:
            pass
        await q.answer(f"Payment #{pay_id} rejected.")

    await cb_admin_payments(update, context)


# ── Discount codes ───────────────────────────────

@guard()
async def cb_admin_discount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.bot_data["db"]
    codes = await db.list_discount_codes()
    lines = ["*Discount Codes:*\n"]
    if codes:
        for c in codes:
            lines.append(f"`{c['code']}` | {c['percent']}% | used: {c['used_count']}/{c['max_uses'] or 'unlimited'}")
    else:
        lines.append("No discount codes.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Add Code", callback_data="add_discount_code")],
        [InlineKeyboardButton("Back", callback_data="admin")],
    ])
    await safe_edit(q, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


@guard()
async def cb_add_discount_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_edit(
        q,
        "*Add Discount Code*\n\nFormat: `CODE PERCENT MAX_USES DAYS`\n\nExample: `SAVE20 20 100 30`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_cancel(),
    )
    return STATE_ADD_DISCOUNT


async def handle_add_discount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = context.bot_data["db"]
    raw = sanitize_text(update.message.text, 100)
    parts = raw.split()
    if len(parts) < 2:
        await update.message.reply_text("Format: `CODE PERCENT [MAX_USES] [DAYS]`", parse_mode=ParseMode.MARKDOWN)
        return STATE_ADD_DISCOUNT
    code = parts[0].upper()
    try:
        percent = int(parts[1])
        max_uses = int(parts[2]) if len(parts) > 2 else 0
        days = int(parts[3]) if len(parts) > 3 else 30
    except ValueError:
        await update.message.reply_text("Invalid numbers.", reply_markup=kb_cancel())
        return STATE_ADD_DISCOUNT
    if percent < 1 or percent > 100:
        await update.message.reply_text("Percent must be 1-100.")
        return STATE_ADD_DISCOUNT
    ok = await db.add_discount_code(code, percent, max_uses, days)
    if ok:
        await db.add_log(uid, "add_discount", f"code={code} percent={percent}")
        await update.message.reply_text(f"Discount `{code}` ({percent}%) created.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin(uid))
    else:
        await update.message.reply_text("Code already exists.", reply_markup=kb_admin(uid))
    return ConversationHandler.END


# ── Super admin ──────────────────────────────────

@guard()
async def cb_super_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    if uid != SUPER_ADMIN_ID:
        await q.answer("Super admin only!", show_alert=True)
        return
    await q.answer()
    db = context.bot_data["db"]
    conn = await db.connect()
    async with conn.execute(
        "SELECT u.telegram_id, u.username, u.full_name FROM users u "
        "JOIN admin_permissions ap ON u.telegram_id=ap.admin_id WHERE u.role='admin'"
    ) as cur:
        admins = [dict(r) for r in await cur.fetchall()]
    lines = ["*Admin Management*\n--------------------"]
    if admins:
        for a in admins:
            lines.append(f"`{a['telegram_id']}` @{a['username'] or '--'} | {a['full_name'] or '--'}")
    else:
        lines.append("No admins.")
    await safe_edit(q, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("Add Admin", callback_data="add_admin")],
        [InlineKeyboardButton("Remove Admin", callback_data="remove_admin")],
        [InlineKeyboardButton("Back", callback_data="admin")],
    ]))


@guard()
async def cb_add_admin_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != SUPER_ADMIN_ID:
        await q.answer("Not allowed.", show_alert=True)
        return
    context.user_data["admin_action"] = "add_admin"
    await safe_edit(q, "*Add Admin*\n\nEnter numeric user ID:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_cancel())
    return STATE_MANAGE_ADMIN


@guard()
async def cb_remove_admin_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != SUPER_ADMIN_ID:
        await q.answer("Not allowed.", show_alert=True)
        return
    context.user_data["admin_action"] = "remove_admin"
    await safe_edit(q, "*Remove Admin*\n\nEnter numeric user ID:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_cancel())
    return STATE_MANAGE_ADMIN


async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != SUPER_ADMIN_ID:
        return ConversationHandler.END
    db = context.bot_data["db"]
    raw = sanitize_text(update.message.text, 50)
    if not is_valid_tg_id(raw):
        await update.message.reply_text("Enter a valid ID.", reply_markup=kb_cancel())
        return STATE_MANAGE_ADMIN
    target = int(raw.strip())
    action = context.user_data.get("admin_action", "add_admin")
    if action == "add_admin":
        if not await db.get_user(target):
            await update.message.reply_text(f"User {target} not registered.")
            return STATE_MANAGE_ADMIN
        conn = await db.connect()
        await conn.execute("UPDATE users SET role='admin' WHERE telegram_id=?", (target,))
        await conn.execute(
            "INSERT OR IGNORE INTO admin_permissions "
            "(admin_id, can_manage_configs, can_manage_payments, can_broadcast, can_block_users, can_give_vip) "
            "VALUES (?, 0, 0, 0, 0, 0)",
            (target,),
        )
        await conn.commit()
        try:
            await context.bot.send_message(target, "You have been promoted to admin!")
        except Exception:
            pass
        await update.message.reply_text(f"`{target}` is now admin.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin(uid))
    else:
        ok = await db.remove_admin(target)
        if not ok:
            await update.message.reply_text(f"Admin {target} not found.")
        else:
            try:
                await context.bot.send_message(target, "Your admin access has been revoked.")
            except Exception:
                pass
            await update.message.reply_text(f"Admin `{target}` removed.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin(uid))
    context.user_data.pop("admin_action", None)
    return ConversationHandler.END


# ── Full stats ───────────────────────────────────

@guard()
async def cb_full_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != SUPER_ADMIN_ID:
        await q.answer("Super admin only!", show_alert=True)
        return
    await q.answer()
    db = context.bot_data["db"]
    stats = await db.get_stats()
    conn = await db.connect()
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    async with conn.execute("SELECT COUNT(*) FROM users WHERE joined_at>?", (week_ago,)) as c:
        week_users = (await c.fetchone())[0]
    async with conn.execute("SELECT COUNT(*) FROM user_logs WHERE action='yt_download' AND created_at>?", (week_ago,)) as c:
        week_dl = (await c.fetchone())[0]
    async with conn.execute("SELECT id, protocol, remark, usage_count FROM configs ORDER BY usage_count DESC LIMIT 3") as c:
        top_configs = [dict(r) for r in await c.fetchall()]
    async with conn.execute("SELECT telegram_id, username, total_referrals FROM users ORDER BY total_referrals DESC LIMIT 3") as c:
        top_referrers = [dict(r) for r in await c.fetchall()]

    proxy_stats = await db.get_proxy_stats()

    text = (
        f"*Full Statistics*\n"
        f"--------------------\n"
        f"Users: {stats['total_users']:,} | Today: {stats['today_users']:,} | Week: {week_users:,}\n"
        f"VIP: {stats['total_vips']:,} | Banned: {stats['total_banned']:,}\n\n"
        f"Downloads: {stats['total_downloads']:,} | Week: {week_dl:,}\n"
        f"Configs claimed: {stats['total_claims']:,}\n\n"
        f"Revenue: {stats['total_revenue']:,}\n"
        f"Payments: {stats['total_payments']:,} | Pending: {stats['pending_payments']:,}\n"
        f"Total wallet balance: {stats['total_wallet_balance']:,}\n\n"
        f"Proxies: {proxy_stats['alive']} alive / {proxy_stats['total']} total\n\n"
        f"Top configs:\n"
    )
    for i, c in enumerate(top_configs, 1):
        text += f"{i}. #{c['id']} {c['protocol'].upper()} -- {c['usage_count']} uses\n"
    text += "\nTop referrers:\n"
    for i, r in enumerate(top_referrers, 1):
        text += f"{i}. @{r['username'] or r['telegram_id']} -- {r['total_referrals']} referrals\n"

    await safe_edit(q, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin(q.from_user.id))


# ── Backup ───────────────────────────────────────

@guard()
async def cb_admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != SUPER_ADMIN_ID:
        await q.answer("Super admin only!", show_alert=True)
        return
    await q.answer("Creating backup...")
    db = context.bot_data["db"]
    dst = db.backup()
    await db.add_log(q.from_user.id, "db_backup", dst)
    with open(dst, "rb") as f:
        await context.bot.send_document(
            q.from_user.id, document=f, filename=os.path.basename(dst),
            caption=f"DB Backup | {datetime.now():%Y/%m/%d %H:%M}",
        )
    await safe_edit(q, "Backup sent!", reply_markup=kb_admin(q.from_user.id))


# ── Cookie ───────────────────────────────────────

@guard()
async def cb_set_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != SUPER_ADMIN_ID:
        await q.answer("Super admin only!", show_alert=True)
        return
    await safe_edit(
        q,
        "*Set YouTube Cookie*\n\nSend `cookies.txt` file (Netscape format).",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_cancel(),
    )
    return STATE_SET_COOKIE


async def handle_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID:
        return ConversationHandler.END
    db = context.bot_data["db"]
    if not update.message.document:
        await update.message.reply_text("Send a .txt file.")
        return STATE_SET_COOKIE
    if not update.message.document.file_name.lower().endswith(".txt"):
        await update.message.reply_text("Only .txt files accepted.")
        return STATE_SET_COOKIE
    tg_file = await context.bot.get_file(update.message.document.file_id)
    await tg_file.download_to_drive(COOKIE_FILE)
    await db.add_log(update.effective_user.id, "set_cookie", COOKIE_FILE)
    await update.message.reply_text("Cookie saved!", reply_markup=kb_admin(update.effective_user.id))
    return ConversationHandler.END


# ── Payment commands ─────────────────────────────

async def cmd_confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = context.bot_data["db"]
    if not await db.has_perm(uid, "can_manage_payments"):
        await update.message.reply_text("Permission denied.")
        return
    try:
        pay_id = int(update.message.text.split("_")[1])
        pay = await db.confirm_payment(pay_id, uid)
        if not pay:
            await update.message.reply_text("Not found or already reviewed.")
            return
        try:
            await context.bot.send_message(pay["user_id"], "Payment confirmed! VIP activated!")
        except Exception:
            pass
        await update.message.reply_text(f"Payment #{pay_id} confirmed.")
    except (IndexError, ValueError):
        await update.message.reply_text("Invalid format. Use /confirm_ID")


async def cmd_reject_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = context.bot_data["db"]
    if not await db.has_perm(uid, "can_manage_payments"):
        await update.message.reply_text("Permission denied.")
        return
    try:
        pay_id = int(update.message.text.split("_")[1])
        pay = await db.reject_payment(pay_id, uid)
        if not pay:
            await update.message.reply_text("Not found.")
            return
        try:
            await context.bot.send_message(pay["user_id"], "Payment rejected.")
        except Exception:
            pass
        await update.message.reply_text(f"Payment #{pay_id} rejected.")
    except (IndexError, ValueError):
        await update.message.reply_text("Invalid format. Use /reject_ID")

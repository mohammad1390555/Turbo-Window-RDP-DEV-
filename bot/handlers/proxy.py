"""
Proxy feature: fetch free proxy lists, check alive, serve to users.
Supports HTTP, HTTPS, SOCKS4, SOCKS5.
"""

import asyncio
import logging
import time
from typing import List

import aiohttp

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from telegram.ext import ConversationHandler

from bot.config import PROXY_CHECK_TIMEOUT, PROXY_MAX_RESULTS, STATE_CHECKING_PROXY
from bot.decorators import guard
from bot.helpers import safe_edit
from bot.keyboards import kb_back_main, kb_proxy_menu

logger = logging.getLogger("BOT.proxy")

# Public proxy API endpoints
PROXY_SOURCES = [
    {
        "url": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
        "protocol": "http",
    },
    {
        "url": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=10000&country=all",
        "protocol": "socks5",
    },
    {
        "url": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=10000&country=all",
        "protocol": "socks4",
    },
    {
        "url": "https://www.proxy-list.download/api/v1/get?type=http",
        "protocol": "http",
    },
    {
        "url": "https://www.proxy-list.download/api/v1/get?type=https",
        "protocol": "https",
    },
    {
        "url": "https://www.proxy-list.download/api/v1/get?type=socks5",
        "protocol": "socks5",
    },
    {
        "url": "https://www.proxy-list.download/api/v1/get?type=socks4",
        "protocol": "socks4",
    },
]


async def _fetch_proxy_list(session: aiohttp.ClientSession, source: dict) -> List[dict]:
    """Fetch proxies from a single API source."""
    proxies = []
    try:
        async with session.get(source["url"], timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            text = await resp.text()
            for line in text.strip().splitlines():
                line = line.strip()
                if ":" in line:
                    parts = line.split(":")
                    if len(parts) == 2:
                        ip, port_str = parts
                        try:
                            port = int(port_str)
                            proxies.append({
                                "ip": ip.strip(),
                                "port": port,
                                "protocol": source["protocol"],
                            })
                        except ValueError:
                            continue
    except Exception as e:
        logger.debug(f"Failed to fetch from {source['url']}: {e}")
    return proxies


async def fetch_all_proxies() -> List[dict]:
    """Fetch proxies from all sources concurrently."""
    all_proxies = []
    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_proxy_list(session, src) for src in PROXY_SOURCES]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                all_proxies.extend(result)

    # deduplicate
    seen = set()
    unique = []
    for p in all_proxies:
        key = (p["ip"], p["port"], p["protocol"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


async def check_proxy(ip: str, port: int, protocol: str, timeout: int = None) -> dict:
    """Check if a proxy is alive and measure speed."""
    timeout = timeout or PROXY_CHECK_TIMEOUT
    proxy_url = f"{protocol}://{ip}:{port}"
    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://httpbin.org/ip",
                proxy=proxy_url if protocol in ("http", "https") else None,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                elapsed = int((time.monotonic() - start) * 1000)
                if resp.status == 200:
                    return {"alive": True, "speed_ms": elapsed}
    except Exception:
        pass
    return {"alive": False, "speed_ms": 0}


async def update_proxy_database(db) -> int:
    """Fetch proxies from all sources and upsert into DB."""
    proxies = await fetch_all_proxies()
    if not proxies:
        return 0
    return await db.upsert_proxies(proxies)


# ── Handlers ──────────────────────────────────────

@guard()
async def cb_proxy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.bot_data["db"]
    stats = await db.get_proxy_stats()
    text = (
        "*Proxy Service*\n"
        "--------------------\n\n"
        f"Available proxies: *{stats['alive']}*\n"
        f"HTTP: {stats['http']} | HTTPS: {stats['https']}\n"
        f"SOCKS4: {stats['socks4']} | SOCKS5: {stats['socks5']}\n\n"
        "Select a proxy type:"
    )
    await safe_edit(q, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_proxy_menu())


@guard()
async def cb_proxy_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.bot_data["db"]

    proto_map = {
        "proxy_http": "http",
        "proxy_socks5": "socks5",
        "proxy_socks4": "socks4",
        "proxy_all": None,
    }
    protocol = proto_map.get(q.data)
    label = protocol.upper() if protocol else "ALL"

    proxies = await db.get_alive_proxies(protocol=protocol, limit=PROXY_MAX_RESULTS)
    if not proxies:
        await safe_edit(
            q,
            f"No {label} proxies available.\nTry refreshing or check back later.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Refresh Proxies", callback_data="proxy_refresh")],
                [InlineKeyboardButton("Back", callback_data="proxy_menu")],
            ]),
        )
        return

    lines = [f"*{label} Proxies* ({len(proxies)} found)\n--------------------\n"]
    for i, p in enumerate(proxies[:20], 1):
        speed = f"{p['speed_ms']}ms" if p["speed_ms"] else "N/A"
        country = p.get("country") or "??"
        lines.append(f"`{p['ip']}:{p['port']}` | {p['protocol']} | {speed} | {country}")

    if len(proxies) > 20:
        lines.append(f"\n... and {len(proxies) - 20} more")

    text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Copy as Text", callback_data=f"proxy_copy_{protocol or 'all'}")],
        [InlineKeyboardButton("Refresh", callback_data="proxy_refresh")],
        [InlineKeyboardButton("Back", callback_data="proxy_menu")],
    ])
    await safe_edit(q, text[:4000], parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


@guard()
async def cb_proxy_copy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.bot_data["db"]
    proto = q.data.replace("proxy_copy_", "")
    protocol = proto if proto != "all" else None

    proxies = await db.get_alive_proxies(protocol=protocol, limit=PROXY_MAX_RESULTS)
    if not proxies:
        await q.answer("No proxies available.", show_alert=True)
        return

    text_list = "\n".join(f"{p['ip']}:{p['port']}" for p in proxies)
    await context.bot.send_message(
        q.from_user.id,
        f"```\n{text_list}\n```",
        parse_mode=ParseMode.MARKDOWN,
    )


@guard()
async def cb_proxy_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.bot_data["db"]
    stats = await db.get_proxy_stats()
    text = (
        "*Proxy Statistics*\n"
        "--------------------\n\n"
        f"Total in DB: {stats['total']}\n"
        f"Alive: *{stats['alive']}*\n"
        f"Dead: {stats['total'] - stats['alive']}\n\n"
        f"HTTP: {stats['http']}\n"
        f"HTTPS: {stats['https']}\n"
        f"SOCKS4: {stats['socks4']}\n"
        f"SOCKS5: {stats['socks5']}"
    )
    await safe_edit(
        q, text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Refresh Proxies", callback_data="proxy_refresh")],
            [InlineKeyboardButton("Back", callback_data="proxy_menu")],
        ]),
    )


@guard()
async def cb_proxy_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Refreshing proxy list...")
    db = context.bot_data["db"]

    await safe_edit(q, "Fetching proxies from sources...", reply_markup=None)
    count = await update_proxy_database(db)
    await db.clean_dead_proxies(older_than_hours=12)

    stats = await db.get_proxy_stats()
    text = (
        f"*Proxy list updated!*\n\n"
        f"Fetched: {count} proxies\n"
        f"Alive: {stats['alive']}"
    )
    await safe_edit(q, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_proxy_menu())


@guard()
async def cb_proxy_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await safe_edit(
        q,
        "*Proxy Checker*\n\n"
        "Send a proxy in format:\n"
        "`ip:port` or `protocol://ip:port`\n\n"
        "Example: `1.2.3.4:8080` or `socks5://1.2.3.4:1080`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_back_main(),
    )
    return STATE_CHECKING_PROXY


async def handle_proxy_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User sent a proxy string to validate."""
    text = update.message.text.strip()
    protocol = "http"
    addr = text

    if "://" in text:
        protocol, addr = text.split("://", 1)
        protocol = protocol.lower()

    if ":" not in addr:
        await update.message.reply_text(
            "Invalid format. Use `ip:port` or `protocol://ip:port`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_back_main(),
        )
        return ConversationHandler.END

    parts = addr.split(":")
    ip = parts[0].strip()
    try:
        port = int(parts[1].strip())
    except (ValueError, IndexError):
        await update.message.reply_text(
            "Invalid port number.",
            reply_markup=kb_back_main(),
        )
        return ConversationHandler.END

    await update.message.reply_text(f"Checking `{protocol}://{ip}:{port}` ...")

    result = await check_proxy(ip, port, protocol)
    if result["alive"]:
        text = (
            f"*Proxy is ALIVE*\n\n"
            f"Address: `{ip}:{port}`\n"
            f"Protocol: {protocol.upper()}\n"
            f"Response time: *{result['speed_ms']}ms*"
        )
    else:
        text = (
            f"*Proxy is DEAD*\n\n"
            f"Address: `{ip}:{port}`\n"
            f"Protocol: {protocol.upper()}\n"
            f"Could not connect within {PROXY_CHECK_TIMEOUT}s"
        )
    await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_proxy_menu()
    )
    return ConversationHandler.END


# Admin proxy management
@guard(perm="can_manage_configs")
async def cb_admin_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.bot_data["db"]
    stats = await db.get_proxy_stats()
    text = (
        "*Admin: Proxy Management*\n"
        "--------------------\n\n"
        f"Total: {stats['total']} | Alive: {stats['alive']}\n"
        f"HTTP: {stats['http']} | SOCKS5: {stats['socks5']}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Fetch New Proxies", callback_data="proxy_refresh")],
        [InlineKeyboardButton("Clean Dead Proxies", callback_data="admin_proxy_clean")],
        [InlineKeyboardButton("Back", callback_data="admin")],
    ])
    await safe_edit(q, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


@guard(perm="can_manage_configs")
async def cb_admin_proxy_clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.bot_data["db"]
    removed = await db.clean_dead_proxies(older_than_hours=1)
    await safe_edit(
        q,
        f"Cleaned {removed} dead proxies.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Back", callback_data="admin_proxy")],
        ]),
    )


@guard()
async def cb_speed_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run a quick download speed test."""
    q = update.callback_query
    await q.answer()
    await safe_edit(q, "Running speed test...\nPlease wait 5-10 seconds.", reply_markup=None)

    test_urls = [
        ("Cloudflare", "https://speed.cloudflare.com/__down?bytes=5000000"),
        ("Hetzner", "https://speed.hetzner.de/1MB.bin"),
    ]
    results = []
    async with aiohttp.ClientSession() as session:
        for name, url in test_urls:
            try:
                start = time.monotonic()
                total = 0
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    async for chunk in resp.content.iter_chunked(65536):
                        total += len(chunk)
                elapsed = time.monotonic() - start
                speed_mbps = (total * 8) / (elapsed * 1_000_000)
                results.append(f"{name}: *{speed_mbps:.1f} Mbps* ({total / 1024:.0f} KB in {elapsed:.1f}s)")
            except Exception:
                results.append(f"{name}: Failed")

    text = "*Speed Test Results*\n--------------------\n\n" + "\n".join(results)
    await safe_edit(q, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_back_main())


async def scheduled_proxy_update(context: ContextTypes.DEFAULT_TYPE):
    """Periodic job to refresh proxy list."""
    db = context.bot_data["db"]
    try:
        count = await update_proxy_database(db)
        await db.clean_dead_proxies(older_than_hours=24)
        logger.info(f"Scheduled proxy update: {count} proxies fetched")
    except Exception as e:
        logger.error(f"Scheduled proxy update failed: {e}")

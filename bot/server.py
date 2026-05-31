"""
HTTP streaming server for file downloads.
"""

import re
import logging
from datetime import datetime

import aiohttp
from aiohttp import web

from bot.config import BOT_TOKEN, WEB_SERVER_HOST, WEB_SERVER_PORT

logger = logging.getLogger("BOT.server")


async def stream_handler(request: web.Request) -> web.Response:
    f_uid = request.match_info.get("f_uid", "")
    if not re.match(r"^[\w\-]{10,}$", f_uid):
        return web.Response(status=400, text="Bad Request")

    db = request.app["db"]
    file_data = await db.get_file(f_uid)
    if not file_data:
        return web.Response(status=404, text="File not found")

    if file_data.get("expires_at") and datetime.now() > datetime.fromisoformat(
        file_data["expires_at"]
    ):
        return web.Response(status=410, text="Link expired")

    await db.increment_file_downloads(f_uid)
    bot = request.app["bot"]
    try:
        tg_file = await bot.get_file(file_data["file_id"])
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{tg_file.file_path}"
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": file_data.get("mime_type", "application/octet-stream"),
                "Content-Disposition": f'attachment; filename="{file_data["file_name"]}"',
            },
        )
        await resp.prepare(request)
        async with request.app["http_client"].get(file_url) as r:
            async for chunk in r.content.iter_chunked(512 * 1024):
                await resp.write(chunk)
        return resp
    except Exception as e:
        logger.error(f"Stream error: {e}")
        return web.Response(status=500, text="Internal error")


async def health_handler(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def proxy_api_handler(request: web.Request) -> web.Response:
    """REST API endpoint for proxy list."""
    db = request.app["db"]
    protocol = request.query.get("protocol")
    try:
        limit = min(int(request.query.get("limit", "50")), 200)
    except (ValueError, TypeError):
        limit = 50
    proxies = await db.get_alive_proxies(protocol=protocol, limit=limit)
    lines = [f"{p['ip']}:{p['port']}" for p in proxies]
    return web.Response(text="\n".join(lines), content_type="text/plain")


async def start_web_server(bot, db):
    app = web.Application()
    app["bot"] = bot
    app["db"] = db
    app["http_client"] = aiohttp.ClientSession()

    async def close_session(app):
        await app["http_client"].close()

    app.on_cleanup.append(close_session)
    app.router.add_get("/stream/{f_uid}", stream_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/api/proxies", proxy_api_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_SERVER_HOST, WEB_SERVER_PORT)
    await site.start()
    logger.info(f"HTTP server: {WEB_SERVER_HOST}:{WEB_SERVER_PORT}")

"""
YouTube / social media download handlers.
"""

import os
import io
import re
import asyncio
import tempfile
import glob as _glob
import logging
from typing import List

import yt_dlp

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import (
    COOKIE_FILE,
    MAX_YT_SIZE_MB,
    MAX_QUEUE,
    YT_SLEEP,
    VIP_LEVELS,
    STATE_WAITING_YT_URL,
)
from bot.decorators import guard
from bot.helpers import (
    safe_edit,
    sanitize_text,
    is_yt_url,
    is_social_url,
    fmt_duration,
    make_progress_bar,
)
from bot.keyboards import kb_back_main, kb_cancel

logger = logging.getLogger("BOT.youtube")

youtube_queue: List[dict] = []
is_processing = False


def _cookie_args_cmd() -> list:
    if os.path.exists(COOKIE_FILE) and os.path.getsize(COOKIE_FILE) > 0:
        return ["--cookies", COOKIE_FILE]
    return ["--extractor-args", "youtube:player_client=android,ios,web"]


def _cookie_args_opts() -> dict:
    if os.path.exists(COOKIE_FILE) and os.path.getsize(COOKIE_FILE) > 0:
        return {"cookiefile": COOKIE_FILE}
    return {"extractor_args": {"youtube": {"player_client": ["android", "ios", "web"]}}}


async def get_media_info(url: str) -> dict:
    loop = asyncio.get_event_loop()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 30,
        **_cookie_args_opts(),
    }

    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    info = await loop.run_in_executor(None, _extract)
    if not info:
        raise ValueError("Could not fetch video info")

    filesize = 0
    for f in sorted(info.get("formats", []), key=lambda x: x.get("height") or 0, reverse=True):
        if (f.get("height") or 999) <= 720:
            fs = f.get("filesize") or f.get("filesize_approx") or 0
            if fs:
                filesize = fs
                break
    if not filesize:
        filesize = info.get("filesize") or info.get("filesize_approx") or 0

    return {
        "title": (info.get("title") or "video")[:80],
        "duration": info.get("duration") or 0,
        "filesize": filesize,
        "uploader": info.get("uploader") or "Unknown",
        "view_count": info.get("view_count") or 0,
        "thumbnail": info.get("thumbnail") or "",
        "is_yt": is_yt_url(url),
    }


async def download_media(url: str, audio_only: bool = False, quality: str = "720") -> bytes:
    if audio_only:
        fmt = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best"
    else:
        h = int(quality)
        fmt = "/".join([
            f"bestvideo[ext=mp4][height<={h}]+bestaudio[ext=m4a]",
            f"bestvideo[ext=mp4][height<={h}]+bestaudio",
            f"bestvideo[height<={h}]+bestaudio[ext=m4a]",
            f"bestvideo[height<={h}]+bestaudio",
            f"best[height<={h}][ext=mp4]",
            f"best[height<={h}]",
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]",
            "bestvideo+bestaudio",
            "best[ext=mp4]",
            "best",
        ])

    with tempfile.TemporaryDirectory(prefix="ytdl_") as tmpdir:
        out_tmpl = os.path.join(tmpdir, "video.%(ext)s")
        cmd = ["yt-dlp", "-f", fmt]
        if audio_only:
            cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
        else:
            cmd += ["--merge-output-format", "mp4"]
        cmd += _cookie_args_cmd()
        cmd += [
            "--no-playlist", "--no-part",
            "--retries", "3", "--fragment-retries", "3",
            "--socket-timeout", "60",
            "-o", out_tmpl, url,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=360)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("Download timed out.")

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore")
            logger.warning(f"yt-dlp stderr: {err[:600]}")
            el = err.lower()
            if "sign in" in el or "login" in el or "age" in el:
                raise RuntimeError("Video requires login. Admin must set cookies.")
            if "private" in el:
                raise RuntimeError("This video is private.")
            if "not available" in el or "unavailable" in el:
                raise RuntimeError("Video is unavailable or region-restricted.")
            if "copyright" in el:
                raise RuntimeError("Video cannot be downloaded due to copyright.")
            if "live" in el and "stream" in el:
                raise RuntimeError("Live streams cannot be downloaded.")
            raise RuntimeError("Download failed. The video may have restrictions.")

        files = _glob.glob(os.path.join(tmpdir, "video.*"))
        if not files:
            raise RuntimeError("No file was downloaded.")
        with open(files[0], "rb") as fh:
            data = fh.read()

    if not data:
        raise RuntimeError("Downloaded file is empty.")
    return data


async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    global youtube_queue, is_processing
    if is_processing:
        return
    is_processing = True
    db = context.bot_data["db"]
    try:
        while youtube_queue:
            job = youtube_queue.pop(0)
            uid = job["uid"]
            url = job["url"]
            title = job["title"]
            audio = job.get("audio", False)
            quality = job.get("quality", "720")
            try:
                icon = "audio" if audio else "video"
                short_title = title[:40] + ("..." if len(title) > 40 else "")
                prog_msg = await context.bot.send_message(
                    uid,
                    f"*Downloading {icon}*\n"
                    f"--------------------\n"
                    f"`{short_title}`\n\n"
                    f"`{make_progress_bar(0, 12)}` 0%\n"
                    f"Connecting...",
                    parse_mode=ParseMode.MARKDOWN,
                )

                async def _update_progress(pct: int, status: str):
                    try:
                        bar = make_progress_bar(pct, 12)
                        await safe_edit(
                            prog_msg,
                            f"*Downloading {icon}*\n"
                            f"--------------------\n"
                            f"`{short_title}`\n\n"
                            f"`{bar}` {pct}%\n"
                            f"{status}",
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception:
                        pass

                await _update_progress(10, "Connecting to server...")
                download_task = asyncio.create_task(
                    download_media(url, audio_only=audio, quality=quality)
                )

                stages = [
                    (25, "Fetching info..."),
                    (50, "Downloading..."),
                    (75, "Processing..."),
                    (90, "Preparing to send..."),
                ]
                for pct, status in stages:
                    await asyncio.sleep(4)
                    if download_task.done():
                        break
                    await _update_progress(pct, status)

                data = await download_task
                await _update_progress(100, "Ready!")

                size_mb = len(data) / (1024 * 1024)
                if size_mb > MAX_YT_SIZE_MB:
                    await context.bot.send_message(
                        uid,
                        f"File too large ({size_mb:.1f} MB > {MAX_YT_SIZE_MB} MB limit).",
                    )
                    continue

                file_buf = io.BytesIO(data)
                safe = re.sub(r"[^\w\s\-]", "", title)[:50]
                if audio:
                    file_buf.name = f"{safe}.mp3"
                    await context.bot.send_audio(
                        uid, audio=file_buf, title=title,
                        caption=f"*{title[:60]}*\n{size_mb:.1f} MB",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                else:
                    file_buf.name = f"{safe}.mp4"
                    await context.bot.send_video(
                        uid, video=file_buf,
                        caption=(
                            f"*{title[:60]}*\n"
                            f"--------------------\n"
                            f"{size_mb:.1f} MB | {quality}p | Ready"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                        supports_streaming=True,
                    )

                try:
                    await prog_msg.delete()
                except Exception:
                    pass

                await db.add_log(uid, "yt_download", f"title={title[:50]} size={size_mb:.1f}MB quality={quality}")
                async with (await db.connect()).execute(
                    "UPDATE bot_stats SET value=value+1 WHERE key='total_downloads'"
                ):
                    pass
                await (await db.connect()).commit()

            except Exception as e:
                logger.error(f"Queue error: {e}")
                try:
                    await context.bot.send_message(uid, f"Error: {str(e)[:200]}")
                except Exception:
                    pass

            await asyncio.sleep(YT_SLEEP)
    finally:
        is_processing = False


@guard()
async def cb_yt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_edit(
        q,
        "*Video Download*\n\n"
        "Send a link from:\n"
        "YouTube | Instagram | TikTok | Twitter\n\n"
        "Supported formats: video + audio",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_cancel(),
    )
    return STATE_WAITING_YT_URL


async def handle_yt_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = context.bot_data["db"]
    url = sanitize_text(update.message.text, 500).strip()

    if not is_yt_url(url) and not is_social_url(url):
        await update.message.reply_text("Invalid URL. Send a valid video link.", reply_markup=kb_cancel())
        return STATE_WAITING_YT_URL

    if len(youtube_queue) >= MAX_QUEUE:
        await update.message.reply_text("Queue is full. Try again later.", reply_markup=kb_back_main())
        return ConversationHandler.END

    status_msg = await update.message.reply_text("Fetching video info...")
    try:
        info = await asyncio.wait_for(get_media_info(url), timeout=45)
        size_mb = info["filesize"] / (1024 * 1024) if info["filesize"] else 0
        context.user_data["yt_url"] = url
        context.user_data["yt_info"] = info

        size_text = f"{size_mb:.1f} MB" if size_mb else "Unknown"
        views = f"{info['view_count']:,}" if info["view_count"] else "--"
        text = (
            f"*Video Info*\n\n"
            f"*{info['title']}*\n\n"
            f"Channel: {info['uploader']}\n"
            f"Duration: `{fmt_duration(info['duration'])}`\n"
            f"Size: `{size_text}`\n"
            f"Views: {views}\n\n"
            f"*Select download quality:*"
        )

        is_vip = await db.is_vip(uid)
        vip_level = await db.get_vip_level(uid) or "silver"
        max_q = VIP_LEVELS.get(vip_level, {}).get("max_yt_quality", "720") if is_vip else "720"

        quality_btns = []
        if info.get("is_yt"):
            available = [("360", "360p"), ("480", "480p"), ("720", "720p HD")]
            if is_vip and max_q == "1080":
                available.append(("1080", "1080p FHD"))
            quality_btns = [
                InlineKeyboardButton(label, callback_data=f"yt_q_{q_val}")
                for q_val, label in available
            ]
        kb_rows = []
        if quality_btns:
            kb_rows.append(quality_btns[:2])
            if len(quality_btns) > 2:
                kb_rows.append(quality_btns[2:])
        kb_rows.append([InlineKeyboardButton("Audio (MP3)", callback_data="yt_dl_audio")])
        if not info.get("is_yt"):
            kb_rows = [
                [InlineKeyboardButton("Download Video", callback_data="yt_dl_video")],
                [InlineKeyboardButton("Download Audio", callback_data="yt_dl_audio")],
            ]
        kb_rows.append([InlineKeyboardButton("Cancel", callback_data="main")])

        await safe_edit(status_msg, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb_rows))
    except asyncio.TimeoutError:
        await safe_edit(status_msg, "Timed out fetching info. Try again.")
    except Exception as e:
        await safe_edit(status_msg, f"Error: {str(e)[:200]}")
        return ConversationHandler.END

    return ConversationHandler.END


@guard()
async def cb_yt_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.bot_data["db"]
    quality = q.data.replace("yt_q_", "")
    url = context.user_data.get("yt_url")
    info = context.user_data.get("yt_info", {})
    if not url:
        await safe_edit(q, "Session expired.", reply_markup=kb_back_main())
        return
    uid = q.from_user.id
    title = info.get("title", "video")
    job = {"uid": uid, "url": url, "title": title, "audio": False, "quality": quality}
    if await db.is_vip(uid):
        youtube_queue.insert(0, job)
        pos_text = "VIP priority!"
    else:
        youtube_queue.append(job)
        pos_text = f"Queue position: {len(youtube_queue)}"
    await safe_edit(
        q,
        f"*Added to queue*\n"
        f"--------------------\n"
        f"`{title[:50]}`\n"
        f"Quality: {quality}p\n\n"
        f"{pos_text}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_back_main(),
    )
    asyncio.create_task(process_queue(context))
    context.user_data.pop("yt_url", None)
    context.user_data.pop("yt_info", None)


@guard()
async def cb_yt_dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.bot_data["db"]
    uid = q.from_user.id
    audio = q.data == "yt_dl_audio"
    url = context.user_data.get("yt_url")
    info = context.user_data.get("yt_info", {})
    if not url:
        await safe_edit(q, "Session expired.", reply_markup=kb_back_main())
        return
    title = info.get("title", "video")
    quality = "720"
    if await db.is_vip(uid):
        lvl = await db.get_vip_level(uid) or "silver"
        quality = VIP_LEVELS.get(lvl, {}).get("max_yt_quality", "720")
    job = {"uid": uid, "url": url, "title": title, "audio": audio, "quality": quality}
    if await db.is_vip(uid):
        youtube_queue.insert(0, job)
        pos_text = "VIP priority!"
    else:
        youtube_queue.append(job)
        pos_text = f"Queue position: {len(youtube_queue)}"
    icon = "Audio" if audio else "Video"
    await safe_edit(
        q,
        f"*Added to queue*\n"
        f"--------------------\n"
        f"{icon}: `{title[:50]}`\n\n"
        f"{pos_text}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_back_main(),
    )
    asyncio.create_task(process_queue(context))
    context.user_data.pop("yt_url", None)
    context.user_data.pop("yt_info", None)

---
name: testing-telegram-bot
description: Test the refactored Telegram bot end-to-end. Use when verifying bot module changes, bug fixes, or new features.
---

# Testing the Telegram Bot

## Overview

The bot is a modular Python Telegram bot under `bot/` with 12 modules. Testing can be done at two levels:
1. **Offline integration tests** (no BOT_TOKEN needed) — covers DB, web server, proxy fetch
2. **Live Telegram tests** (requires BOT_TOKEN) — covers conversation flows, file streaming

## Devin Secrets Needed

- `BOT_TOKEN` — Telegram bot token (only needed for live Telegram interaction tests)
- `SUPER_ADMIN_ID` — Telegram user ID for admin (can use dummy `999999` for offline tests)

## Offline Testing (No Token Required)

### Setup
```bash
cd /home/ubuntu/repos/Turbo-Window-RDP-DEV-
pip install -r requirements.txt
```

### Key Pattern: Monkey-patch DB_NAME
The `Database` class uses `DB_NAME` from `bot.config` (no constructor arg). To test with a temp file:
```python
import bot.database as db_mod
db_mod.DB_NAME = "/tmp/test.db"  # or tempfile.mktemp(suffix=".db")
from bot.database import Database
db = Database()
await db.init_db()
```

### Key Pattern: Set env vars BEFORE importing bot modules
```python
import os
os.environ["BOT_TOKEN"] = "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
os.environ["SUPER_ADMIN_ID"] = "999999"
# NOW import bot modules
```
`bot/config.py` calls `sys.exit(1)` if BOT_TOKEN is empty, so it must be set before any bot import.

### What Can Be Tested Offline

1. **Database layer** — All CRUD operations, schema creation, migrations
2. **Web server endpoints** — Build a minimal `aiohttp` app with handlers from `bot.server`
3. **Proxy fetching** — `bot.handlers.proxy.fetch_all_proxies()` hits real public APIs
4. **Module imports** — Verify no ImportError across all 12 modules
5. **Conversation states** — Verify count matches expected (currently 19, 0-indexed 0-18)

### Web Server Testing Pattern
```python
from aiohttp import web
from bot.server import proxy_api_handler, health_handler

app = web.Application()
app["db"] = db  # your test Database instance
app.router.add_get("/api/proxies", proxy_api_handler)
app.router.add_get("/health", health_handler)

runner = web.AppRunner(app)
await runner.setup()
site = web.TCPSite(runner, "127.0.0.1", 19876)
await site.start()
# Test with aiohttp.ClientSession against localhost:19876
```

### Common Schema Gotchas
- `file_links` table: `file_type` is NOT NULL — always include it in test INSERTs
- `user_logs` table: column is `details` (not `detail`)
- `discount_codes`: `expires_at` should be set to future date for valid codes

## Live Testing (Requires BOT_TOKEN)

With a real token, you can test:
- Bot startup via `python run.py`
- Conversation flows (send /start, interact with menus)
- File streaming via `/stream/{f_uid}` endpoint
- Broadcast messaging
- VIP payment flow

The bot entry point is `bot.main.main()` (not `build_application`).

## Key Bug Fixes to Verify on Changes

1. **Referral balance**: `start.py` should show `referrer['balance']` (already includes bonus from DB)
2. **Discount timing**: `validate_discount()` must NOT increment `used_count`; only `use_discount()` should
3. **Proxy API limit**: `server.py` wraps `int(limit)` in try/except, defaults to 50
4. **Session cleanup**: `server.py` registers `on_cleanup` handler for ClientSession
5. **File download count**: `get_file()` is read-only; `increment_file_downloads()` is separate

## Running the Test Script
```bash
cd /home/ubuntu/repos/Turbo-Window-RDP-DEV-
python test_integration.py
```
Expected: 44/44 assertions pass. The test script sets dummy env vars automatically.

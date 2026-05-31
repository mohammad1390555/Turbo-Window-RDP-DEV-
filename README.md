# Telegram Bot v4.0 — Modular Architecture

A feature-rich Telegram bot with VPN config distribution, media downloading, proxy service, file-to-link, VIP subscriptions, and full admin panel.

## Features

- **VPN Config Distribution** — Free and VIP tiers with cooldown and usage tracking
- **Media Download** — YouTube, Instagram, TikTok, Twitter video/audio download with queue system
- **Proxy Service** — Aggregates free HTTP/HTTPS/SOCKS4/SOCKS5 proxies from multiple sources, auto-refreshes, REST API endpoint
- **File to Link** — Upload any file, get a direct download link (7-day expiry)
- **VIP System** — Silver/Gold/Diamond plans with payment receipts and wallet purchases
- **Wallet** — Balance management, referral bonuses, VIP purchases
- **Referral System** — Invite friends, earn wallet credit
- **Admin Panel** — User management, broadcast, stats, config management, discount codes
- **HTTP Streaming Server** — Direct file downloads via HTTP, proxy list API

## Project Structure

```
bot/
├── __init__.py         # Package init
├── config.py           # Configuration (env vars, config.json, constants)
├── database.py         # Async SQLite database layer (aiosqlite)
├── helpers.py          # Utilities (formatting, QR, rate limiting, validation)
├── keyboards.py        # Inline keyboard builders
├── decorators.py       # Guard decorator (auth, rate limit, ban check)
├── server.py           # HTTP streaming server + proxy API
├── main.py             # App entry point, handler registration
└── handlers/
    ├── start.py        # /start command
    ├── main_menu.py    # Profile, help, referral, claim, VIP, wallet
    ├── youtube.py      # Media download (YouTube, Instagram, TikTok)
    ├── file.py         # File-to-link
    ├── proxy.py        # Proxy service (fetch, check, list, manage)
    └── admin.py        # Admin panel (configs, users, broadcast, stats)
```

## Setup

1. Clone the repo
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your values:
   ```bash
   cp .env.example .env
   ```
4. Run:
   ```bash
   python run.py
   ```

## Configuration

Edit `config.json` (auto-generated on first run) to customize:
- VIP plan pricing and durations
- Download limits and queue sizes
- Rate limiting
- Proxy update intervals
- Referral bonuses

## Proxy API

The bot exposes a REST API at `/api/proxies` for programmatic proxy access:
```
GET /api/proxies?protocol=http&limit=50
```

## Architecture Highlights

- **Async database** via `aiosqlite` — non-blocking DB operations
- **Persistent DB connection** with WAL mode and 8 MB cache
- **Separated concerns** — each module handles one domain
- **Guard decorator** — centralized auth, rate limiting, ban checking
- **Queue system** for media downloads with progress tracking
- **Concurrent updates** enabled for better throughput
- **Periodic jobs** — proxy refresh, daily reports, file cleanup

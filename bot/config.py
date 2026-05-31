"""
Centralized configuration loader.
Reads from .env and config.json, merges defaults, and exposes typed constants.
"""

import os
import sys
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
SUPER_ADMIN_ID: int = int(os.getenv("SUPER_ADMIN_ID", "0"))
PAYMENT_CARD: str = os.getenv("PAYMENT_CARD", "")
FORCE_CHANNEL: str = os.getenv("FORCE_CHANNEL", "")

if not BOT_TOKEN:
    print("BOT_TOKEN is not set in .env")
    sys.exit(1)

# ── Web / streaming server ────────────────────────
WEB_SERVER_HOST: str = "0.0.0.0"
WEB_SERVER_PORT: int = int(os.getenv("WEB_PORT", "10674"))
BASE_DOWNLOAD_URL: str = os.getenv("BASE_URL", f"http://localhost:{WEB_SERVER_PORT}")
COOKIE_FILE: str = os.path.join(os.path.expanduser("~"), ".yt-dlp", "cookies.txt")

# ── Directories ───────────────────────────────────
for _d in ("uploads", "logs", "backups", "temp"):
    Path(_d).mkdir(exist_ok=True)
Path(COOKIE_FILE).parent.mkdir(exist_ok=True)

# ── Config JSON ───────────────────────────────────
DEFAULT_CONFIG: dict = {
    "database": "bot.db",
    "claim_cooldown_hours": 6,
    "max_yt_size_mb": 500,
    "max_file_size_mb": 2000,
    "yt_sleep_interval": 2,
    "max_queue_size": 50,
    "referral_bonus": 5000,
    "enable_qr_code": True,
    "welcome_message": "به ربات خوش اومدی!",
    "rate_limit_per_minute": 25,
    "max_broadcast_delay": 0.05,
    "daily_report_hour": 8,
    "proxy_update_interval_minutes": 30,
    "proxy_check_timeout": 8,
    "proxy_max_results": 30,
    "vip_levels": {
        "silver": {
            "name": "Silver",
            "price": 50000,
            "days": 30,
            "max_yt_quality": "720",
            "max_configs": 3,
            "wallet_bonus": 0,
        },
        "gold": {
            "name": "Gold",
            "price": 120000,
            "days": 90,
            "max_yt_quality": "1080",
            "max_configs": 5,
            "wallet_bonus": 10000,
        },
        "diamond": {
            "name": "Diamond",
            "price": 250000,
            "days": 180,
            "max_yt_quality": "1080",
            "max_configs": 10,
            "wallet_bonus": 30000,
        },
    },
    "discount_codes": {},
}

CONFIG_PATH = Path("config.json")
if not CONFIG_PATH.exists():
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=4, ensure_ascii=False), encoding="utf-8")

with open(CONFIG_PATH, "r", encoding="utf-8") as _f:
    CONFIG: dict = json.load(_f)

for _k, _v in DEFAULT_CONFIG.items():
    CONFIG.setdefault(_k, _v)

# ── Typed shortcuts ───────────────────────────────
DB_NAME: str = CONFIG["database"]
CLAIM_COOLDOWN: int = CONFIG["claim_cooldown_hours"]
MAX_YT_SIZE_MB: int = CONFIG["max_yt_size_mb"]
MAX_FILE_SIZE: int = CONFIG["max_file_size_mb"]
YT_SLEEP: int = CONFIG["yt_sleep_interval"]
MAX_QUEUE: int = CONFIG["max_queue_size"]
REFERRAL_BONUS: int = CONFIG["referral_bonus"]
ENABLE_QR: bool = CONFIG["enable_qr_code"]
WELCOME_MSG: str = CONFIG["welcome_message"]
VIP_LEVELS: dict = CONFIG["vip_levels"]
RATE_LIMIT: int = CONFIG.get("rate_limit_per_minute", 25)
DISCOUNT_CODES: dict = CONFIG.get("discount_codes", {})
PROXY_UPDATE_INTERVAL: int = CONFIG.get("proxy_update_interval_minutes", 30)
PROXY_CHECK_TIMEOUT: int = CONFIG.get("proxy_check_timeout", 8)
PROXY_MAX_RESULTS: int = CONFIG.get("proxy_max_results", 30)

VALID_PERMS = frozenset({
    "can_manage_configs",
    "can_manage_payments",
    "can_broadcast",
    "can_block_users",
    "can_give_vip",
})

# ── Conversation states ───────────────────────────
(
    STATE_NONE,
    STATE_WAITING_RECEIPT,
    STATE_ADDING_CONFIG,
    STATE_DELETING_CONFIG,
    STATE_BROADCASTING,
    STATE_WAITING_FILE,
    STATE_WAITING_YT_URL,
    STATE_MANAGE_ADMIN,
    STATE_SET_COOKIE,
    STATE_WAITING_CONFIG_TEXT,
    STATE_SEARCHING_USER,
    STATE_GIVE_VIP,
    STATE_BAN_USER,
    STATE_UNBAN_USER,
    STATE_WAITING_DISCOUNT,
    STATE_ADD_DISCOUNT,
    STATE_WALLET_CHARGE,
    STATE_CHECKING_PROXY,
    STATE_ADMIN_WALLET_CHARGE,
) = range(19)

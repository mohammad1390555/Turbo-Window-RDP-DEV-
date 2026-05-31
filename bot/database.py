"""
Async database layer using aiosqlite.
All DB operations are non-blocking; connection pooling via a persistent connection.
"""

import shutil
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

import aiosqlite

from bot.config import (
    DB_NAME,
    SUPER_ADMIN_ID,
    CLAIM_COOLDOWN,
    VIP_LEVELS,
    REFERRAL_BONUS,
    VALID_PERMS,
)
from bot.helpers import generate_secure_code

logger = logging.getLogger("BOT.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    username TEXT DEFAULT '',
    full_name TEXT DEFAULT '',
    role TEXT DEFAULT 'user',
    is_vip INTEGER DEFAULT 0,
    vip_level TEXT DEFAULT 'silver',
    vip_expiry TEXT,
    last_claim TEXT,
    total_claims INTEGER DEFAULT 0,
    balance INTEGER DEFAULT 0,
    total_spent INTEGER DEFAULT 0,
    joined_at TEXT DEFAULT (datetime('now')),
    banned INTEGER DEFAULT 0,
    ban_reason TEXT,
    referral_code TEXT UNIQUE,
    referred_by INTEGER,
    total_referrals INTEGER DEFAULT 0,
    last_seen TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS admin_permissions (
    admin_id INTEGER PRIMARY KEY,
    can_manage_configs INTEGER DEFAULT 0,
    can_manage_payments INTEGER DEFAULT 0,
    can_broadcast INTEGER DEFAULT 0,
    can_block_users INTEGER DEFAULT 0,
    can_give_vip INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_text TEXT NOT NULL,
    category TEXT DEFAULT 'free',
    protocol TEXT DEFAULT 'unknown',
    remark TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT,
    usage_count INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS file_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_unique_id TEXT UNIQUE NOT NULL,
    file_id TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_name TEXT DEFAULT 'file',
    file_size INTEGER DEFAULT 0,
    mime_type TEXT DEFAULT 'application/octet-stream',
    uploader_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT,
    download_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    plan TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    receipt_file TEXT,
    discount_code TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    reviewed_at TEXT,
    confirmed_by INTEGER
);

CREATE TABLE IF NOT EXISTS wallet_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    type TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS discount_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    percent INTEGER NOT NULL,
    max_uses INTEGER DEFAULT 0,
    used_count INTEGER DEFAULT 0,
    expires_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS bot_stats (
    key TEXT PRIMARY KEY,
    value INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT,
    details TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    message TEXT,
    total_sent INTEGER DEFAULT 0,
    total_failed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    port INTEGER NOT NULL,
    protocol TEXT DEFAULT 'http',
    country TEXT DEFAULT '',
    speed_ms INTEGER DEFAULT 0,
    anonymity TEXT DEFAULT 'unknown',
    alive INTEGER DEFAULT 1,
    last_check TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(ip, port, protocol)
);

CREATE INDEX IF NOT EXISTS idx_users_tg ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_configs_cat ON configs(category, active);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_files_uid ON file_links(file_unique_id);
CREATE INDEX IF NOT EXISTS idx_proxies_alive ON proxies(alive, protocol);
"""

_STAT_KEYS = (
    "total_users",
    "total_downloads",
    "total_claims",
    "total_payments",
    "total_revenue",
)


class Database:
    """Async SQLite database with connection pooling via a persistent connection."""

    def __init__(self) -> None:
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(DB_NAME, timeout=30)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA foreign_keys=ON")
            await self._db.execute("PRAGMA synchronous=NORMAL")
            await self._db.execute("PRAGMA cache_size=-8000")  # 8 MB cache
        return self._db

    async def init_db(self) -> None:
        db = await self.connect()
        await db.executescript(_SCHEMA)

        # migrations
        for col, default in [
            ("vip_level", "'silver'"),
            ("total_spent", "0"),
        ]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT {default}")
            except Exception:
                pass
        try:
            await db.execute("ALTER TABLE payments ADD COLUMN discount_code TEXT")
        except Exception:
            pass

        for key in _STAT_KEYS:
            await db.execute("INSERT OR IGNORE INTO bot_stats (key, value) VALUES (?, 0)", (key,))

        if SUPER_ADMIN_ID:
            await db.execute(
                "INSERT OR IGNORE INTO users (telegram_id, role, referral_code) VALUES (?, 'super_admin', ?)",
                (SUPER_ADMIN_ID, generate_secure_code()),
            )
            await db.execute(
                "INSERT OR IGNORE INTO admin_permissions "
                "(admin_id, can_manage_configs, can_manage_payments, can_broadcast, can_block_users, can_give_vip) "
                "VALUES (?, 1, 1, 1, 1, 1)",
                (SUPER_ADMIN_ID,),
            )
        await db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ── helpers ────────────────────────────────────

    async def _fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        db = await self.connect()
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def _fetchall(self, sql: str, params: tuple = ()) -> List[dict]:
        db = await self.connect()
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def _execute(self, sql: str, params: tuple = ()) -> int:
        db = await self.connect()
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.rowcount

    async def _insert(self, sql: str, params: tuple = ()) -> int:
        db = await self.connect()
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.lastrowid

    # ── Users ─────────────────────────────────────

    async def get_user(self, tg_id: int) -> Optional[dict]:
        return await self._fetchone("SELECT * FROM users WHERE telegram_id=?", (tg_id,))

    async def create_user(
        self, tg_id: int, username: str, full_name: str, ref_code: str = None
    ) -> Tuple[dict, Optional[int]]:
        db = await self.connect()
        async with self._lock:
            existing = await self._fetchone("SELECT 1 FROM users WHERE telegram_id=?", (tg_id,))
            if existing:
                await self._execute(
                    "UPDATE users SET username=?, full_name=?, last_seen=datetime('now') WHERE telegram_id=?",
                    (username or "", full_name or "", tg_id),
                )
                return await self.get_user(tg_id), None

            code = generate_secure_code()
            referred_by = None

            if ref_code and not ref_code.startswith("dl_"):
                ref_row = await self._fetchone(
                    "SELECT telegram_id FROM users WHERE referral_code=?", (ref_code,)
                )
                if ref_row and ref_row["telegram_id"] != tg_id:
                    referred_by = ref_row["telegram_id"]
                    await db.execute(
                        "UPDATE users SET balance=balance+?, total_referrals=total_referrals+1 WHERE telegram_id=?",
                        (REFERRAL_BONUS, referred_by),
                    )
                    await db.execute(
                        "INSERT INTO wallet_transactions (user_id, amount, type, description) VALUES (?, ?, ?, ?)",
                        (referred_by, REFERRAL_BONUS, "credit", "Referral bonus"),
                    )

            role = "super_admin" if tg_id == SUPER_ADMIN_ID else "user"
            await db.execute(
                "INSERT INTO users (telegram_id, username, full_name, referral_code, referred_by, role) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (tg_id, username or "", full_name or "", code, referred_by, role),
            )
            await db.execute("UPDATE bot_stats SET value=value+1 WHERE key='total_users'")
            await db.commit()
        return await self.get_user(tg_id), referred_by

    async def update_last_seen(self, tg_id: int) -> None:
        await self._execute(
            "UPDATE users SET last_seen=datetime('now') WHERE telegram_id=?", (tg_id,)
        )

    async def is_vip(self, tg_id: int) -> bool:
        user = await self.get_user(tg_id)
        if not user or not user["is_vip"]:
            return False
        if user["vip_expiry"]:
            try:
                if datetime.now() > datetime.fromisoformat(user["vip_expiry"]):
                    await self._execute(
                        "UPDATE users SET is_vip=0, vip_expiry=NULL WHERE telegram_id=?",
                        (tg_id,),
                    )
                    return False
            except ValueError:
                return False
        return True

    async def get_vip_level(self, tg_id: int) -> Optional[str]:
        if not await self.is_vip(tg_id):
            return None
        user = await self.get_user(tg_id)
        return user.get("vip_level", "silver") if user else None

    async def can_claim(self, tg_id: int) -> Tuple[bool, int]:
        user = await self.get_user(tg_id)
        if not user or not user["last_claim"]:
            return True, 0
        try:
            last = datetime.fromisoformat(user["last_claim"])
        except ValueError:
            return True, 0
        passed = (datetime.now() - last).total_seconds() / 3600
        if passed >= CLAIM_COOLDOWN:
            return True, 0
        remaining = int((CLAIM_COOLDOWN - passed) * 3600)
        return False, remaining

    async def update_claim(self, tg_id: int) -> None:
        db = await self.connect()
        await db.execute(
            "UPDATE users SET last_claim=datetime('now'), total_claims=total_claims+1 WHERE telegram_id=?",
            (tg_id,),
        )
        await db.execute("UPDATE bot_stats SET value=value+1 WHERE key='total_claims'")
        await db.commit()

    async def has_perm(self, tg_id: int, perm: str) -> bool:
        if perm not in VALID_PERMS:
            return False
        if tg_id == SUPER_ADMIN_ID:
            return True
        row = await self._fetchone(
            f"SELECT {perm} FROM admin_permissions WHERE admin_id=?", (tg_id,)
        )
        return bool(row and row[perm])

    async def get_role(self, tg_id: int) -> str:
        user = await self.get_user(tg_id)
        return user["role"] if user else "user"

    async def ban_user(self, tg_id: int, reason: str = "") -> None:
        await self._execute(
            "UPDATE users SET banned=1, ban_reason=? WHERE telegram_id=?", (reason, tg_id)
        )

    async def unban_user(self, tg_id: int) -> bool:
        return (
            await self._execute(
                "UPDATE users SET banned=0, ban_reason=NULL WHERE telegram_id=?", (tg_id,)
            )
            > 0
        )

    async def get_banned_users(self, limit: int = 50) -> List[dict]:
        return await self._fetchall(
            "SELECT * FROM users WHERE banned=1 ORDER BY id DESC LIMIT ?", (limit,)
        )

    async def search_user(self, query: str) -> List[dict]:
        if query.isdigit():
            return await self._fetchall("SELECT * FROM users WHERE telegram_id=?", (int(query),))
        pattern = f"%{query}%"
        return await self._fetchall(
            "SELECT * FROM users WHERE username LIKE ? OR full_name LIKE ? LIMIT 10",
            (pattern, pattern),
        )

    async def get_all_users(self) -> List[int]:
        rows = await self._fetchall("SELECT telegram_id FROM users WHERE banned=0")
        return [r["telegram_id"] for r in rows]

    # ── Wallet ────────────────────────────────────

    async def add_balance(self, tg_id: int, amount: int, description: str = "") -> int:
        db = await self.connect()
        await db.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?", (amount, tg_id))
        await db.execute(
            "INSERT INTO wallet_transactions (user_id, amount, type, description) VALUES (?, ?, ?, ?)",
            (tg_id, amount, "credit", description),
        )
        await db.commit()
        row = await self._fetchone("SELECT balance FROM users WHERE telegram_id=?", (tg_id,))
        return row["balance"] if row else 0

    async def deduct_balance(self, tg_id: int, amount: int, description: str = "") -> bool:
        db = await self.connect()
        async with self._lock:
            row = await self._fetchone("SELECT balance FROM users WHERE telegram_id=?", (tg_id,))
            if not row or row["balance"] < amount:
                return False
            await db.execute(
                "UPDATE users SET balance=balance-?, total_spent=total_spent+? WHERE telegram_id=?",
                (amount, amount, tg_id),
            )
            await db.execute(
                "INSERT INTO wallet_transactions (user_id, amount, type, description) VALUES (?, ?, ?, ?)",
                (tg_id, -amount, "debit", description),
            )
            await db.commit()
        return True

    async def get_wallet_history(self, tg_id: int, limit: int = 10) -> List[dict]:
        return await self._fetchall(
            "SELECT * FROM wallet_transactions WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (tg_id, limit),
        )

    # ── Discount codes ────────────────────────────

    async def add_discount_code(
        self, code: str, percent: int, max_uses: int = 0, days_valid: int = 30
    ) -> bool:
        expires = (datetime.now() + timedelta(days=days_valid)).isoformat()
        try:
            await self._insert(
                "INSERT INTO discount_codes (code, percent, max_uses, expires_at) VALUES (?, ?, ?, ?)",
                (code.upper(), percent, max_uses, expires),
            )
            return True
        except Exception:
            return False

    async def validate_discount(self, code: str) -> Optional[dict]:
        d = await self._fetchone(
            "SELECT * FROM discount_codes WHERE code=? AND active=1", (code.upper(),)
        )
        if not d:
            return None
        if d["expires_at"] and datetime.now() > datetime.fromisoformat(d["expires_at"]):
            return None
        if d["max_uses"] > 0 and d["used_count"] >= d["max_uses"]:
            return None
        return d

    async def use_discount(self, code: str) -> None:
        await self._execute(
            "UPDATE discount_codes SET used_count=used_count+1 WHERE code=?", (code.upper(),)
        )

    async def list_discount_codes(self) -> List[dict]:
        return await self._fetchall(
            "SELECT * FROM discount_codes ORDER BY created_at DESC LIMIT 20"
        )

    # ── Configs ───────────────────────────────────

    async def add_config(self, text: str, category: str = "free", remark: str = "") -> int:
        proto = text.split("://")[0] if "://" in text else "unknown"
        expiry = (datetime.now() + timedelta(days=30)).isoformat()
        return await self._insert(
            "INSERT INTO configs (config_text, category, protocol, remark, expires_at) VALUES (?, ?, ?, ?, ?)",
            (text, category, proto, remark, expiry),
        )

    async def delete_config(self, cfg_id: int) -> bool:
        return await self._execute("DELETE FROM configs WHERE id=?", (cfg_id,)) > 0

    async def get_all_configs(
        self, limit: int = 20, offset: int = 0, category: str = None
    ) -> List[dict]:
        if category:
            return await self._fetchall(
                "SELECT * FROM configs WHERE category=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (category, limit, offset),
            )
        return await self._fetchall(
            "SELECT * FROM configs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        )

    async def get_config(self, cfg_id: int) -> Optional[dict]:
        return await self._fetchone("SELECT * FROM configs WHERE id=?", (cfg_id,))

    async def get_active_configs(self, category: str, limit: int = 3) -> List[dict]:
        now = datetime.now().isoformat()
        return await self._fetchall(
            "SELECT * FROM configs WHERE active=1 AND category=? AND expires_at>? "
            "ORDER BY usage_count ASC, id DESC LIMIT ?",
            (category, now, limit),
        )

    async def count_configs(self, category: str = None) -> int:
        if category:
            row = await self._fetchone(
                "SELECT COUNT(*) AS cnt FROM configs WHERE category=?", (category,)
            )
        else:
            row = await self._fetchone("SELECT COUNT(*) AS cnt FROM configs")
        return row["cnt"] if row else 0

    async def increment_usage(self, cfg_id: int) -> None:
        await self._execute("UPDATE configs SET usage_count=usage_count+1 WHERE id=?", (cfg_id,))

    # ── Files ─────────────────────────────────────

    async def save_file(
        self, unique_id: str, file_id: str, ftype: str,
        name: str, size: int, mime: str, uploader_id: int = None
    ) -> bool:
        expires = (datetime.now() + timedelta(days=7)).isoformat()
        await self._execute(
            "INSERT OR IGNORE INTO file_links "
            "(file_unique_id, file_id, file_type, file_name, file_size, mime_type, uploader_id, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (unique_id, file_id, ftype, name, size, mime, uploader_id, expires),
        )
        return True

    async def get_file(self, unique_id: str) -> Optional[dict]:
        row = await self._fetchone(
            "SELECT * FROM file_links WHERE file_unique_id=?", (unique_id,)
        )
        if row:
            await self._execute(
                "UPDATE file_links SET download_count=download_count+1 WHERE file_unique_id=?",
                (unique_id,),
            )
        return row

    async def clean_expired_files(self) -> None:
        await self._execute("DELETE FROM file_links WHERE expires_at < datetime('now')")

    # ── Payments ──────────────────────────────────

    async def create_payment(
        self, user_id: int, amount: int, plan: str, receipt: str, discount_code: str = None
    ) -> int:
        return await self._insert(
            "INSERT INTO payments (user_id, amount, plan, receipt_file, discount_code) VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, plan, receipt, discount_code),
        )

    async def get_pending_payments(self) -> List[dict]:
        return await self._fetchall(
            "SELECT p.*, u.username, u.full_name "
            "FROM payments p JOIN users u ON p.user_id=u.telegram_id "
            "WHERE p.status='pending' ORDER BY p.created_at DESC"
        )

    async def get_payment(self, pay_id: int) -> Optional[dict]:
        return await self._fetchone("SELECT * FROM payments WHERE id=?", (pay_id,))

    async def confirm_payment(self, pay_id: int, admin_id: int) -> Optional[dict]:
        db = await self.connect()
        async with self._lock:
            pay = await self._fetchone(
                "SELECT * FROM payments WHERE id=? AND status='pending'", (pay_id,)
            )
            if not pay:
                return None
            plan_info = VIP_LEVELS.get(pay["plan"], {})
            days = plan_info.get("days", 30)
            wallet_bonus = plan_info.get("wallet_bonus", 0)

            user = await self._fetchone(
                "SELECT vip_expiry, is_vip FROM users WHERE telegram_id=?", (pay["user_id"],)
            )
            if user and user["is_vip"] and user["vip_expiry"]:
                try:
                    base = max(datetime.fromisoformat(user["vip_expiry"]), datetime.now())
                except Exception:
                    base = datetime.now()
            else:
                base = datetime.now()
            expiry = (base + timedelta(days=days)).isoformat()

            await db.execute(
                "UPDATE payments SET status='confirmed', confirmed_by=?, reviewed_at=datetime('now') WHERE id=?",
                (admin_id, pay_id),
            )
            await db.execute(
                "UPDATE users SET is_vip=1, vip_level=?, vip_expiry=?, total_spent=total_spent+? WHERE telegram_id=?",
                (pay["plan"], expiry, pay["amount"], pay["user_id"]),
            )
            if wallet_bonus > 0:
                await db.execute(
                    "UPDATE users SET balance=balance+? WHERE telegram_id=?",
                    (wallet_bonus, pay["user_id"]),
                )
                await db.execute(
                    "INSERT INTO wallet_transactions (user_id, amount, type, description) VALUES (?, ?, ?, ?)",
                    (pay["user_id"], wallet_bonus, "credit", f"VIP purchase bonus ({pay['plan']})"),
                )
            await db.execute("UPDATE bot_stats SET value=value+1 WHERE key='total_payments'")
            await db.execute(
                "UPDATE bot_stats SET value=value+? WHERE key='total_revenue'",
                (pay["amount"],),
            )
            await db.commit()

        result = dict(pay)
        result["wallet_bonus"] = wallet_bonus
        result["vip_expiry"] = expiry
        result["plan_info"] = plan_info
        return result

    async def reject_payment(self, pay_id: int, admin_id: int) -> Optional[dict]:
        pay = await self._fetchone(
            "SELECT * FROM payments WHERE id=? AND status='pending'", (pay_id,)
        )
        if not pay:
            return None
        await self._execute(
            "UPDATE payments SET status='rejected', confirmed_by=?, reviewed_at=datetime('now') WHERE id=?",
            (admin_id, pay_id),
        )
        return dict(pay)

    # ── Stats ─────────────────────────────────────

    async def get_stats(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        db = await self.connect()

        async def _count(sql, params=()):
            async with db.execute(sql, params) as c:
                r = await c.fetchone()
                return r[0] if r else 0

        return {
            "total_users": await _count("SELECT COUNT(*) FROM users"),
            "today_users": await _count(
                "SELECT COUNT(*) FROM users WHERE joined_at LIKE ?", (f"{today}%",)
            ),
            "total_vips": await _count("SELECT COUNT(*) FROM users WHERE is_vip=1"),
            "total_banned": await _count("SELECT COUNT(*) FROM users WHERE banned=1"),
            "total_configs_free": await _count(
                "SELECT COUNT(*) FROM configs WHERE category='free' AND active=1"
            ),
            "total_configs_vip": await _count(
                "SELECT COUNT(*) FROM configs WHERE category='vip' AND active=1"
            ),
            "total_downloads": await _count(
                "SELECT COALESCE(value,0) FROM bot_stats WHERE key='total_downloads'"
            ),
            "total_claims": await _count(
                "SELECT COALESCE(value,0) FROM bot_stats WHERE key='total_claims'"
            ),
            "total_revenue": await _count(
                "SELECT COALESCE(value,0) FROM bot_stats WHERE key='total_revenue'"
            ),
            "total_payments": await _count(
                "SELECT COALESCE(value,0) FROM bot_stats WHERE key='total_payments'"
            ),
            "pending_payments": await _count(
                "SELECT COUNT(*) FROM payments WHERE status='pending'"
            ),
            "total_wallet_balance": await _count(
                "SELECT COALESCE(SUM(balance),0) FROM users"
            ),
        }

    async def get_daily_stats(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        db = await self.connect()

        async def _count(sql, params=()):
            async with db.execute(sql, params) as c:
                r = await c.fetchone()
                return r[0] if r else 0

        return {
            "new_users_today": await _count(
                "SELECT COUNT(*) FROM users WHERE joined_at LIKE ?", (f"{today}%",)
            ),
            "new_users_yesterday": await _count(
                "SELECT COUNT(*) FROM users WHERE joined_at LIKE ?", (f"{yesterday}%",)
            ),
            "payments_today": await _count(
                "SELECT COUNT(*) FROM payments WHERE status='confirmed' AND reviewed_at LIKE ?",
                (f"{today}%",),
            ),
            "revenue_today": await _count(
                "SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='confirmed' AND reviewed_at LIKE ?",
                (f"{today}%",),
            ),
            "downloads_today": await _count(
                "SELECT COUNT(*) FROM user_logs WHERE action='yt_download' AND created_at LIKE ?",
                (f"{today}%",),
            ),
            "claims_today": await _count(
                "SELECT COUNT(*) FROM user_logs WHERE action='claim_config' AND created_at LIKE ?",
                (f"{today}%",),
            ),
            "active_vips": await _count("SELECT COUNT(*) FROM users WHERE is_vip=1"),
            "total_users": await _count("SELECT COUNT(*) FROM users"),
        }

    # ── Logs ──────────────────────────────────────

    async def add_log(self, user_id: int, action: str, details: str = "") -> None:
        await self._insert(
            "INSERT INTO user_logs (user_id, action, details) VALUES (?, ?, ?)",
            (user_id, action, details),
        )

    async def add_broadcast(self, admin_id: int, msg: str, sent: int, failed: int) -> None:
        await self._insert(
            "INSERT INTO broadcasts (admin_id, message, total_sent, total_failed) VALUES (?, ?, ?, ?)",
            (admin_id, msg, sent, failed),
        )

    # ── VIP ───────────────────────────────────────

    async def give_vip(self, tg_id: int, days: int, level: str = "silver") -> None:
        user = await self._fetchone(
            "SELECT vip_expiry, is_vip FROM users WHERE telegram_id=?", (tg_id,)
        )
        if user and user["is_vip"] and user["vip_expiry"]:
            try:
                base = max(datetime.fromisoformat(user["vip_expiry"]), datetime.now())
            except Exception:
                base = datetime.now()
        else:
            base = datetime.now()
        expiry = (base + timedelta(days=days)).isoformat()
        await self._execute(
            "UPDATE users SET is_vip=1, vip_level=?, vip_expiry=? WHERE telegram_id=?",
            (level, expiry, tg_id),
        )

    async def remove_admin(self, tg_id: int) -> bool:
        count = await self._execute("DELETE FROM admin_permissions WHERE admin_id=?", (tg_id,))
        await self._execute("UPDATE users SET role='user' WHERE telegram_id=?", (tg_id,))
        return count > 0

    # ── Proxies ───────────────────────────────────

    async def upsert_proxies(self, proxy_list: List[dict]) -> int:
        db = await self.connect()
        count = 0
        for p in proxy_list:
            try:
                await db.execute(
                    "INSERT INTO proxies (ip, port, protocol, country, speed_ms, anonymity, alive, last_check) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now')) "
                    "ON CONFLICT(ip, port, protocol) DO UPDATE SET "
                    "alive=1, speed_ms=excluded.speed_ms, last_check=datetime('now'), country=excluded.country, anonymity=excluded.anonymity",
                    (p["ip"], p["port"], p.get("protocol", "http"),
                     p.get("country", ""), p.get("speed_ms", 0), p.get("anonymity", "unknown")),
                )
                count += 1
            except Exception:
                pass
        await db.commit()
        return count

    async def get_alive_proxies(
        self, protocol: str = None, limit: int = 30
    ) -> List[dict]:
        if protocol:
            return await self._fetchall(
                "SELECT * FROM proxies WHERE alive=1 AND protocol=? ORDER BY speed_ms ASC LIMIT ?",
                (protocol, limit),
            )
        return await self._fetchall(
            "SELECT * FROM proxies WHERE alive=1 ORDER BY speed_ms ASC LIMIT ?", (limit,)
        )

    async def mark_proxy_dead(self, proxy_id: int) -> None:
        await self._execute("UPDATE proxies SET alive=0 WHERE id=?", (proxy_id,))

    async def get_proxy_stats(self) -> dict:
        db = await self.connect()

        async def _count(sql, params=()):
            async with db.execute(sql, params) as c:
                r = await c.fetchone()
                return r[0] if r else 0

        return {
            "total": await _count("SELECT COUNT(*) FROM proxies"),
            "alive": await _count("SELECT COUNT(*) FROM proxies WHERE alive=1"),
            "http": await _count("SELECT COUNT(*) FROM proxies WHERE alive=1 AND protocol='http'"),
            "https": await _count("SELECT COUNT(*) FROM proxies WHERE alive=1 AND protocol='https'"),
            "socks4": await _count("SELECT COUNT(*) FROM proxies WHERE alive=1 AND protocol='socks4'"),
            "socks5": await _count("SELECT COUNT(*) FROM proxies WHERE alive=1 AND protocol='socks5'"),
        }

    async def clean_dead_proxies(self, older_than_hours: int = 24) -> int:
        cutoff = (datetime.now() - timedelta(hours=older_than_hours)).isoformat()
        return await self._execute(
            "DELETE FROM proxies WHERE alive=0 AND last_check < ?", (cutoff,)
        )

    # ── Backup ────────────────────────────────────

    def backup(self) -> str:
        dst = f"backups/db_{datetime.now():%Y%m%d_%H%M%S}.sqlite"
        shutil.copy2(DB_NAME, dst)
        return dst

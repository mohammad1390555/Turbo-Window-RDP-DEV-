"""
Inline keyboard builders.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import SUPER_ADMIN_ID


def is_admin_check(tg_id: int, db_role: str) -> bool:
    return tg_id == SUPER_ADMIN_ID or db_role in ("admin", "super_admin")


def kb_main(uid: int, role: str = "user") -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("Config", callback_data="claim"),
            InlineKeyboardButton("Download", callback_data="yt"),
        ],
        [
            InlineKeyboardButton("File2Link", callback_data="file"),
            InlineKeyboardButton("VIP", callback_data="vip"),
        ],
        [
            InlineKeyboardButton("Wallet", callback_data="wallet"),
            InlineKeyboardButton("Profile", callback_data="profile"),
        ],
        [
            InlineKeyboardButton("Proxy", callback_data="proxy_menu"),
            InlineKeyboardButton("Speed Test", callback_data="speed_test"),
        ],
        [
            InlineKeyboardButton("Referral", callback_data="referral"),
            InlineKeyboardButton("Help", callback_data="help"),
        ],
    ]
    if is_admin_check(uid, role):
        rows.append([InlineKeyboardButton("Admin Panel", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


def kb_back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Home", callback_data="main")]]
    )


def kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cancel", callback_data="main")]]
    )


def kb_admin(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("Configs", callback_data="admin_configs"),
            InlineKeyboardButton("Payments", callback_data="admin_payments"),
        ],
        [
            InlineKeyboardButton("Users", callback_data="admin_users"),
            InlineKeyboardButton("Broadcast", callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("Give VIP", callback_data="admin_give_vip"),
            InlineKeyboardButton("Discount", callback_data="admin_discount"),
        ],
        [
            InlineKeyboardButton("Wallet Charge", callback_data="admin_wallet_charge"),
            InlineKeyboardButton("Proxies", callback_data="admin_proxy"),
        ],
    ]
    if uid == SUPER_ADMIN_ID:
        rows += [
            [
                InlineKeyboardButton("Admins", callback_data="super_admin"),
                InlineKeyboardButton("Full Stats", callback_data="full_stats"),
            ],
            [
                InlineKeyboardButton("DB Backup", callback_data="admin_backup"),
                InlineKeyboardButton("YT Cookie", callback_data="set_cookie"),
            ],
        ]
    rows.append([InlineKeyboardButton("Home", callback_data="main")])
    return InlineKeyboardMarkup(rows)


def kb_proxy_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("HTTP/S Proxies", callback_data="proxy_http"),
            InlineKeyboardButton("SOCKS5 Proxies", callback_data="proxy_socks5"),
        ],
        [
            InlineKeyboardButton("SOCKS4 Proxies", callback_data="proxy_socks4"),
            InlineKeyboardButton("All Proxies", callback_data="proxy_all"),
        ],
        [
            InlineKeyboardButton("Proxy Stats", callback_data="proxy_stats"),
            InlineKeyboardButton("Check Proxy", callback_data="proxy_check"),
        ],
        [InlineKeyboardButton("Home", callback_data="main")],
    ])

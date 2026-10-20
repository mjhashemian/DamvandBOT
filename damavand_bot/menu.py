"""User-facing reply-menu buttons and their persistent setting keys."""

MENU_BUTTONS = (
    ("buy", "🛒 خرید سرویس"),
    ("renew", "♻️ تمدید سرویس"),
    ("services", "📋 سرویس‌های من"),
    ("trial", "🎁 سرویس تست"),
    ("support", "💬 پشتیبانی"),
    ("channel", "📢 کانال ما"),
)

MENU_SETTING_KEYS = {name: f"MENU_{name.upper()}_ENABLED" for name, _ in MENU_BUTTONS}
DEFAULT_ENABLED_BUTTONS = frozenset(name for name, _ in MENU_BUTTONS)

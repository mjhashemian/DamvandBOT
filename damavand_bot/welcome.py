"""Default Telegram profile and first-message copy."""

from pathlib import Path


WELCOME_BANNER = Path(__file__).parent / "assets" / "welcome-banner.png"

DEFAULT_BOT_DESCRIPTION = (
    "به دماوند VPN خوش اومدی 🌄\n"
    "پلن‌های آی‌پی ثابت برای ترید، برنامه‌نویسی، ابزارهای هوش مصنوعی و وب‌گردی رو ببین؛ "
    "سرویس بخر یا تمدید کن، لینک و وضعیت سرویس‌هات رو بگیر و با پشتیبانی در ارتباط باش."
)

DEFAULT_WELCOME_MESSAGE = (
    "سلام {name} عزیز! به دماوند VPN خوش اومدی 🌄\n\n"
    "اینجا می‌تونی پلن‌های آی‌پی ثابت برای ترید، برنامه‌نویسی، ابزارهای هوش مصنوعی و وب‌گردی رو ببینی، "
    "سرویس بخری یا تمدید کنی و لینک و وضعیت سرویس‌هات رو در «سرویس‌های من» ببینی.\n\n"
    "برای شروع، {requirements}. اگر سوالی داشتی، پشتیبانی کنارته 💬"
)


def render_welcome(message: str, name: str, channel_required: bool) -> str:
    requirements = "عضویت در کانال و قوانین رو تأیید کن" if channel_required else "قوانین رو تأیید کن"
    return message.replace("{name}", name).replace("{requirements}", requirements)

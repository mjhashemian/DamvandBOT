"""Default Telegram profile and first-message copy."""

from pathlib import Path


WELCOME_BANNER = Path(__file__).parent / "assets" / "welcome-banner.png"
REFERRAL_BANNER = Path(__file__).parent / "assets" / "referral-banner.png"
CUSTOM_WELCOME_BANNER_NAME = "welcome-banner-custom.jpg"

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


def welcome_banner_path(config_dir: Path) -> Path:
    custom = config_dir / CUSTOM_WELCOME_BANNER_NAME
    return custom if custom.is_file() else WELCOME_BANNER


def welcome_photo_text(title: str, details: str, welcome_message: str) -> tuple[str, str | None]:
    prefix = "\n\n".join(part.strip() for part in (title, details) if part.strip())
    combined = "\n\n".join(part for part in (prefix, welcome_message) if part)
    if len(combined) <= 1000:
        return combined, None
    return prefix, welcome_message

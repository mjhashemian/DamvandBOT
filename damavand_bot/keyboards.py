from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from .config import Config
from .db import Plan


BUY = "🛒 خرید سرویس"
RENEW = "♻️ تمدید سرویس"
MY_SERVICES = "📋 سرویس‌های من"
TRIAL = "🎁 سرویس تست"
SUPPORT = "💬 پشتیبانی"
CHANNEL = "📢 کانال ما"
DISCOUNTS = "🎟 مدیریت کد تخفیف"


def main_menu(admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BUY), KeyboardButton(text=RENEW)],
        [KeyboardButton(text=MY_SERVICES), KeyboardButton(text=TRIAL)],
        [KeyboardButton(text=SUPPORT), KeyboardButton(text=CHANNEL)],
    ]
    if admin:
        rows.append([KeyboardButton(text=DISCOUNTS)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="یکی از گزینه‌ها رو انتخاب کن…",
    )


def join_keyboard(config: Config) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="عضویت در کانال", url=config.channel_url)],
            [InlineKeyboardButton(text="✅ عضو شدم؛ بررسی کن", callback_data="check_join")],
        ]
    )


def terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ قوانین را می‌پذیرم", callback_data="accept_terms")],
            [InlineKeyboardButton(text="❌ نمی‌پذیرم", callback_data="reject_terms")],
        ]
    )


def plans_keyboard(
    plans: list[Plan], action: str, target: str = "", demo_mode: bool = False, card_payment: bool = False
) -> InlineKeyboardMarkup:
    rows = []
    for plan in plans:
        if demo_mode or card_payment:
            suffix = f" • {plan.toman_price:,} تومان"
        else:
            suffix = f" • {plan.stars_price} ⭐" if plan.stars_price > 0 else " • در حال تنظیم"
        callback = f"plan:{action}:{plan.code}:{target}" if target else f"plan:{action}:{plan.code}"
        rows.append([InlineKeyboardButton(text=f"{plan.title_fa}{suffix}", callback_data=callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def demo_confirmation_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأیید و ساخت سرویس", callback_data=f"demo:confirm:{order_id}"
                )
            ],
            [InlineKeyboardButton(text="❌ انصراف", callback_data=f"demo:cancel:{order_id}")],
        ]
    )


def card_review_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ پرداخت تأیید شد؛ ساخت سرویس", callback_data=f"card:approve:{order_id}"),
                InlineKeyboardButton(text="❌ رد پرداخت", callback_data=f"card:reject:{order_id}"),
            ]
        ]
    )


def card_receipt_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📷 ارسال رسید پرداخت", callback_data=f"card:receipt:{order_id}")]
        ]
    )


def discount_choice_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎟 کد تخفیف دارم", callback_data=f"discount:yes:{order_id}")],
        [InlineKeyboardButton(text="بدون کد تخفیف ادامه بده", callback_data=f"discount:no:{order_id}")],
    ])


def discount_admin_keyboard(codes: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ تعریف کد تخفیف", callback_data="discount:admin:add")]]
    rows.extend(
        [InlineKeyboardButton(text=f"🗑 غیرفعال کردن {code}", callback_data=f"discount:admin:off:{code}")]
        for code in codes
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def services_keyboard(emails: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=email, callback_data=f"service:{email}")]
            for email in emails
        ]
    )


def service_actions(email: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="♻️ تمدید این سرویس", callback_data=f"renew:{email}")]
        ]
    )

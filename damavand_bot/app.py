from __future__ import annotations

import asyncio
import html
import io
import logging
import os
import re
from datetime import datetime, timezone

import qrcode
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from .config import Config
from .db import Database, Plan
from .keyboards import (
    BUY,
    CHANNEL,
    DISCOUNTS,
    MY_SERVICES,
    RENEW,
    SUPPORT,
    TRIAL,
    card_receipt_keyboard,
    card_review_keyboard,
    demo_confirmation_keyboard,
    discount_admin_keyboard,
    discount_choice_keyboard,
    join_keyboard,
    main_menu,
    plans_keyboard,
    service_actions,
    services_keyboard,
    terms_keyboard,
)
from .xui import XUIClient, XUIError


logger = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
runtime_app: App | None = None

WELCOME = (
    "سلام <b>{name}</b> عزیزم! به دماوند VPN خیلی خوش اومدی ⭐️\n"
    "خیالت راحت، واسه انتخاب بهترین سرور مناسب نیازت، گام‌به‌گام کنارتیم 😎\n"
    "راستی، هر وقت به مشکلی خوردی یا سوالی داشتی، پشتیبانی ما کنارته 💬"
)

STARTUP_WELCOME = (
    "سلام <b>{name}</b> عزیز! 🌄\n\n"
    "به <b>دماوند VPN</b> خوش اومدی. اینجا می‌تونی سرویس مناسب خودت رو انتخاب کنی، "
    "وضعیت سرویس‌هات رو ببینی و هر زمان لازم بود تمدیدشون کنی.\n\n"
    "برای شروع، {requirements}؛ بعد همه‌چیز از منوی پایین در دسترسته. 🤝"
)

BUY_INTRO = (
    "همه سرویس‌ها دارای آی‌پی ثابت هستن که برای ترید، سرویس‌های برنامه‌نویسی، "
    "هوش مصنوعی و وب‌گردی کاملاً مناسب هستن و توی این سرویس‌ها امنیت شما "
    "دارای اهمیت ویژه‌ای است.\n\n"
    "مدت اعتبار و حجم هر پلن در فهرست پلن‌ها مشخص شده است."
)


class NewServiceName(StatesGroup):
    waiting_for_name = State()


class DiscountCodeInput(StatesGroup):
    waiting_for_code = State()


class AdminDiscountInput(StatesGroup):
    waiting_for_definition = State()


class ChannelMembershipUnavailable(RuntimeError):
    """Telegram has not granted the bot access to channel membership data."""


class App:
    def __init__(self, config: Config, bot: Bot, db: Database, xui: XUIClient):
        self.config = config
        self.bot = bot
        self.db = db
        self.xui = xui

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.config.admin_ids

    async def is_channel_member(self, user_id: int) -> bool:
        if not self.config.channel_join_required:
            return True
        if self.is_admin(user_id):
            return True
        try:
            chat_id = int(self.config.channel_id) if self.config.channel_id else self.config.channel_username
            member = await self.bot.get_chat_member(chat_id, user_id)
        except TelegramBadRequest as exc:
            logger.exception("Channel membership check failed")
            raise ChannelMembershipUnavailable from exc
        if member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }:
            return True
        return bool(member.status == ChatMemberStatus.RESTRICTED and getattr(member, "is_member", False))

    async def gate(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        if not user:
            return False
        await self.db.upsert_user(user.id, user.username, user.full_name)
        answer = event.message.answer if isinstance(event, CallbackQuery) else event.answer
        try:
            is_member = await self.is_channel_member(user.id)
        except ChannelMembershipUnavailable:
            await answer(
                "فعلاً امکان بررسی عضویت کانال وجود ندارد. لطفاً به پشتیبانی اطلاع بده تا دسترسی بات به کانال بررسی شود."
            )
            return False
        if not is_member:
            await answer(
                "برای استفاده از بات اول باید عضو کانال دماوند بشی، بعد دکمهٔ بررسی رو بزن.",
                reply_markup=join_keyboard(self.config),
            )
            return False
        terms = self.config.read_terms()
        if not terms:
            await answer(
                "متن قوانین هنوز توسط مدیریت ثبت نشده؛ فروش و ساخت سرویس تا ثبت قوانین غیرفعال است."
            )
            return False
        if not await self.db.has_accepted_terms(user.id, self.config.terms_version):
            await answer(
                f"<b>قوانین و شرایط استفاده</b>\n\n{html.escape(terms)}",
                reply_markup=terms_keyboard(),
            )
            return False
        return True

    async def show_welcome(self, message: Message) -> None:
        name = html.escape(message.from_user.first_name or "دوست")
        await message.answer(WELCOME.format(name=name), reply_markup=main_menu(self.is_admin(message.from_user.id), self.config.channel_join_required))

    async def notify_admins(self, text: str) -> None:
        for admin_id in self.config.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
            except Exception:
                logger.exception("Could not notify admin %s", admin_id)


def _app(event: Message | CallbackQuery | PreCheckoutQuery) -> App:
    if runtime_app is None:
        raise RuntimeError("Application is not initialized")
    return runtime_app


def _client_email(user_id: int, suffix: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", suffix)[:20]
    return f"tg{user_id}-{safe}-{int(datetime.now(timezone.utc).timestamp())}"


def _client_from_envelope(envelope: dict) -> dict:
    return envelope.get("client", envelope)


def _format_expiry(expiry_ms: int) -> str:
    if not expiry_ms:
        return "نامحدود"
    return datetime.fromtimestamp(expiry_ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _format_bytes(value: int) -> str:
    value = max(0, int(value))
    if value >= 1024**3:
        return f"{value / 1024**3:.2f} GB"
    return f"{value / 1024**2:.0f} MB"


def _remaining_days(expiry_ms: int) -> int | None:
    if not expiry_ms:
        return None
    seconds = expiry_ms / 1000 - datetime.now(timezone.utc).timestamp()
    return max(0, int((seconds + 86_399) // 86_400))


def _valid_client_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,31}", value))


async def _client_name_available(app: App, name: str) -> bool:
    try:
        await app.xui.get_client(name)
        return False
    except XUIError as exc:
        if "not found" in str(exc).lower():
            return True
        raise


async def _begin_checkout(
    app: App,
    *,
    order,
    plan: Plan | None,
) -> None:
    chat_id = order["telegram_id"]
    user_id = chat_id
    action = order["kind"]
    target_email = order["target_email"]
    order_id = order["id"]
    plan_title = plan.title_fa if plan else order["plan_code"]
    if app.config.demo_mode:
        verb = "تمدید" if action == "renew" else "ساخت"
        name_line = (
            f"نام سرویس: <code>{html.escape(target_email)}</code>\n"
            if action == "buy" and target_email
            else ""
        )
        await app.bot.send_message(
            chat_id,
            "🧪 <b>تأیید پلن آزمایشی</b>\n\n"
            f"{name_line}"
            f"پلن: <b>{html.escape(plan_title)}</b>\n"
            f"حجم: <b>{order['traffic_gb']} گیگابایت</b>\n"
            f"اعتبار: <b>{order['duration_days']} روز</b>\n"
            f"قیمت نمایشی: <b>{order['toman_amount']:,} تومان</b>\n\n"
            f"در این مرحله پرداختی انجام نمی‌شود. برای {verb} سرویس تأیید کن.",
            reply_markup=demo_confirmation_keyboard(order_id),
        )
        return
    if app.config.card_payment_enabled:
        if not await app.db.begin_card_payment(order_id, user_id):
            raise RuntimeError("Could not initialize card-payment order")
        verb = "تمدید" if action == "renew" else "ساخت"
        rial_price = order["toman_amount"] * 10
        await app.bot.send_message(
            chat_id,
            f"لطفا مبلغ <code>{order['toman_amount']:,} تومان</code> "
            f"(<code>{rial_price:,} ریال</code>) رو به مشخصات کارت زیر وارد کنید:\n\n"
            f"شماره کارت: <code>{html.escape(app.config.card_payment_card_number)}</code>\n"
            f"به نام: <b>{html.escape(app.config.card_payment_holder)}</b>\n\n"
            f"پلن انتخابی: <b>{html.escape(plan_title)}</b> برای {verb}\n\n"
            f"{html.escape(app.config.card_payment_instructions) + chr(10) + chr(10) if app.config.card_payment_instructions else ''}"
            "بعد از انجام تراکنش دکمهٔ <b>ارسال رسید پرداخت</b> رو لمس کنید و "
            "عکس رسیدتون رو برامون بفرستید.\n\n"
            "لطفا پیش از تأیید انتقال، حتما نام صاحب کارت رو چک کنید؛ "
            "مسئولیت واریز اشتباه با خودتونه.\n\n"
            "سرویس فقط پس از تأیید مدیریت ساخته می‌شود.",
            reply_markup=card_receipt_keyboard(order_id),
        )
        return
    if order["stars_amount"] <= 0:
        await app.bot.send_message(chat_id, "قیمت این پلن به Stars هنوز توسط مدیریت تنظیم نشده است.")
        return
    await app.bot.send_invoice(
        chat_id=chat_id,
        title=f"سرویس دماوند VPN - {plan_title}",
        description=f"اعتبار {order['duration_days']} روز، بدون محدودیت دستگاه",
        payload=order["invoice_payload"],
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=plan_title, amount=order["stars_amount"])],
        start_parameter=f"damavand-{order_id}",
    )


async def _offer_discount(app: App, *, user_id: int, action: str, plan: Plan, target_email: str | None) -> None:
    order_id, _ = await app.db.create_order(user_id, action, plan, target_email, await_discount=True)
    await app.bot.send_message(
        user_id,
        f"قیمت اصلی پلن <b>{html.escape(plan.title_fa)}</b>: <code>{plan.toman_price:,} تومان</code>\n\n"
        "کد تخفیف داری؟",
        reply_markup=discount_choice_keyboard(order_id),
    )


async def _finish_discount(app: App, order_id: int, user_id: int, code: str | None) -> bool:
    order = await app.db.finalize_discount_order(order_id, user_id, code)
    if not order:
        return False
    plan = await app.db.get_plan(order["plan_code"])
    if code and not order["discount_code"]:
        await app.bot.send_message(
            user_id, f"این کد تخفیف معتبر نیست. مبلغ اصلی: <code>{order['toman_amount']:,} تومان</code>."
        )
    elif order["discount_percent"]:
        await app.bot.send_message(
            user_id,
            f"✅ کد تخفیف {order['discount_percent']}٪ اعمال شد. "
            f"مبلغ نهایی: <code>{order['toman_amount']:,} تومان</code>."
        )
    else:
        await app.bot.send_message(user_id, f"مبلغ نهایی: <code>{order['toman_amount']:,} تومان</code>.")
    if order["discount_percent"] != 100:
        await _begin_checkout(app, order=order, plan=plan)
        return True
    try:
        envelope, email = await _provision_order(app, order, user_id)
        await app.db.mark_order_result(order_id, "complete", email=email)
    except Exception as exc:
        logger.exception("Free discount provisioning failed for order %s", order_id)
        await app.db.mark_order_result(order_id, "provision_error", error=str(exc)[:1000])
        await app.bot.send_message(user_id, "ساخت سرویس رایگان با خطا روبه‌رو شد؛ پشتیبانی پیگیری می‌کند.")
        await app.notify_admins(f"🚨 خطای ساخت سفارش تخفیفی #{order_id}: <code>{html.escape(str(exc)[:1000])}</code>")
        return True
    try:
        await _send_credentials(app, user_id, envelope, email)
    except Exception as exc:
        logger.exception("Free discount delivery failed for order %s", order_id)
        await app.db.mark_order_result(order_id, "complete", email=email, error=str(exc)[:1000])
        await app.notify_admins(f"سفارش تخفیفی #{order_id} ساخته شد اما ارسال اطلاعات خطا داشت؛ /resendorder {order_id}")
        return True
    await app.notify_admins(f"🎟 سفارش #{order_id} با تخفیف ۱۰۰٪ ساخته و برای کاربر ارسال شد.")
    return True


async def _send_credentials(app: App, chat_id: int, envelope: dict, email: str) -> None:
    subscription = app.xui.subscription_url(envelope)
    links = await app.xui.links(email)
    image = qrcode.make(subscription)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    await app.bot.send_photo(
        chat_id,
        BufferedInputFile(buffer.getvalue(), filename="damavand-subscription.png"),
        caption=(
            "✅ سرویس با موفقیت ساخته شد.\n\n"
            f"<b>لینک اشتراک:</b>\n<code>{html.escape(subscription)}</code>\n\n"
            "QR بالا را می‌تونی داخل برنامهٔ سازگار اسکن کنی."
        ),
    )
    if links:
        await app.bot.send_message(chat_id, "<b>لینک جداگانهٔ کانفیگ‌ها:</b>")
        for index, link in enumerate(links, start=1):
            body = f"<b>کانفیگ {index}</b>\n<code>{html.escape(link)}</code>"
            if len(body) <= 3900:
                await app.bot.send_message(chat_id, body)
            else:
                await app.bot.send_document(
                    chat_id,
                    BufferedInputFile(link.encode("utf-8"), filename=f"damavand-config-{index}.txt"),
                    caption=f"کانفیگ {index}",
                )


@router.message(CommandStart())
async def start(message: Message) -> None:
    app = _app(message)
    user = message.from_user
    is_new_user = not await app.db.user_exists(user.id)
    await app.db.upsert_user(user.id, user.username, user.full_name)
    claimed_services = await app.db.claim_pending_services(user.id, user.username)
    if is_new_user:
        requirements = "عضویت در کانال و قوانین رو تأیید کن" if app.config.channel_join_required else "قوانین رو تأیید کن"
        await message.answer(STARTUP_WELCOME.format(name=html.escape(user.full_name), requirements=requirements))
    if not await app.gate(message):
        return
    await app.show_welcome(message)
    if claimed_services:
        await message.answer(
            "✅ سرویس قبلی‌ات به حسابت اضافه شد. از بخش «سرویس‌های من» می‌تونی لینک اشتراکت رو ببینی."
        )


@router.callback_query(F.data == "check_join")
async def check_join(callback: CallbackQuery) -> None:
    app = _app(callback)
    await callback.answer()
    if not app.config.channel_join_required:
        if await app.gate(callback):
            await callback.message.answer(
                WELCOME.format(name=html.escape(callback.from_user.first_name or "دوست")),
                reply_markup=main_menu(app.is_admin(callback.from_user.id), False),
            )
        return
    try:
        is_member = await app.is_channel_member(callback.from_user.id)
    except ChannelMembershipUnavailable:
        await callback.message.answer(
            "فعلاً امکان بررسی عضویت کانال وجود ندارد. لطفاً به پشتیبانی اطلاع بده تا دسترسی بات به کانال بررسی شود."
        )
        return
    if not is_member:
        await callback.message.answer(
            "هنوز عضویتت تأیید نشد. عضو کانال شو و دوباره بررسی کن.",
            reply_markup=join_keyboard(app.config),
        )
        return
    terms = app.config.read_terms()
    if not terms:
        await callback.message.answer("متن قوانین هنوز توسط مدیریت ثبت نشده است.")
        return
    await callback.message.answer(
        f"<b>قوانین و شرایط استفاده</b>\n\n{html.escape(terms)}",
        reply_markup=terms_keyboard(),
    )


@router.callback_query(F.data == "accept_terms")
async def accept_terms(callback: CallbackQuery) -> None:
    app = _app(callback)
    try:
        is_member = await app.is_channel_member(callback.from_user.id)
    except ChannelMembershipUnavailable:
        await callback.answer("بررسی عضویت کانال فعلاً ممکن نیست؛ لطفاً به پشتیبانی اطلاع بده.", show_alert=True)
        return
    if not is_member:
        await callback.answer("ابتدا باید عضو کانال باشی.", show_alert=True)
        return
    if not app.config.read_terms():
        await callback.answer("قوانین هنوز ثبت نشده است.", show_alert=True)
        return
    await app.db.accept_terms(callback.from_user.id, app.config.terms_version)
    await callback.answer("قوانین پذیرفته شد ✅")
    await callback.message.answer(
        WELCOME.format(name=html.escape(callback.from_user.first_name or "دوست")),
        reply_markup=main_menu(app.is_admin(callback.from_user.id), app.config.channel_join_required),
    )


@router.callback_query(F.data == "reject_terms")
async def reject_terms(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("بدون پذیرش قوانین امکان استفاده از فروشگاه وجود ندارد.")


@router.message(F.text == BUY)
async def buy(message: Message) -> None:
    app = _app(message)
    if not await app.gate(message):
        return
    plans = await app.db.list_plans()
    price_list = "\n".join(
        f"• {plan.traffic_gb}GB — {plan.toman_price:,} تومان" for plan in plans
    )
    await message.answer(
        f"{BUY_INTRO}\n\n<b>پلن‌ها:</b>\n{price_list}\n\n"
        + (
            "🧪 حالت آزمایشی فعال است؛ مبلغی دریافت نمی‌شود و پس از تأیید، سرویس فوراً ساخته خواهد شد."
            if app.config.demo_mode
            else (
                "💳 پرداخت کارت‌به‌کارت است؛ پس از ارسال رسید و تأیید مدیریت، سرویس ساخته می‌شود."
                if app.config.card_payment_enabled
                else "پرداخت داخل تلگرام با Stars انجام می‌شود."
            )
        ),
        reply_markup=plans_keyboard(
            plans, "buy", demo_mode=app.config.demo_mode, card_payment=app.config.card_payment_enabled
        ),
    )


@router.callback_query(F.data.startswith("plan:"))
async def select_plan(callback: CallbackQuery, state: FSMContext) -> None:
    app = _app(callback)
    if not await app.gate(callback):
        await callback.answer()
        return
    parts = callback.data.split(":", 3)
    if len(parts) < 3:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    _, action, code, *target = parts
    plan = await app.db.get_plan(code)
    if not plan:
        await callback.answer("این پلن پیدا نشد.", show_alert=True)
        return
    target_email = target[0] if target else None
    if action not in {"buy", "renew"}:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    if action == "buy" and target_email:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    if action == "renew":
        if not target_email:
            await callback.answer("سرویس تمدید مشخص نیست.", show_alert=True)
            return
        owned = await app.xui.clients_for_telegram(callback.from_user.id)
        if not any(_client_from_envelope(item).get("email") == target_email for item in owned):
            await callback.answer("این سرویس متعلق به حساب شما نیست.", show_alert=True)
            return
    if action == "buy" and not target_email:
        await state.set_state(NewServiceName.waiting_for_name)
        await state.update_data(plan_code=plan.code, action=action)
        await callback.answer()
        await callback.message.answer(
            "یک نام دلخواه برای سرویس وارد کن.\n\n"
            "نام باید ۳ تا ۳۲ کاراکتر، انگلیسی و شامل حروف، عدد، <code>_</code> یا <code>-</code> باشد.\n"
            "مثال: <code>my_panel</code>"
        )
        return
    await callback.answer()
    await state.clear()
    await _offer_discount(app, user_id=callback.from_user.id, action=action, plan=plan, target_email=target_email)


@router.message(NewServiceName.waiting_for_name)
async def receive_service_name(message: Message, state: FSMContext) -> None:
    app = _app(message)
    if not await app.gate(message):
        return
    name = (message.text or "").strip()
    if not _valid_client_name(name):
        await message.answer(
            "نام معتبر نیست. یک نام انگلیسی ۳ تا ۳۲ کاراکتری با حروف، عدد، <code>_</code> یا <code>-</code> وارد کن."
        )
        return
    try:
        available = await _client_name_available(app, name)
    except XUIError:
        logger.exception("Could not check client-name availability")
        await message.answer("بررسی نام موقتاً ممکن نیست؛ چند لحظهٔ دیگه دوباره امتحان کن.")
        return
    if not available:
        await message.answer("این نام قبلاً استفاده شده؛ لطفاً یک نام دیگه وارد کن.")
        return
    data = await state.get_data()
    plan = await app.db.get_plan(str(data.get("plan_code", "")))
    if not plan:
        await state.clear()
        await message.answer("پلن انتخاب‌شده پیدا نشد؛ دوباره از منوی خرید شروع کن.")
        return
    await state.clear()
    await _offer_discount(app, user_id=message.from_user.id, action="buy", plan=plan, target_email=name)


@router.callback_query(F.data.startswith("discount:yes:"))
async def ask_discount_code(callback: CallbackQuery, state: FSMContext) -> None:
    app = _app(callback)
    if not await app.gate(callback):
        await callback.answer()
        return
    try:
        order_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    if not await app.db.discount_order_waiting(order_id, callback.from_user.id):
        await callback.answer("این سفارش قبلاً ادامه داده شده است.", show_alert=True)
        return
    await state.set_state(DiscountCodeInput.waiting_for_code)
    await state.update_data(discount_order_id=order_id)
    await callback.answer()
    await callback.message.answer("کد تخفیف رو بفرست؛ مثلاً <code>LASTOFUS-35</code>. اگر کد نداری، دکمهٔ «بدون کد تخفیف ادامه بده» رو بزن.")


@router.callback_query(F.data.startswith("discount:no:"))
async def skip_discount_code(callback: CallbackQuery, state: FSMContext) -> None:
    app = _app(callback)
    if not await app.gate(callback):
        await callback.answer()
        return
    try:
        order_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    if not await _finish_discount(app, order_id, callback.from_user.id, None):
        await callback.message.answer("این سفارش قبلاً ادامه داده شده است.")


@router.message(DiscountCodeInput.waiting_for_code)
async def receive_discount_code(message: Message, state: FSMContext) -> None:
    app = _app(message)
    if not await app.gate(message):
        return
    if not message.text:
        await message.answer("لطفاً کد تخفیف را به‌صورت متن بفرست.")
        return
    data = await state.get_data()
    order_id = data.get("discount_order_id")
    if not isinstance(order_id, int):
        await state.clear()
        await message.answer("سفارش پیدا نشد؛ لطفاً دوباره پلن را انتخاب کن.")
        return
    await state.clear()
    if not await _finish_discount(app, order_id, message.from_user.id, message.text.strip().upper()):
        await message.answer("این سفارش قبلاً ادامه داده شده است؛ دوباره پلن را انتخاب کن.")


@router.message(F.photo)
async def receive_card_receipt(message: Message) -> None:
    app = _app(message)
    order_id = await app.db.selected_card_receipt(message.from_user.id)
    if order_id is None:
        return
    if not await app.gate(message):
        return
    photo = message.photo[-1]
    order = await app.db.submit_card_receipt(order_id, message.from_user.id, photo.file_id, photo.file_unique_id)
    if not order:
        await message.answer("این رسید قبلاً برای سفارشی ثبت شده یا سفارش دیگر منتظر رسید نیست. عکس رسید دیگری بفرست یا با پشتیبانی تماس بگیر.")
        return
    plan = await app.db.get_plan(order["plan_code"])
    plan_title = plan.title_fa if plan else order["plan_code"]
    caption = (
        f"💳 <b>رسید کارت‌به‌کارت جدید — سفارش #{order['id']}</b>\n"
        f"کاربر: <code>{message.from_user.id}</code>\n"
        f"نام کاربری: @{html.escape(message.from_user.username or 'ندارد')}\n"
        f"نوع: {'تمدید' if order['kind'] == 'renew' else 'خرید'}\n"
        f"پلن: <b>{html.escape(plan_title)}</b>\n"
        f"مبلغ مورد انتظار: <b>{(order['toman_amount'] or (plan.toman_price if plan else 0)):,} تومان</b>\n"
        "⚠️ قبل از تأیید، واریز واقعی، مبلغ و شماره کارت مقصد را در حساب بانکی بررسی کنید."
    )
    delivered = False
    for admin_id in app.config.admin_ids:
        try:
            await app.bot.send_photo(
                admin_id,
                photo.file_id,
                caption=caption,
                reply_markup=card_review_keyboard(order["id"]),
            )
            delivered = True
        except Exception:
            logger.exception("Could not deliver card receipt for order %s to admin %s", order["id"], admin_id)
    await message.answer(
        "✅ رسید ثبت شد. پس از تأیید پرداخت توسط مدیریت، سرویس برایت ارسال می‌شود."
        if delivered else
        "رسید ثبت شد، اما ارسال آن به مدیریت موقتاً مشکل داشت. لطفاً با پشتیبانی تماس بگیر و شماره سفارش را بگو: " + str(order["id"])
    )


@router.callback_query(F.data.startswith("card:receipt:"))
async def begin_card_receipt_upload(callback: CallbackQuery, state: FSMContext) -> None:
    app = _app(callback)
    try:
        order_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    if not await app.db.select_card_receipt(order_id, callback.from_user.id):
        await callback.answer("این درخواست دیگر منتظر رسید نیست.", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await callback.message.answer("حالا عکس رسید پرداخت را به‌صورت تصویر ارسال کن.")


@router.callback_query(F.data.startswith("card:approve:"))
async def approve_card_payment(callback: CallbackQuery) -> None:
    app = _app(callback)
    if not app.is_admin(callback.from_user.id):
        await callback.answer("اجازهٔ تأیید پرداخت نداری.", show_alert=True)
        return
    try:
        order_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    order = await app.db.claim_card_payment_approval(order_id, callback.from_user.id)
    if not order:
        await callback.answer("این رسید قبلاً بررسی شده است.", show_alert=True)
        return
    await callback.answer("پرداخت تأیید شد؛ سرویس در حال ساخت است…")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.exception("Could not remove review buttons for order %s", order_id)
    try:
        envelope, email = await _provision_order(app, order, order["telegram_id"])
    except Exception as exc:
        logger.exception("Card payment provisioning failed for order %s", order_id)
        await app.db.mark_order_result(order_id, "paid_error", error=str(exc)[:1000])
        await callback.message.answer("ساخت سرویس خطا داشت؛ با <code>/retryorder %s</code> دوباره تلاش کن." % order_id)
        return
    await app.db.mark_order_result(order_id, "complete", email=email)
    try:
        await _send_credentials(app, order["telegram_id"], envelope, email)
    except Exception as exc:
        logger.exception("Card payment credential delivery failed for order %s", order_id)
        await app.db.mark_order_result(order_id, "complete", email=email, error=str(exc)[:1000])
        await callback.message.answer(
            f"سرویس <code>{html.escape(email)}</code> ساخته شد، اما ارسال اطلاعات کامل نبود. "
            f"برای ارسال دوباره از <code>/resendorder {order_id}</code> استفاده کن."
        )
        return
    await callback.message.answer(f"✅ سرویس <code>{html.escape(email)}</code> ساخته و برای کاربر ارسال شد.")


@router.callback_query(F.data.startswith("card:reject:"))
async def reject_card_payment(callback: CallbackQuery) -> None:
    app = _app(callback)
    if not app.is_admin(callback.from_user.id):
        await callback.answer("اجازهٔ رد پرداخت نداری.", show_alert=True)
        return
    try:
        order_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    order = await app.db.reject_card_payment(order_id, callback.from_user.id)
    if not order:
        await callback.answer("این رسید قبلاً بررسی شده است.", show_alert=True)
        return
    await callback.answer("پرداخت رد شد.")
    await callback.message.edit_reply_markup(reply_markup=None)
    await app.bot.send_message(
        order["telegram_id"],
        "❌ رسید پرداخت تأیید نشد. اگر فکر می‌کنی اشتباه شده، با پشتیبانی تماس بگیر.",
    )
    await callback.message.answer("رسید رد شد و به کاربر اطلاع داده شد.")


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    app = _app(query)
    order = await app.db.get_order_for_checkout(query.invoice_payload, query.from_user.id)
    valid = bool(
        order
        and query.currency == "XTR"
        and query.total_amount == order["stars_amount"]
    )
    await query.answer(
        ok=valid,
        error_message=None if valid else "سفارش معتبر نیست یا قیمت آن تغییر کرده است.",
    )


async def _provision_order(app: App, order, user_id: int) -> tuple[dict, str]:
    if order["telegram_id"] != user_id:
        raise RuntimeError("Order owner mismatch")
    plan = await app.db.get_plan(order["plan_code"])
    if not plan:
        raise RuntimeError("Plan disappeared before provisioning")
    traffic = (order["traffic_gb"] or plan.traffic_gb) * 1024**3
    duration_days = order["duration_days"] or plan.duration_days
    if order["kind"] == "renew":
        email = order["target_email"]
        if not email:
            raise RuntimeError("Renewal order has no target")
        owned = await app.xui.clients_for_telegram(user_id)
        if not any(_client_from_envelope(item).get("email") == email for item in owned):
            raise RuntimeError("Renewal target is not owned by order user")
        envelope = await app.xui.renew_client(email, traffic, duration_days)
    elif order["kind"] == "buy":
        email = order["target_email"] or f"tg{user_id}-order{order['id']}"
        if not _valid_client_name(email):
            raise RuntimeError("Invalid service name")
        expected_comment = (
            f"Discount order #{order['id']}" if order["discount_percent"] == 100 else
            f"Demo order #{order['id']}" if app.config.demo_mode else
            f"Telegram Stars order #{order['id']}"
        )
        try:
            envelope = await app.xui.get_client(email)
            existing = _client_from_envelope(envelope)
            if existing.get("comment") != expected_comment or str(existing.get("tgId")) != str(user_id):
                raise XUIError("این نام سرویس قبلاً استفاده شده است")
        except XUIError:
            if not await _client_name_available(app, email):
                raise
            envelope = await app.xui.create_client(
                email=email,
                telegram_id=user_id,
                traffic_bytes=traffic,
                duration_days=duration_days,
                comment=expected_comment,
            )
    else:
        raise RuntimeError("Invalid order kind")
    await app.db.record_service(user_id, email, f"order:{order['id']}")
    return envelope, email


@router.message(F.successful_payment)
async def successful_payment(message: Message) -> None:
    app = _app(message)
    if message.chat.type != ChatType.PRIVATE:
        logger.error("Successful payment received outside private chat; declining credential delivery")
        return
    payment = message.successful_payment
    order = await app.db.claim_payment(
        payment.invoice_payload,
        message.from_user.id,
        payment.total_amount,
        payment.telegram_payment_charge_id,
        payment.provider_payment_charge_id,
    )
    if not order:
        logger.warning("Duplicate or invalid successful payment: %s", payment.telegram_payment_charge_id)
        return
    try:
        envelope, email = await _provision_order(app, order, message.from_user.id)
        await app.db.mark_order_result(order["id"], "complete", email=email)
    except Exception as exc:
        logger.exception("Paid provisioning failed for order %s", order["id"])
        await app.db.mark_order_result(order["id"], "paid_error", error=str(exc)[:1000])
        await message.answer(
            "پرداختت موفق بود، اما ساخت سرویس با خطا روبه‌رو شد. مبلغ از بین نرفته و پشتیبانی پیگیری می‌کند."
        )
        await app.notify_admins(
            f"🚨 پرداخت سفارش #{order['id']} موفق بود ولی ساخت سرویس شکست خورد:\n"
            f"<code>{html.escape(str(exc)[:1500])}</code>"
        )
        return
    try:
        await _send_credentials(app, message.from_user.id, envelope, email)
    except Exception as exc:
        logger.exception("Credential delivery failed for Stars order %s", order["id"])
        await app.db.mark_order_result(order["id"], "complete", email=email, error=str(exc)[:1000])
        await app.notify_admins(f"سفارش #{order['id']} ساخته شد اما ارسال اطلاعات خطا داشت؛ /resendorder {order['id']}")
        return
    await app.notify_admins(
        f"✅ سفارش #{order['id']} برای کاربر <code>{message.from_user.id}</code> ساخته شد.\n"
        f"کلاینت: <code>{html.escape(email)}</code>"
    )


@router.callback_query(F.data.startswith("demo:confirm:"))
async def confirm_demo_order(callback: CallbackQuery) -> None:
    app = _app(callback)
    if not app.config.demo_mode:
        await callback.answer("حالت آزمایشی غیرفعال است.", show_alert=True)
        return
    if not await app.gate(callback):
        await callback.answer()
        return
    try:
        order_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    order = await app.db.claim_demo_order(order_id, callback.from_user.id)
    if not order:
        await callback.answer("این درخواست قبلاً پردازش یا لغو شده است.", show_alert=True)
        return
    await callback.answer("در حال ساخت سرویس…")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.exception("Could not remove demo buttons for order %s", order_id)
    try:
        envelope, email = await _provision_order(app, order, callback.from_user.id)
        await app.db.mark_order_result(order_id, "complete", email=email)
    except Exception as exc:
        logger.exception("Demo provisioning failed for order %s", order_id)
        await app.db.mark_order_result(order_id, "provision_error", error=str(exc)[:1000])
        await callback.message.answer(
            "ساخت سرویس با خطا روبه‌رو شد؛ درخواست ثبت شده و مدیریت می‌تواند دوباره اجراش کند."
        )
        await app.notify_admins(
            f"🚨 خطای ساخت سفارش آزمایشی #{order_id}:\n"
            f"<code>{html.escape(str(exc)[:1500])}</code>"
        )
        return
    try:
        await _send_credentials(app, callback.from_user.id, envelope, email)
    except Exception as exc:
        logger.exception("Credential delivery failed for demo order %s", order_id)
        await app.db.mark_order_result(order_id, "complete", email=email, error=str(exc)[:1000])
        await app.notify_admins(f"سفارش آزمایشی #{order_id} ساخته شد اما ارسال اطلاعات خطا داشت؛ /resendorder {order_id}")
        return
    await app.notify_admins(
        f"🧪 سفارش آزمایشی #{order_id} برای <code>{callback.from_user.id}</code> ساخته شد.\n"
        f"کلاینت: <code>{html.escape(email)}</code>"
    )


@router.callback_query(F.data.startswith("demo:cancel:"))
async def cancel_demo_order(callback: CallbackQuery) -> None:
    app = _app(callback)
    try:
        order_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    cancelled = await app.db.cancel_demo_order(order_id, callback.from_user.id)
    await callback.answer("لغو شد." if cancelled else "این درخواست قبلاً پردازش شده است.")
    if cancelled:
        await callback.message.edit_reply_markup(reply_markup=None)


@router.message(F.text == TRIAL)
async def trial(message: Message) -> None:
    app = _app(message)
    if not await app.gate(message):
        return
    if not app.config.trial_enabled:
        await message.answer("سرویس تست در حال حاضر غیرفعال است.")
        return
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    email = f"tg{message.from_user.id}-test-{period.replace('-', '')}"
    if not await app.db.claim_trial(message.from_user.id, period, email):
        await message.answer("سرویس تست این ماهت رو قبلاً دریافت کردی. ماه بعد دوباره می‌تونی تست بگیری.")
        return
    await message.answer(f"⏳ در حال ساخت سرویس تست {app.config.trial_mb} مگابایتی {app.config.trial_days} روزه…")
    try:
        envelope = await app.xui.create_client(
            email=email,
            telegram_id=message.from_user.id,
            traffic_bytes=app.config.trial_mb * 1024**2,
            duration_days=app.config.trial_days,
            comment=f"Monthly Telegram trial {period}",
        )
        await app.db.complete_trial(message.from_user.id, period, email)
    except Exception as exc:
        logger.exception("Trial provisioning failed")
        await message.answer("ساخت سرویس تست موقتاً با مشکل روبه‌رو شد؛ لطفاً با پشتیبانی تماس بگیر.")
        await app.notify_admins(f"🚨 خطای ساخت تست: <code>{html.escape(str(exc)[:1500])}</code>")
        return
    try:
        await _send_credentials(app, message.from_user.id, envelope, email)
    except Exception:
        logger.exception("Trial credential delivery failed for %s", message.from_user.id)
        await message.answer("سرویس تست ساخته شد، ولی ارسال کانفیگ خطا داشت؛ با پشتیبانی تماس بگیر.")
        await app.notify_admins(f"تست {html.escape(email)} ساخته شد اما ارسال کانفیگ خطا داشت.")
        return
    await app.notify_admins(
        f"🎁 تست ماهانه برای <code>{message.from_user.id}</code> ساخته شد: <code>{html.escape(email)}</code>"
    )


@router.message(F.text == RENEW)
@router.message(F.text == MY_SERVICES)
async def renew_list(message: Message) -> None:
    app = _app(message)
    if not await app.gate(message):
        return
    manual_services = await app.db.list_manual_services(message.from_user.id)
    try:
        envelopes = await app.xui.clients_for_telegram(message.from_user.id)
        emails = [_client_from_envelope(item).get("email") for item in envelopes]
        emails = [email for email in emails if email]
    except XUIError:
        logger.exception("Could not load user services")
        if not manual_services:
            await message.answer("دریافت لیست سرویس‌ها موقتاً ممکن نیست.")
            return
        emails = []
    if not emails and not manual_services:
        await message.answer("هنوز سرویسی به حساب تلگرامت متصل نشده است.")
        return
    await message.answer(
        "📋 <b>لیست سرویس‌هایی که داری اینجاست؛ هرکدوم رو انتخاب کنی می‌تونی جزئیاتش رو ببینی:</b>",
        reply_markup=services_keyboard(emails, manual_services),
    )


@router.callback_query(F.data.startswith("manual:"))
async def manual_service_detail(callback: CallbackQuery) -> None:
    app = _app(callback)
    if not await app.gate(callback):
        await callback.answer()
        return
    try:
        service_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("سرویس نامعتبر است.", show_alert=True)
        return
    service = await app.db.get_manual_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer("این سرویس متعلق به حساب شما نیست.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        f"<b>سرویس:</b> {html.escape(service['label'])}\n"
        f"<b>لینک اشتراک:</b>\n<code>{html.escape(service['subscription_url'])}</code>"
    )


@router.callback_query(F.data.startswith("service:"))
async def service_detail(callback: CallbackQuery) -> None:
    app = _app(callback)
    if not await app.gate(callback):
        await callback.answer()
        return
    email = callback.data.split(":", 1)[1]
    owned_items = await app.xui.clients_for_telegram(callback.from_user.id)
    owned = next(
        (item for item in owned_items if _client_from_envelope(item).get("email") == email),
        None,
    )
    if not owned:
        await callback.answer("این سرویس متعلق به حساب شما نیست.", show_alert=True)
        return
    envelope = await app.xui.get_client(email)
    client = _client_from_envelope(envelope)
    total = int(client.get("totalGB", 0))
    used = int(owned.get("usedTraffic", 0))
    remaining = max(0, total - used) if total else 0
    expiry_ms = int(client.get("expiryTime", 0))
    days = _remaining_days(expiry_ms)
    subscription = app.xui.subscription_url(envelope)
    inbound_count = len(envelope.get("inboundIds", []))
    await callback.answer()
    await callback.message.answer(
        f"<b>سرویس:</b> <code>{html.escape(email)}</code>\n"
        f"<b>حجم کل:</b> {_format_bytes(total) if total else 'نامحدود'}\n"
        f"<b>مصرف‌شده:</b> {_format_bytes(used)}\n"
        f"<b>حجم باقی‌مانده:</b> {_format_bytes(remaining) if total else 'نامحدود'}\n"
        f"<b>انقضا:</b> {_format_expiry(expiry_ms)}\n"
        f"<b>روز باقی‌مانده:</b> {days if days is not None else 'نامحدود'}\n"
        f"<b>تعداد کانفیگ‌ها:</b> {inbound_count}\n"
        f"<b>وضعیت:</b> {'فعال ✅' if client.get('enable') else 'غیرفعال ❌'}\n\n"
        f"<b>لینک اشتراک:</b>\n<code>{html.escape(subscription)}</code>",
        reply_markup=service_actions(email),
    )


@router.callback_query(F.data.startswith("renew:"))
async def renew_choose_plan(callback: CallbackQuery) -> None:
    app = _app(callback)
    if not await app.gate(callback):
        await callback.answer()
        return
    email = callback.data.split(":", 1)[1]
    allowed = {
        _client_from_envelope(item).get("email")
        for item in await app.xui.clients_for_telegram(callback.from_user.id)
    }
    if email not in allowed:
        await callback.answer("این سرویس متعلق به حساب شما نیست.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        "پلن تمدید رو انتخاب کن:",
        reply_markup=plans_keyboard(
            await app.db.list_plans(),
            "renew",
            email,
            demo_mode=app.config.demo_mode,
            card_payment=app.config.card_payment_enabled,
        ),
    )


@router.message(F.text == SUPPORT)
@router.message(Command("support"))
@router.message(Command("paysupport"))
async def support(message: Message) -> None:
    app = _app(message)
    await message.answer(
        f"برای پشتیبانی و پیگیری پرداخت به این حساب پیام بده:\n{app.config.support_username}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="پیام به پشتیبانی", url=app.config.support_url)]
            ]
        ),
    )


@router.message(F.text == CHANNEL)
async def channel(message: Message) -> None:
    app = _app(message)
    if not app.config.channel_join_required:
        await message.answer("الزام عضویت در کانال غیرفعال است.")
        return
    await message.answer(
        f"کانال رسمی دماوند VPN:\n{app.config.channel_username or app.config.channel_url}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="مشاهده کانال", url=app.config.channel_url)]
            ]
        ),
    )


@router.message(Command("terms"))
async def terms(message: Message) -> None:
    app = _app(message)
    body = app.config.read_terms()
    await message.answer(
        f"<b>قوانین و شرایط استفاده</b>\n\n{html.escape(body)}"
        if body
        else "قوانین هنوز توسط مدیریت ثبت نشده است."
    )


@router.message(Command("status"))
async def admin_status(message: Message) -> None:
    app = _app(message)
    if not app.is_admin(message.from_user.id):
        return
    summary = await app.db.summary()
    try:
        await app.xui.health()
        panel = "سالم ✅"
    except Exception as exc:
        panel = f"خطا ❌ ({html.escape(str(exc)[:200])})"
    await message.answer(
        f"<b>وضعیت بات</b>\n"
        f"کاربران: {summary['users']}\n"
        f"سفارش‌های کامل: {summary['paid']}\n"
        f"خطاهای بعد از پرداخت: {summary['failed']}\n"
        f"تست‌ها: {summary['trials']}\n"
        f"پنل: {panel}"
    )


@router.message(F.text == DISCOUNTS)
@router.message(Command("discounts"))
async def admin_discounts(message: Message) -> None:
    app = _app(message)
    if not app.is_admin(message.from_user.id):
        return
    codes = await app.db.list_discount_codes()
    listing = "\n".join(f"• <code>{html.escape(row['code'])}</code> — {row['percent']}٪" for row in codes[:30])
    await message.answer(
        "🎟 <b>کدهای تخفیف فعال</b>\n" + (listing or "هنوز کدی ثبت نشده است.")
        + "\n\nبرای تعریف کد، دکمهٔ زیر را بزن.",
        reply_markup=discount_admin_keyboard([row["code"] for row in codes[:30]]),
    )


@router.callback_query(F.data == "discount:admin:add")
async def admin_begin_discount(callback: CallbackQuery, state: FSMContext) -> None:
    app = _app(callback)
    if not app.is_admin(callback.from_user.id):
        await callback.answer("اجازهٔ مدیریت کد تخفیف نداری.", show_alert=True)
        return
    await state.set_state(AdminDiscountInput.waiting_for_definition)
    await callback.answer()
    await callback.message.answer("کد و درصد را به شکل <code>LASTOFUS-35</code> بفرست. درصد باید از ۱ تا ۱۰۰ باشد.")


@router.message(AdminDiscountInput.waiting_for_definition)
async def admin_save_discount(message: Message, state: FSMContext) -> None:
    app = _app(message)
    if not app.is_admin(message.from_user.id):
        await state.clear()
        return
    code = (message.text or "").strip().upper()
    match = re.fullmatch(r"[A-Z0-9_]{3,32}-([1-9][0-9]?|100)", code)
    if not match:
        await message.answer("فرمت درست نیست. مثال: <code>LASTOFUS-35</code>؛ درصد ۱ تا ۱۰۰.")
        return
    await app.db.save_discount_code(code, int(match.group(1)), message.from_user.id)
    await state.clear()
    await message.answer(f"✅ کد <code>{code}</code> با تخفیف {match.group(1)}٪ فعال شد.")


@router.callback_query(F.data.startswith("discount:admin:off:"))
async def admin_disable_discount(callback: CallbackQuery) -> None:
    app = _app(callback)
    if not app.is_admin(callback.from_user.id):
        await callback.answer("اجازهٔ مدیریت کد تخفیف نداری.", show_alert=True)
        return
    code = callback.data.removeprefix("discount:admin:off:")
    disabled = await app.db.disable_discount_code(code)
    await callback.answer("کد غیرفعال شد." if disabled else "کد از قبل غیرفعال بوده است.")
    codes = await app.db.list_discount_codes()
    await callback.message.edit_reply_markup(reply_markup=discount_admin_keyboard([row["code"] for row in codes[:30]]))


@router.message(Command("plans"))
async def admin_plans(message: Message) -> None:
    app = _app(message)
    if not app.is_admin(message.from_user.id):
        return
    plans = await app.db.list_plans(False)
    await message.answer(
        "\n".join(
            f"{plan.traffic_gb}GB — {plan.toman_price:,} تومان — {plan.stars_price} ⭐"
            for plan in plans
        )
    )


@router.message(Command("setstars"))
async def admin_set_stars(message: Message) -> None:
    app = _app(message)
    if not app.is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3 or not all(part.isdigit() for part in parts[1:]):
        await message.answer("فرمت صحیح: <code>/setstars 10 100</code>")
        return
    ok = await app.db.set_stars_price(int(parts[1]), int(parts[2]))
    await message.answer("قیمت Stars ذخیره شد ✅" if ok else "پلن پیدا نشد.")


@router.message(Command("retryorder"))
async def admin_retry_order(message: Message) -> None:
    app = _app(message)
    if not app.is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("فرمت صحیح: <code>/retryorder 12</code>")
        return
    order = await app.db.get_order(int(parts[1]))
    if not order or order["status"] not in {"paid_error", "provision_error"}:
        await message.answer("سفارش خطادار با این شناسه پیدا نشد.")
        return
    if order["kind"] == "renew":
        await message.answer("تمدید ممکن است قبلاً در پنل اعمال شده باشد. برای جلوگیری از تمدید دوباره، وضعیت و تاریخ انقضا را در پنل بررسی کن؛ اجرای خودکار مجدد غیرفعال است.")
        return
    order = await app.db.claim_retry_order(order["id"])
    if not order:
        await message.answer("این سفارش هم‌اکنون در حال پردازش است یا دیگر قابل تکرار نیست.")
        return
    try:
        envelope, email = await _provision_order(app, order, order["telegram_id"])
    except Exception as exc:
        await app.db.mark_order_result(order["id"], "paid_error", error=str(exc)[:1000])
        await message.answer(f"هنوز خطا دارد: <code>{html.escape(str(exc)[:1000])}</code>")
        return
    await app.db.mark_order_result(order["id"], "complete", email=email)
    try:
        await _send_credentials(app, order["telegram_id"], envelope, email)
    except Exception as exc:
        logger.exception("Credential delivery failed for retried order %s", order["id"])
        await app.db.mark_order_result(order["id"], "complete", email=email, error=str(exc)[:1000])
        await message.answer(
            f"سرویس ساخته شد، اما ارسال اطلاعات کامل نبود. "
            f"از <code>/resendorder {order['id']}</code> استفاده کن."
        )
        return
    await message.answer("سرویس ساخته و برای کاربر ارسال شد ✅")


@router.message(Command("pendingreceipts"))
async def admin_pending_receipts(message: Message) -> None:
    app = _app(message)
    if not app.is_admin(message.from_user.id):
        return
    orders = await app.db.pending_card_receipts()
    if not orders:
        await message.answer("رسید منتظر بررسی وجود ندارد.")
        return
    for order in orders:
        await app.bot.send_photo(
            message.from_user.id,
            order["receipt_file_id"],
            caption=(
                f"رسید سفارش #{order['id']} — کاربر <code>{order['telegram_id']}</code>\n"
                f"مبلغ مورد انتظار: <b>{order['toman_amount']:,} تومان</b>\n"
                "⚠️ واریز واقعی را در حساب بانکی بررسی کنید؛ عکس به‌تنهایی مدرک پرداخت نیست."
            ),
            reply_markup=card_review_keyboard(order["id"]),
        )


@router.message(Command("resendorder"))
async def admin_resend_order(message: Message) -> None:
    app = _app(message)
    if not app.is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("فرمت صحیح: <code>/resendorder 12</code>")
        return
    order = await app.db.get_order(int(parts[1]))
    if not order or order["status"] != "complete" or not order["provisioned_email"]:
        await message.answer("سفارش ساخته‌شده با این شناسه پیدا نشد.")
        return
    try:
        envelope = await app.xui.get_client(order["provisioned_email"])
        await _send_credentials(app, order["telegram_id"], envelope, order["provisioned_email"])
        await app.db.mark_order_result(order["id"], "complete", email=order["provisioned_email"])
        await message.answer("اطلاعات سرویس دوباره برای کاربر ارسال شد ✅")
    except Exception as exc:
        logger.exception("Credential resend failed for order %s", order["id"])
        await message.answer(f"ارسال دوباره خطا داشت: <code>{html.escape(str(exc)[:1000])}</code>")


async def run() -> None:
    global runtime_app
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = Config.from_env()
    db = Database(config.db_path)
    await db.initialize()
    pid_file = config.db_path.parent / "bot.pid"
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    try:
        bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        async with XUIClient(config) as xui:
            app = App(config, bot, db, xui)
            runtime_app = app
            dispatcher = Dispatcher()
            dispatcher.include_router(router)
            await bot.delete_webhook(drop_pending_updates=False)
            await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        try:
            if pid_file.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_file.unlink(missing_ok=True)
        except FileNotFoundError:
            pass


def main() -> None:
    asyncio.run(run())

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from damavand_bot.app import App, _client_name_available, _finish_discount, _provision_order, _send_credentials, _send_referral_banner, buy, pre_checkout, start
from damavand_bot.config import Config
from damavand_bot.db import Database
from damavand_bot.keyboards import CHANNEL, REFERRAL, main_menu
from damavand_bot.menu import DEFAULT_ENABLED_BUTTONS, MENU_BUTTONS
from damavand_bot.welcome import DEFAULT_WELCOME_MESSAGE, render_welcome, welcome_photo_text
from damavand_bot.xui import XUIClient, XUIError, XUINameTakenError


class ChannelRequirementTests(IsolatedAsyncioTestCase):
    async def test_disabled_requirement_skips_telegram_and_channel_menu(self):
        config = SimpleNamespace(channel_join_required=False, admin_ids=frozenset(), channel_id="", channel_username="")
        bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=AssertionError("should not call Telegram")))
        app = App(config, bot, None, None)
        self.assertTrue(await app.is_channel_member(123))
        bot.get_chat_member.assert_not_awaited()
        self.assertNotIn(CHANNEL, [button.text for row in main_menu(channel_enabled=False).keyboard for button in row])

    async def test_enabled_requirement_checks_telegram(self):
        config = SimpleNamespace(channel_join_required=True, admin_ids=frozenset(), channel_id="", channel_username="@publicchannel")
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="left")))
        app = App(config, bot, None, None)
        self.assertFalse(await app.is_channel_member(123))
        bot.get_chat_member.assert_awaited_once_with("@publicchannel", 123)


class XUINameAvailabilityTests(IsolatedAsyncioTestCase):
    async def test_legacy_inbound_client_is_taken_even_when_lookup_misses_it(self):
        xui = XUIClient(SimpleNamespace())
        xui.get_client = AsyncMock(side_effect=XUIError("Obtain (record not found)"))
        xui._request = AsyncMock(return_value=[
            {"settings": '{"clients": [{"email": "Test"}]}', "enable": False},
        ])
        self.assertFalse(await _client_name_available(SimpleNamespace(xui=xui), "test"))

    async def test_modern_inbound_client_is_taken(self):
        xui = XUIClient(SimpleNamespace())
        xui.get_client = AsyncMock(side_effect=XUIError("Client not found"))
        xui._request = AsyncMock(return_value=[{"settings": {"clients": [{"email": "taken"}]}}])
        self.assertTrue(await xui.client_email_taken("taken"))

    async def test_missing_client_is_available(self):
        xui = XUIClient(SimpleNamespace())
        xui.get_client = AsyncMock(side_effect=XUIError("Client not found"))
        xui._request = AsyncMock(return_value=[{"settings": {"clients": []}}])
        self.assertTrue(await _client_name_available(SimpleNamespace(xui=xui), "unused"))

    async def test_incomplete_inbound_data_does_not_approve_name(self):
        xui = XUIClient(SimpleNamespace())
        xui.get_client = AsyncMock(side_effect=XUIError("Client not found"))
        xui._request = AsyncMock(return_value=[{"settings": "bad json"}])
        with self.assertRaises(XUIError):
            await xui.client_email_taken("unused")

    async def test_duplicate_create_error_is_identified(self):
        xui = XUIClient(SimpleNamespace())
        xui.enabled_inbound_ids = AsyncMock(return_value=[1])
        xui._request = AsyncMock(side_effect=XUIError("Duplicate email: Test"))
        with self.assertRaises(XUINameTakenError):
            await xui.create_client(email="Test", telegram_id=123, traffic_bytes=1, duration_days=1, comment="test")

    async def test_paid_provision_does_not_create_over_legacy_client(self):
        xui = SimpleNamespace(
            get_client=AsyncMock(side_effect=XUIError("Obtain (record not found)")),
            client_email_taken=AsyncMock(return_value=True),
            create_client=AsyncMock(),
        )
        db = SimpleNamespace(get_plan=AsyncMock(return_value=SimpleNamespace(traffic_gb=10, duration_days=30)))
        order = {"id": 7, "telegram_id": 123, "kind": "buy", "plan_code": "gb10",
                 "target_email": "Test", "traffic_gb": 10, "duration_days": 30, "discount_percent": 0}
        with self.assertRaises(XUINameTakenError):
            await _provision_order(SimpleNamespace(db=db, xui=xui, config=SimpleNamespace(demo_mode=False)), order, 123)
        xui.create_client.assert_not_awaited()

    async def test_stars_checkout_rejects_name_taken_after_invoice(self):
        order = {"kind": "buy", "target_email": "Test", "stars_amount": 100}
        app = SimpleNamespace(
            db=SimpleNamespace(get_order_for_checkout=AsyncMock(return_value=order)),
            xui=SimpleNamespace(client_email_taken=AsyncMock(return_value=True)),
        )
        query = SimpleNamespace(invoice_payload="p", from_user=SimpleNamespace(id=123),
                                currency="XTR", total_amount=100, answer=AsyncMock())
        with patch("damavand_bot.app.runtime_app", app):
            await pre_checkout(query)
        self.assertFalse(query.answer.await_args.kwargs["ok"])


class MenuButtonTests(IsolatedAsyncioTestCase):
    async def test_saved_button_flag_is_loaded_when_bot_starts(self):
        with patch.dict(os.environ, {
            "BOT_TOKEN": "123456789:" + "A" * 35,
            "ADMIN_IDS": "123456789",
            "XUI_BASE_URL": "https://example.com/panel",
            "XUI_API_TOKEN": "test-token",
            "XUI_SUBSCRIPTION_BASE": "https://example.com/sub",
            "MENU_BUY_ENABLED": "false",
        }):
            config = Config.from_env()
        self.assertFalse(config.button_enabled("buy"))
        self.assertTrue(config.button_enabled("renew"))

    async def test_admin_menu_has_all_user_buttons(self):
        labels = [button.text for row in main_menu().keyboard for button in row]
        self.assertEqual([label for _, label in MENU_BUTTONS], labels)
        self.assertIn(REFERRAL, labels)
        self.assertNotIn("🎟 مدیریت کد تخفیف", labels)

    async def test_referral_banner_contains_personal_link_and_share_button(self):
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="examplebot")),
            send_photo=AsyncMock(), send_message=AsyncMock(),
        )
        await _send_referral_banner(SimpleNamespace(bot=bot), 123, 456)
        kwargs = bot.send_photo.await_args.kwargs
        self.assertIn("https://t.me/examplebot?start=ref_456", kwargs["caption"])
        self.assertEqual("https://t.me/examplebot?start=ref_456",
                         kwargs["reply_markup"].inline_keyboard[0][0].url)
        self.assertTrue(kwargs["photo"].path.exists())
        bot.send_message.assert_not_awaited()

    async def test_disabled_button_is_hidden_and_old_buy_button_is_blocked(self):
        enabled = DEFAULT_ENABLED_BUTTONS - {"buy"}
        labels = [button.text for row in main_menu(enabled).keyboard for button in row]
        self.assertNotIn(MENU_BUTTONS[0][1], labels)
        config = SimpleNamespace(button_enabled=lambda name: name in enabled)
        app = SimpleNamespace(config=config, menu=lambda: main_menu(enabled), gate=AsyncMock(side_effect=AssertionError("must not enter buy")))
        message = SimpleNamespace(answer=AsyncMock())
        with patch("damavand_bot.app.runtime_app", app):
            await buy(message)
        app.gate.assert_not_awaited()
        message.answer.assert_awaited_once()


class WelcomeTests(IsolatedAsyncioTestCase):
    async def test_welcome_text_uses_name_and_channel_requirement(self):
        enabled = render_welcome(DEFAULT_WELCOME_MESSAGE, "مریم", True)
        disabled = render_welcome(DEFAULT_WELCOME_MESSAGE, "مریم", False)
        self.assertIn("مریم", enabled)
        self.assertIn("عضویت در کانال", enabled)
        self.assertNotIn("عضویت در کانال", disabled)
        self.assertIn("آی‌پی ثابت", disabled)

    async def test_banner_text_stays_with_photo_until_caption_limit(self):
        caption, followup = welcome_photo_text("Damavand", "Fast VPN", "Welcome")
        self.assertEqual("Damavand\n\nFast VPN\n\nWelcome", caption)
        self.assertIsNone(followup)
        caption, followup = welcome_photo_text("Damavand", "Fast VPN", "x" * 900)
        self.assertEqual("Damavand\n\nFast VPN\n\n" + "x" * 900, caption)
        caption, followup = welcome_photo_text("Damavand", "x" * 240, "x" * 900)
        self.assertEqual("Damavand\n\n" + "x" * 240, caption)
        self.assertEqual("x" * 900, followup)


class DatabaseTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.db")
        await self.db.initialize()
        await self.db.upsert_user(123, "tester", "Test User")

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_plans_are_seeded_and_paid_sales_default_off(self):
        plans = await self.db.list_plans()
        self.assertEqual([10, 20, 30, 50, 75, 100], [p.traffic_gb for p in plans])
        self.assertTrue(all(p.stars_price == 0 for p in plans))
        self.assertEqual(150_000, plans[1].toman_price)

    async def test_new_wallet_bonus_is_given_once(self):
        self.assertEqual(5000, (await self.db.wallet_summary(123))[0])
        await self.db.upsert_user(123, "tester", "Updated")
        self.assertEqual(5000, (await self.db.wallet_summary(123))[0])
        async with self.db.connect() as db:
            count = await (await db.execute(
                "SELECT count(*) FROM wallet_transactions WHERE reference='welcome:123'"
            )).fetchone()
        self.assertEqual(1, count[0])

    async def test_referral_credits_only_first_completed_purchase_once(self):
        await self.db.upsert_user(456, "friend", "Friend", 123)
        await self.db.upsert_user(456, "friend", "Friend", 999)
        plan = await self.db.get_plan("gb20")
        first, _ = await self.db.create_order(456, "buy", plan, "friend1")
        await self.db.mark_order_result(first, "paid_error")
        self.assertEqual(5000, (await self.db.wallet_summary(123))[0])
        await self.db.mark_order_result(first, "complete", email="friend1")
        await self.db.mark_order_result(first, "complete", email="friend1")
        second, _ = await self.db.create_order(456, "buy", plan, "friend2")
        await self.db.mark_order_result(second, "complete", email="friend2")
        self.assertEqual((20000, 1, 15000), await self.db.wallet_summary(123))
        async with self.db.connect() as db:
            row = await (await db.execute(
                "SELECT rewarded_order_id,reward_toman FROM referrals WHERE invited_telegram_id=456"
            )).fetchone()
        self.assertEqual((first, 15000), tuple(row))

    async def test_referral_cannot_attach_to_existing_user_or_self(self):
        await self.db.upsert_user(123, "tester", "Tester", 123)
        await self.db.upsert_user(456, "friend", "Friend")
        await self.db.upsert_user(456, "friend", "Friend", 123)
        self.assertEqual(0, (await self.db.wallet_summary(123))[1])

    async def test_free_first_service_does_not_consume_paid_referral_reward(self):
        await self.db.upsert_user(456, "friend", "Friend", 123)
        plan = await self.db.get_plan("gb10")
        free_id, _ = await self.db.create_order(456, "buy", plan, "free")
        async with self.db.connect() as db:
            await db.execute("UPDATE orders SET toman_amount=0,discount_percent=100 WHERE id=?", (free_id,))
            await db.commit()
        await self.db.mark_order_result(free_id, "complete", email="free")
        paid_id, _ = await self.db.create_order(456, "buy", plan, "paid")
        await self.db.mark_order_result(paid_id, "complete", email="paid")
        self.assertEqual((12500, 1, 7500), await self.db.wallet_summary(123))

    async def test_wallet_topup_and_purchase_are_atomic_and_idempotent(self):
        topup_id = await self.db.create_wallet_topup(123, 150000)
        self.assertTrue(await self.db.select_wallet_receipt(topup_id, 123))
        self.assertIsNotNone(await self.db.submit_wallet_receipt(topup_id, 123, "file", "unique"))
        self.assertIsNone(await self.db.submit_wallet_receipt(topup_id, 123, "file", "unique"))
        self.assertIsNotNone(await self.db.review_wallet_topup(topup_id, 999, True))
        self.assertIsNone(await self.db.review_wallet_topup(topup_id, 999, True))
        self.assertEqual(155000, (await self.db.wallet_summary(123))[0])
        plan = await self.db.get_plan("gb20")
        order_id, _ = await self.db.create_order(123, "buy", plan, "walletuser")
        self.assertIsNotNone(await self.db.claim_wallet_payment(order_id, 123))
        self.assertIsNone(await self.db.claim_wallet_payment(order_id, 123))
        self.assertEqual(5000, (await self.db.wallet_summary(123))[0])
        another, _ = await self.db.create_order(123, "buy", plan, "walletuser2")
        self.assertIsNone(await self.db.claim_wallet_payment(another, 123))
        self.assertEqual("pending", (await self.db.get_order(another))["status"])
        await self.db.mark_order_result(order_id, "paid_error")
        self.assertEqual((123, 150000), await self.db.refund_failed_wallet_order(order_id))
        self.assertIsNone(await self.db.refund_failed_wallet_order(order_id))
        self.assertEqual(155000, (await self.db.wallet_summary(123))[0])

    async def test_existing_account_migration_does_not_get_signup_bonus(self):
        async with self.db.connect() as db:
            await db.execute("INSERT INTO users(telegram_id,full_name,created_at,updated_at) VALUES(789,'Old',?,?)", ("now", "now"))
            await db.commit()
        await self.db.initialize()
        await self.db.upsert_user(789, None, "Old")
        self.assertEqual(0, (await self.db.wallet_summary(789))[0])

    async def test_plan_edits_survive_bot_database_initialization(self):
        async with self.db.connect() as db:
            await db.execute("UPDATE plans SET toman_price=123456,enabled=0 WHERE code='gb10'")
            await db.commit()
        await self.db.initialize()
        async with self.db.connect() as db:
            row = await (await db.execute("SELECT toman_price,enabled FROM plans WHERE code='gb10'")).fetchone()
        self.assertEqual((123456, 0), tuple(row))

    async def test_payment_claim_is_idempotent(self):
        await self.db.set_stars_price(10, 100)
        plan = await self.db.get_plan("gb10")
        order_id, payload = await self.db.create_order(123, "buy", plan, None)
        first = await self.db.claim_payment(payload, 123, 100, "charge-1", "")
        second = await self.db.claim_payment(payload, 123, 100, "charge-1", "")
        self.assertEqual(order_id, first["id"])
        self.assertIsNone(second)

    async def test_trial_is_unique_per_month(self):
        self.assertFalse(await self.db.trial_used(123, "2026-09"))
        await self.db.record_trial(123, "2026-09", "tg123-test")
        self.assertTrue(await self.db.trial_used(123, "2026-09"))

    async def test_demo_order_can_only_be_claimed_once(self):
        plan = await self.db.get_plan("gb10")
        order_id, _ = await self.db.create_order(123, "buy", plan, None)
        first = await self.db.claim_demo_order(order_id, 123)
        second = await self.db.claim_demo_order(order_id, 123)
        self.assertEqual(order_id, first["id"])
        self.assertIsNone(second)

    async def test_card_receipt_can_only_be_approved_once(self):
        plan = await self.db.get_plan("gb10")
        order_id, _ = await self.db.create_order(123, "buy", plan, "card_payment_test")
        self.assertTrue(await self.db.begin_card_payment(order_id, 123))
        receipt = await self.db.submit_card_receipt(order_id, 123, "telegram-file-id", "unique-photo")
        self.assertEqual(order_id, receipt["id"])
        first = await self.db.claim_card_payment_approval(order_id, 456)
        second = await self.db.claim_card_payment_approval(order_id, 456)
        self.assertEqual(order_id, first["id"])
        self.assertIsNone(second)

    async def test_receipt_selection_survives_database_reopen_and_photo_replay_is_blocked(self):
        plan = await self.db.get_plan("gb10")
        first, _ = await self.db.create_order(123, "buy", plan, "first_client")
        second, _ = await self.db.create_order(123, "buy", plan, "second_client")
        for order_id in (first, second):
            self.assertTrue(await self.db.begin_card_payment(order_id, 123))
        self.assertTrue(await self.db.select_card_receipt(first, 123))
        reopened = Database(self.db.path)
        self.assertEqual(first, await reopened.selected_card_receipt(123))
        self.assertIsNotNone(await reopened.submit_card_receipt(first, 123, "file-one", "same-photo"))
        self.assertTrue(await reopened.select_card_receipt(second, 123))
        self.assertIsNone(await reopened.submit_card_receipt(second, 123, "file-two", "same-photo"))
        self.assertTrue(await reopened.card_receipt_waiting(second, 123))
        self.assertEqual(second, await reopened.selected_card_receipt(123))

    async def test_order_terms_are_snapshotted(self):
        plan = await self.db.get_plan("gb10")
        order_id, _ = await self.db.create_order(123, "buy", plan, "client_name")
        order = await self.db.get_order(order_id)
        self.assertEqual((75_000, 10, 31), (order["toman_amount"], order["traffic_gb"], order["duration_days"]))

    async def test_trial_reservation_is_atomic(self):
        self.assertTrue(await self.db.claim_trial(123, "2026-10", "trial"))
        self.assertFalse(await self.db.claim_trial(123, "2026-10", "trial"))
        await self.db.complete_trial(123, "2026-10", "trial")

    async def test_retry_claim_is_atomic_and_excludes_renewals(self):
        plan = await self.db.get_plan("gb10")
        buy_id, _ = await self.db.create_order(123, "buy", plan, "buyer")
        renew_id, _ = await self.db.create_order(123, "renew", plan, "existing")
        await self.db.mark_order_result(buy_id, "paid_error")
        await self.db.mark_order_result(renew_id, "paid_error")
        self.assertIsNotNone(await self.db.claim_retry_order(buy_id))
        self.assertIsNone(await self.db.claim_retry_order(buy_id))
        self.assertIsNone(await self.db.claim_retry_order(renew_id))

    async def test_failed_paid_order_can_be_renamed_before_retry_only(self):
        plan = await self.db.get_plan("gb10")
        order_id, _ = await self.db.create_order(123, "buy", plan, "taken")
        self.assertFalse(await self.db.rename_failed_buy_order(order_id, "free"))
        await self.db.mark_order_result(order_id, "paid_error")
        self.assertTrue(await self.db.rename_failed_buy_order(order_id, "free"))
        self.assertEqual("free", (await self.db.get_order(order_id))["target_email"])
        await self.db.mark_order_result(order_id, "complete", email="free")
        self.assertFalse(await self.db.rename_failed_buy_order(order_id, "another"))

    async def test_discount_is_frozen_once_and_bound_to_order_owner(self):
        plan = await self.db.get_plan("gb10")
        await self.db.save_discount_code("LASTOFUS-35", 35, 456)
        order_id, _ = await self.db.create_order(123, "buy", plan, "discount_client", await_discount=True)
        self.assertIsNone(await self.db.finalize_discount_order(order_id, 999, "LASTOFUS-35"))
        order = await self.db.finalize_discount_order(order_id, 123, "LASTOFUS-35")
        self.assertEqual((35, 48_750, "pending"),
                         (order["discount_percent"], order["toman_amount"], order["status"]))
        self.assertIsNone(await self.db.finalize_discount_order(order_id, 123, None))

    async def test_invalid_or_disabled_code_uses_original_price(self):
        plan = await self.db.get_plan("gb10")
        await self.db.save_discount_code("EXPIRED-35", 35, 456)
        self.assertTrue(await self.db.disable_discount_code("EXPIRED-35"))
        order_id, _ = await self.db.create_order(123, "buy", plan, "discount_client", await_discount=True)
        order = await self.db.finalize_discount_order(order_id, 123, "EXPIRED-35")
        self.assertEqual((0, 75_000, None, "pending"),
                         (order["discount_percent"], order["toman_amount"], order["discount_code"], order["status"]))

    async def test_discount_user_and_plan_scopes(self):
        plan10 = await self.db.get_plan("gb10")
        plan20 = await self.db.get_plan("gb20")
        await self.db.upsert_user(999, "other", "Other User")
        await self.db.save_discount_code("TARGET-40", 40, 456, target_telegram_id=123, target_plan_code="gb10")
        for owner, plan, expected_percent in ((999, plan10, 0), (123, plan20, 0), (123, plan10, 40)):
            order_id, _ = await self.db.create_order(owner, "buy", plan, "scope_client", await_discount=True)
            order = await self.db.finalize_discount_order(order_id, owner, "TARGET-40")
            self.assertEqual(expected_percent, order["discount_percent"])

    async def test_manual_service_is_visible_only_to_its_owner(self):
        async with self.db.connect() as db:
            cursor = await db.execute(
                "INSERT INTO manual_services(telegram_id,plan_code,label,subscription_url,created_at) VALUES(?,?,?,?,datetime('now'))",
                (123, "gb10", "Existing plan", "https://example.com/sub/abc"),
            )
            service_id = cursor.lastrowid
            await db.commit()
        self.assertEqual(1, len(await self.db.list_manual_services(123)))
        self.assertEqual([], await self.db.list_manual_services(999))
        self.assertIsNone(await self.db.get_manual_service(service_id, 999))
        self.assertEqual("Existing plan", (await self.db.get_manual_service(service_id, 123))["label"])

    async def test_pending_username_service_claims_once_and_binds_to_telegram_id(self):
        async with self.db.connect() as db:
            await db.execute(
                "INSERT INTO pending_services(username,plan_code,label,subscription_url,created_at) VALUES(?,?,?,?,datetime('now'))",
                ("damavandhashemian", "gb10", "Old XUI service", "https://example.com/sub/old"),
            )
            await db.commit()
        self.assertEqual(0, await self.db.claim_pending_services(123, "differentuser"))
        self.assertEqual(1, await self.db.claim_pending_services(123, "DamavandHashemian"))
        self.assertEqual(0, await self.db.claim_pending_services(999, "damavandhashemian"))
        self.assertEqual(1, len(await self.db.list_manual_services(123)))
        self.assertEqual([], await self.db.list_manual_services(999))
        async with self.db.connect() as db:
            self.assertEqual(0, (await (await db.execute("SELECT count(*) FROM pending_services")).fetchone())[0])

    async def test_start_attaches_pending_username_service(self):
        async with self.db.connect() as db:
            await db.execute(
                "INSERT INTO pending_services(username,plan_code,label,subscription_url,created_at) VALUES(?,?,?,?,datetime('now'))",
                ("newtelegramuser", None, "Old service", "https://example.com/sub/on-start"),
            )
            await db.commit()
        user = SimpleNamespace(id=777, username="NewTelegramUser", full_name="New User", first_name="New")
        message = SimpleNamespace(from_user=user, answer=AsyncMock(), answer_photo=AsyncMock())
        app = SimpleNamespace(db=self.db, config=SimpleNamespace(channel_join_required=True, welcome_message=DEFAULT_WELCOME_MESSAGE), gate=AsyncMock(return_value=True), show_welcome=AsyncMock())
        with patch("damavand_bot.app.runtime_app", app):
            await start(message)
        self.assertEqual(1, len(await self.db.list_manual_services(777)))
        app.show_welcome.assert_awaited_once_with(message)
        message.answer_photo.assert_awaited_once()
        self.assertIn("New", message.answer_photo.await_args.kwargs["caption"])
        self.assertEqual(1, message.answer.await_count)

    async def test_full_discount_skips_payment_and_cannot_be_reused(self):
        plan = await self.db.get_plan("gb10")
        await self.db.save_discount_code("FREEBIE-100", 100, 456)
        order_id, _ = await self.db.create_order(123, "buy", plan, "free_client", await_discount=True)
        order = await self.db.finalize_discount_order(order_id, 123, "FREEBIE-100")
        self.assertEqual((0, 0, "processing"),
                         (order["toman_amount"], order["stars_amount"], order["status"]))
        self.assertIsNone(await self.db.finalize_discount_order(order_id, 123, "FREEBIE-100"))

    async def test_full_discount_provisions_without_card_or_invoice(self):
        plan = await self.db.get_plan("gb10")
        await self.db.save_discount_code("FREE-100", 100, 456)
        order_id, _ = await self.db.create_order(123, "buy", plan, "free_client", await_discount=True)
        bot = SimpleNamespace(send_message=AsyncMock(), send_invoice=AsyncMock())
        app = SimpleNamespace(db=self.db, bot=bot, notify_admins=AsyncMock())
        with patch("damavand_bot.app._provision_order", new_callable=AsyncMock) as provision, \
             patch("damavand_bot.app._send_credentials", new_callable=AsyncMock) as deliver:
            provision.return_value = ({}, "free_client")
            self.assertTrue(await _finish_discount(app, order_id, 123, "FREE-100"))
            provision.assert_awaited_once()
            deliver.assert_awaited_once()
        bot.send_invoice.assert_not_awaited()
        self.assertEqual("complete", (await self.db.get_order(order_id))["status"])

    async def test_user_exists_after_registration(self):
        self.assertTrue(await self.db.user_exists(123))
        self.assertFalse(await self.db.user_exists(999))


class CredentialDeliveryTests(IsolatedAsyncioTestCase):
    async def test_renewal_requires_panel_ownership_at_provision_time(self):
        class FakeDB:
            async def get_plan(self, code):
                return SimpleNamespace(traffic_gb=10, duration_days=31)

        class FakeXUI:
            async def clients_for_telegram(self, user_id):
                return []

            async def renew_client(self, *args):
                raise AssertionError("must not renew an unowned service")

        order = {"id": 1, "telegram_id": 123, "kind": "renew", "plan_code": "gb10", "target_email": "victim", "traffic_gb": 10, "duration_days": 31}
        with self.assertRaisesRegex(RuntimeError, "not owned"):
            await _provision_order(SimpleNamespace(db=FakeDB(), xui=FakeXUI()), order, 123)

    async def test_long_config_list_keeps_each_html_tag_intact(self):
        links = [f"vless://config-{index}?path={'a' * 900}&mode=auto" for index in range(5)]

        class FakeBot:
            def __init__(self):
                self.messages = []
                self.documents = []

            async def send_photo(self, *args, **kwargs):
                pass

            async def send_message(self, chat_id, body):
                self.messages.append(body)

            async def send_document(self, *args, **kwargs):
                self.documents.append((args, kwargs))

        class FakeXUI:
            def subscription_url(self, envelope):
                return "https://example.com/sub/example"

            async def links(self, email):
                return links

        bot = FakeBot()
        await _send_credentials(SimpleNamespace(bot=bot, xui=FakeXUI()), 123, {}, "test")
        self.assertEqual(6, len(bot.messages))
        self.assertFalse(bot.documents)
        for body in bot.messages[1:]:
            self.assertEqual(1, body.count("<code>"))
            self.assertEqual(1, body.count("</code>"))
            self.assertLessEqual(len(body), 3900)

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from damavand_bot.app import _finish_discount, _provision_order, _send_credentials
from damavand_bot.db import Database


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

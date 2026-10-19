from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT NOT NULL,
    terms_version TEXT,
    terms_accepted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    code TEXT PRIMARY KEY,
    title_fa TEXT NOT NULL,
    traffic_gb INTEGER NOT NULL,
    duration_days INTEGER NOT NULL,
    toman_price INTEGER NOT NULL,
    stars_price INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('buy','renew')),
    plan_code TEXT NOT NULL,
    target_email TEXT,
    stars_amount INTEGER NOT NULL,
    toman_amount INTEGER NOT NULL DEFAULT 0,
    traffic_gb INTEGER NOT NULL DEFAULT 0,
    duration_days INTEGER NOT NULL DEFAULT 0,
    discount_code TEXT,
    discount_percent INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    invoice_payload TEXT UNIQUE NOT NULL,
    telegram_charge_id TEXT UNIQUE,
    provider_charge_id TEXT,
    provisioned_email TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id),
    FOREIGN KEY (plan_code) REFERENCES plans(code)
);

CREATE TABLE IF NOT EXISTS trials (
    telegram_id INTEGER NOT NULL,
    period TEXT NOT NULL,
    client_email TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'complete',
    created_at TEXT NOT NULL,
    PRIMARY KEY (telegram_id, period)
);

CREATE TABLE IF NOT EXISTS services (
    telegram_id INTEGER NOT NULL,
    client_email TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (telegram_id, client_email)
);

CREATE TABLE IF NOT EXISTS card_payments (
    order_id INTEGER PRIMARY KEY,
    receipt_file_id TEXT NOT NULL,
    receipt_unique_id TEXT,
    submitted_at TEXT NOT NULL,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    decision TEXT,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS receipt_selections (
    telegram_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    selected_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS discount_codes (
    code TEXT PRIMARY KEY,
    percent INTEGER NOT NULL CHECK(percent BETWEEN 1 AND 100),
    active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    telegram_id INTEGER,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


DEFAULT_PLANS = (
    ("gb10", "۱۰ گیگابایت", 10, 31, 75_000),
    ("gb20", "۲۰ گیگابایت", 20, 31, 150_000),
    ("gb30", "۳۰ گیگابایت", 30, 31, 225_000),
    ("gb50", "۵۰ گیگابایت", 50, 31, 350_000),
    ("gb75", "۷۵ گیگابایت", 75, 31, 525_000),
    ("gb100", "۱۰۰ گیگابایت", 100, 31, 700_000),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class Plan:
    code: str
    title_fa: str
    traffic_gb: int
    duration_days: int
    toman_price: int
    stars_price: int


class Database:
    def __init__(self, path: Path):
        self.path = path

    @asynccontextmanager
    async def connect(self):
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as db:
            await db.executescript(SCHEMA)
            for table, column, definition in (
                ("orders", "toman_amount", "INTEGER NOT NULL DEFAULT 0"),
                ("orders", "traffic_gb", "INTEGER NOT NULL DEFAULT 0"),
                ("orders", "duration_days", "INTEGER NOT NULL DEFAULT 0"),
                ("orders", "discount_code", "TEXT"),
                ("orders", "discount_percent", "INTEGER NOT NULL DEFAULT 0"),
                ("card_payments", "receipt_unique_id", "TEXT"),
                ("trials", "status", "TEXT NOT NULL DEFAULT 'complete'"),
            ):
                columns = {row[1] for row in await (await db.execute(f"PRAGMA table_info({table})")).fetchall()}
                if column not in columns:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS card_receipt_unique_idx ON card_payments(receipt_unique_id)")
            await db.executemany(
                """
                INSERT INTO plans(code,title_fa,traffic_gb,duration_days,toman_price)
                VALUES(?,?,?,?,?)
                ON CONFLICT(code) DO UPDATE SET
                  title_fa=excluded.title_fa,
                  traffic_gb=excluded.traffic_gb,
                  duration_days=excluded.duration_days,
                  toman_price=excluded.toman_price
                """,
                DEFAULT_PLANS,
            )
            await db.commit()

    async def upsert_user(self, telegram_id: int, username: str | None, full_name: str) -> None:
        now = now_iso()
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO users(telegram_id,username,full_name,created_at,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                  username=excluded.username, full_name=excluded.full_name,
                  updated_at=excluded.updated_at
                """,
                (telegram_id, username, full_name, now, now),
            )
            await db.commit()

    async def user_exists(self, telegram_id: int) -> bool:
        async with self.connect() as db:
            row = await (
                await db.execute("SELECT 1 FROM users WHERE telegram_id=?", (telegram_id,))
            ).fetchone()
            return row is not None

    async def has_accepted_terms(self, telegram_id: int, version: str) -> bool:
        async with self.connect() as db:
            row = await (
                await db.execute(
                    "SELECT terms_version FROM users WHERE telegram_id=?", (telegram_id,)
                )
            ).fetchone()
            return bool(row and row["terms_version"] == version)

    async def accept_terms(self, telegram_id: int, version: str) -> None:
        now = now_iso()
        async with self.connect() as db:
            await db.execute(
                "UPDATE users SET terms_version=?,terms_accepted_at=?,updated_at=? WHERE telegram_id=?",
                (version, now, now, telegram_id),
            )
            await db.commit()

    async def list_plans(self, only_enabled: bool = True) -> list[Plan]:
        where = "WHERE enabled=1" if only_enabled else ""
        async with self.connect() as db:
            rows = await (
                await db.execute(
                    f"SELECT code,title_fa,traffic_gb,duration_days,toman_price,stars_price FROM plans {where} ORDER BY traffic_gb"
                )
            ).fetchall()
        return [Plan(**dict(row)) for row in rows]

    async def get_plan(self, code: str) -> Plan | None:
        async with self.connect() as db:
            row = await (
                await db.execute(
                    "SELECT code,title_fa,traffic_gb,duration_days,toman_price,stars_price FROM plans WHERE code=? AND enabled=1",
                    (code,),
                )
            ).fetchone()
        return Plan(**dict(row)) if row else None

    async def set_stars_price(self, traffic_gb: int, stars: int) -> bool:
        async with self.connect() as db:
            cur = await db.execute(
                "UPDATE plans SET stars_price=? WHERE traffic_gb=?", (stars, traffic_gb)
            )
            await db.commit()
            return cur.rowcount == 1

    async def list_discount_codes(self):
        async with self.connect() as db:
            return await (await db.execute(
                "SELECT code,percent FROM discount_codes WHERE active=1 ORDER BY code"
            )).fetchall()

    async def save_discount_code(self, code: str, percent: int, admin_id: int) -> None:
        if not 1 <= percent <= 100:
            raise ValueError("Discount percentage must be between 1 and 100")
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO discount_codes(code,percent,active,created_by,created_at) VALUES(?,?,1,?,?) "
                "ON CONFLICT(code) DO UPDATE SET percent=excluded.percent,active=1,created_by=excluded.created_by",
                (code, percent, admin_id, now_iso()),
            )
            await db.commit()

    async def disable_discount_code(self, code: str) -> bool:
        async with self.connect() as db:
            cur = await db.execute(
                "UPDATE discount_codes SET active=0 WHERE code=? AND active=1", (code,)
            )
            await db.commit()
            return cur.rowcount == 1

    async def create_order(
        self, telegram_id: int, kind: str, plan: Plan, target_email: str | None,
        *, await_discount: bool = False,
    ) -> tuple[int, str]:
        now = now_iso()
        async with self.connect() as db:
            cur = await db.execute(
                """
                INSERT INTO orders(telegram_id,kind,plan_code,target_email,stars_amount,toman_amount,traffic_gb,duration_days,status,invoice_payload,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,'draft','',?,?)
                """,
                (telegram_id, kind, plan.code, target_email, plan.stars_price, plan.toman_price, plan.traffic_gb, plan.duration_days, now, now),
            )
            order_id = int(cur.lastrowid)
            payload = f"dv1:{order_id}:{telegram_id}:{kind}:{plan.code}"
            await db.execute(
                "UPDATE orders SET invoice_payload=?,status=? WHERE id=?",
                (payload, "await_discount" if await_discount else "pending", order_id),
            )
            await db.commit()
            return order_id, payload

    async def discount_order_waiting(self, order_id: int, telegram_id: int) -> bool:
        async with self.connect() as db:
            row = await (await db.execute(
                "SELECT 1 FROM orders WHERE id=? AND telegram_id=? AND status='await_discount'",
                (order_id, telegram_id),
            )).fetchone()
            return row is not None

    async def finalize_discount_order(self, order_id: int, telegram_id: int, code: str | None):
        """Atomically freeze the final price and consume the checkout choice once."""
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            order = await (await db.execute(
                "SELECT * FROM orders WHERE id=? AND telegram_id=? AND status='await_discount'",
                (order_id, telegram_id),
            )).fetchone()
            if not order:
                await db.rollback()
                return None
            discount = None
            if code:
                discount = await (await db.execute(
                    "SELECT code,percent FROM discount_codes WHERE code=? AND active=1", (code,)
                )).fetchone()
            percent = int(discount["percent"]) if discount else 0
            toman = (order["toman_amount"] * (100 - percent) + 99) // 100
            stars = (order["stars_amount"] * (100 - percent) + 99) // 100
            status = "processing" if percent == 100 else "pending"
            await db.execute(
                "UPDATE orders SET discount_code=?,discount_percent=?,toman_amount=?,stars_amount=?,status=?,updated_at=? WHERE id=?",
                (discount["code"] if discount else None, percent, toman, stars, status, now_iso(), order_id),
            )
            updated = await (await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))).fetchone()
            await db.commit()
            return updated

    async def get_order_for_checkout(self, payload: str, telegram_id: int):
        async with self.connect() as db:
            return await (
                await db.execute(
                    "SELECT * FROM orders WHERE invoice_payload=? AND telegram_id=? AND status='pending'",
                    (payload, telegram_id),
                )
            ).fetchone()

    async def claim_payment(
        self,
        payload: str,
        telegram_id: int,
        amount: int,
        charge_id: str,
        provider_charge_id: str | None,
    ):
        now = now_iso()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT * FROM orders WHERE telegram_charge_id=?", (charge_id,)
                )
            ).fetchone()
            if existing:
                await db.rollback()
                return None
            order = await (
                await db.execute(
                    "SELECT * FROM orders WHERE invoice_payload=? AND telegram_id=? AND stars_amount=? AND status='pending'",
                    (payload, telegram_id, amount),
                )
            ).fetchone()
            if not order:
                await db.rollback()
                return None
            await db.execute(
                """
                UPDATE orders SET status='processing',telegram_charge_id=?,provider_charge_id=?,updated_at=?
                WHERE id=?
                """,
                (charge_id, provider_charge_id, now, order["id"]),
            )
            await db.commit()
            return order

    async def claim_demo_order(self, order_id: int, telegram_id: int):
        """Atomically claim a pending demo order so a double click provisions once."""
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            order = await (
                await db.execute(
                    "SELECT * FROM orders WHERE id=? AND telegram_id=? AND status='pending'",
                    (order_id, telegram_id),
                )
            ).fetchone()
            if not order:
                await db.rollback()
                return None
            await db.execute(
                "UPDATE orders SET status='processing',updated_at=? WHERE id=?",
                (now_iso(), order_id),
            )
            await db.commit()
            return order

    async def begin_card_payment(self, order_id: int, telegram_id: int) -> bool:
        """Move a newly created order into the receipt-upload stage."""
        async with self.connect() as db:
            cur = await db.execute(
                """
                UPDATE orders SET status='card_waiting_receipt',updated_at=?
                WHERE id=? AND telegram_id=? AND status='pending'
                """,
                (now_iso(), order_id, telegram_id),
            )
            await db.commit()
            return cur.rowcount == 1

    async def select_card_receipt(self, order_id: int, telegram_id: int) -> bool:
        if not await self.card_receipt_waiting(order_id, telegram_id):
            return False
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO receipt_selections(telegram_id,order_id,selected_at) VALUES(?,?,?) "
                "ON CONFLICT(telegram_id) DO UPDATE SET order_id=excluded.order_id,selected_at=excluded.selected_at",
                (telegram_id, order_id, now_iso()),
            )
            await db.commit()
        return True

    async def selected_card_receipt(self, telegram_id: int) -> int | None:
        async with self.connect() as db:
            row = await (await db.execute(
                "SELECT order_id FROM receipt_selections WHERE telegram_id=?", (telegram_id,)
            )).fetchone()
            return int(row[0]) if row else None

    async def submit_card_receipt(self, order_id: int, telegram_id: int, receipt_file_id: str, receipt_unique_id: str):
        """Atomically accept one receipt and make it available to administrators."""
        now = now_iso()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            order = await (
                await db.execute(
                    """
                    SELECT * FROM orders WHERE id=? AND telegram_id=?
                    AND status='card_waiting_receipt'
                    """,
                    (order_id, telegram_id),
                )
            ).fetchone()
            if not order:
                await db.rollback()
                return None
            try:
                await db.execute(
                    "INSERT INTO card_payments(order_id,receipt_file_id,receipt_unique_id,submitted_at) VALUES(?,?,?,?)",
                    (order_id, receipt_file_id, receipt_unique_id, now),
                )
            except sqlite3.IntegrityError:
                await db.rollback()
                return None
            await db.execute(
                "UPDATE orders SET status='card_pending_review',updated_at=? WHERE id=?",
                (now, order_id),
            )
            await db.execute("DELETE FROM receipt_selections WHERE telegram_id=?", (telegram_id,))
            await db.commit()
            return order

    async def pending_card_receipts(self):
        async with self.connect() as db:
            return await (await db.execute(
                "SELECT o.*, c.receipt_file_id FROM orders o JOIN card_payments c ON c.order_id=o.id "
                "WHERE o.status='card_pending_review' ORDER BY c.submitted_at LIMIT 25"
            )).fetchall()

    async def card_receipt_waiting(self, order_id: int, telegram_id: int) -> bool:
        async with self.connect() as db:
            row = await (
                await db.execute(
                    """
                    SELECT 1 FROM orders WHERE id=? AND telegram_id=?
                    AND status='card_waiting_receipt'
                    """,
                    (order_id, telegram_id),
                )
            ).fetchone()
            return row is not None

    async def claim_card_payment_approval(self, order_id: int, admin_id: int):
        """Claim a receipt for provisioning so concurrent admin clicks are idempotent."""
        now = now_iso()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            order = await (
                await db.execute(
                    "SELECT * FROM orders WHERE id=? AND status='card_pending_review'", (order_id,)
                )
            ).fetchone()
            if not order:
                await db.rollback()
                return None
            await db.execute(
                "UPDATE orders SET status='processing',updated_at=? WHERE id=?", (now, order_id)
            )
            await db.execute(
                "UPDATE card_payments SET reviewed_by=?,reviewed_at=?,decision='approved' WHERE order_id=?",
                (admin_id, now, order_id),
            )
            await db.commit()
            return order

    async def reject_card_payment(self, order_id: int, admin_id: int):
        now = now_iso()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            order = await (
                await db.execute(
                    "SELECT * FROM orders WHERE id=? AND status='card_pending_review'", (order_id,)
                )
            ).fetchone()
            if not order:
                await db.rollback()
                return None
            await db.execute(
                "UPDATE orders SET status='card_rejected',updated_at=? WHERE id=?", (now, order_id)
            )
            await db.execute(
                "UPDATE card_payments SET reviewed_by=?,reviewed_at=?,decision='rejected' WHERE order_id=?",
                (admin_id, now, order_id),
            )
            await db.commit()
            return order

    async def cancel_demo_order(self, order_id: int, telegram_id: int) -> bool:
        async with self.connect() as db:
            cur = await db.execute(
                """
                UPDATE orders SET status='cancelled',updated_at=?
                WHERE id=? AND telegram_id=? AND status='pending'
                """,
                (now_iso(), order_id, telegram_id),
            )
            await db.commit()
            return cur.rowcount == 1

    async def get_order(self, order_id: int):
        async with self.connect() as db:
            return await (
                await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))
            ).fetchone()

    async def claim_retry_order(self, order_id: int):
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            order = await (await db.execute(
                "SELECT * FROM orders WHERE id=? AND kind='buy' AND status IN ('paid_error','provision_error')",
                (order_id,),
            )).fetchone()
            if not order:
                await db.rollback()
                return None
            await db.execute(
                "UPDATE orders SET status='processing',updated_at=? WHERE id=?", (now_iso(), order_id)
            )
            await db.commit()
            return order

    async def mark_order_result(
        self, order_id: int, status: str, email: str | None = None, error: str | None = None
    ) -> None:
        async with self.connect() as db:
            await db.execute(
                "UPDATE orders SET status=?,provisioned_email=?,error=?,updated_at=? WHERE id=?",
                (status, email, error, now_iso(), order_id),
            )
            await db.commit()

    async def trial_used(self, telegram_id: int, period: str) -> bool:
        async with self.connect() as db:
            row = await (
                await db.execute(
                    "SELECT 1 FROM trials WHERE telegram_id=? AND period=?",
                    (telegram_id, period),
                )
            ).fetchone()
            return row is not None

    async def claim_trial(self, telegram_id: int, period: str, email: str) -> bool:
        async with self.connect() as db:
            cur = await db.execute(
                "INSERT OR IGNORE INTO trials(telegram_id,period,client_email,status,created_at) VALUES(?,?,?,'processing',?)",
                (telegram_id, period, email, now_iso()),
            )
            await db.commit()
            return cur.rowcount == 1

    async def complete_trial(self, telegram_id: int, period: str, email: str) -> None:
        async with self.connect() as db:
            await db.execute(
                "UPDATE trials SET status='complete' WHERE telegram_id=? AND period=? AND client_email=?",
                (telegram_id, period, email),
            )
            await db.execute(
                "INSERT OR IGNORE INTO services(telegram_id,client_email,source,created_at) VALUES(?,?,?,?)",
                (telegram_id, email, "trial", now_iso()),
            )
            await db.commit()

    async def record_trial(self, telegram_id: int, period: str, email: str) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO trials(telegram_id,period,client_email,created_at) VALUES(?,?,?,?)",
                (telegram_id, period, email, now_iso()),
            )
            await db.execute(
                "INSERT OR IGNORE INTO services(telegram_id,client_email,source,created_at) VALUES(?,?,?,?)",
                (telegram_id, email, "trial", now_iso()),
            )
            await db.commit()

    async def record_service(self, telegram_id: int, email: str, source: str) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO services(telegram_id,client_email,source,created_at) VALUES(?,?,?,?)",
                (telegram_id, email, source, now_iso()),
            )
            await db.commit()

    async def audit(self, event: str, telegram_id: int | None, details: dict) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO audit_log(event,telegram_id,details_json,created_at) VALUES(?,?,?,?)",
                (event, telegram_id, json.dumps(details, ensure_ascii=False), now_iso()),
            )
            await db.commit()

    async def summary(self) -> dict[str, int]:
        async with self.connect() as db:
            users = (await (await db.execute("SELECT count(*) FROM users")).fetchone())[0]
            paid = (await (await db.execute("SELECT count(*) FROM orders WHERE status='complete'")).fetchone())[0]
            failed = (await (await db.execute("SELECT count(*) FROM orders WHERE status='paid_error'")).fetchone())[0]
            trials = (await (await db.execute("SELECT count(*) FROM trials")).fetchone())[0]
        return {"users": users, "paid": paid, "failed": failed, "trials": trials}

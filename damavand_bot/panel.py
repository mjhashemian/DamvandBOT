"""SSH-tunnel-only management UI. No public listener or privileged service."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import secrets
import signal
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from aiohttp import web

from .management import (
    AUTH_PATH, CONFIG_DIR, CONFIG_PATH, PANEL_DOMAIN_PATH,
    SECRET_SETTINGS, SETTINGS,
    read_config, validate_settings, verify_panel_password, write_config,
)
from .menu import MENU_BUTTONS, MENU_SETTING_KEYS

SESSIONS: dict[str, tuple[float, str]] = {}
LOGIN_CHALLENGES: dict[str, tuple[float, str]] = {}
FAILURES: dict[str, list[float]] = {}
COOKIE = "damavand_panel"
LOGIN_COOKIE = "damavand_login"
NULL_ORIGIN_CSRF_PATHS = frozenset({"/login", "/logout", "/plans", "/discounts", "/services", "/settings", "/settings/reveal", "/buttons", "/terms"})
SETTING_GROUPS = (
    ("Bot settings", ("BOT_TOKEN", "ADMIN_IDS", "SUPPORT_USERNAME", "XUI_BASE_URL", "XUI_API_TOKEN", "XUI_SUBSCRIPTION_BASE")),
    ("Welcome settings", ("BOT_DESCRIPTION", "WELCOME_MESSAGE")),
    ("Channel settings", ("CHANNEL_JOIN_REQUIRED", "CHANNEL_USERNAME", "CHANNEL_ID", "CHANNEL_JOIN_URL")),
    ("Payment settings", ("CARD_PAYMENT_ENABLED", "CARD_PAYMENT_CARD_NUMBER", "CARD_PAYMENT_HOLDER", "CARD_PAYMENT_INSTRUCTIONS")),
    ("Trial settings", ("TRIAL_ENABLED", "TRIAL_MB", "TRIAL_DAYS")),
)
SETTING_LABELS = {
    "BOT_TOKEN": "Bot Token", "ADMIN_IDS": "Admin Telegram IDs", "SUPPORT_USERNAME": "Support username",
    "BOT_DESCRIPTION": "Before-START description", "WELCOME_MESSAGE": "Welcome message",
    "XUI_BASE_URL": "3x-ui panel URL", "XUI_API_TOKEN": "3x-ui API token",
    "XUI_SUBSCRIPTION_BASE": "Subscription base URL", "CHANNEL_USERNAME": "Channel username",
    "CHANNEL_ID": "Channel ID", "CHANNEL_JOIN_URL": "Channel join link", "CHANNEL_JOIN_REQUIRED": "Require channel membership",
    "CARD_PAYMENT_ENABLED": "Card-to-card payment", "CARD_PAYMENT_CARD_NUMBER": "Card number",
    "CARD_PAYMENT_HOLDER": "Cardholder name", "CARD_PAYMENT_INSTRUCTIONS": "Payment instructions",
    "TRIAL_ENABLED": "Free trial", "TRIAL_MB": "Trial traffic (MB)", "TRIAL_DAYS": "Trial duration (days)",
}


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


@contextmanager
def database():
    path = Path(read_config()["DB_PATH"])
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def audit(db: sqlite3.Connection, event: str, details: dict) -> None:
    db.execute(
        "INSERT INTO audit_log(event,telegram_id,details_json,created_at) VALUES(?,NULL,?,datetime('now'))",
        (event, json.dumps(details, ensure_ascii=False)),
    )


def page(title: str, content: str, csrf: str = "", message: str = "") -> web.Response:
    sidebar = f'<div id="panel-sidebar" data-csrf="{esc(csrf)}"></div>' if csrf else ""
    body_class = "app" if csrf else "login"
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Damavand Bot</title>
<link rel="stylesheet" href="/static/panel.css"><script defer src="/static/panel.js"></script></head>
<body class="{body_class}">{sidebar}<main><h1>{esc(title)}</h1>{f'<p class="notice">{esc(message)}</p>' if message else ''}{content}</main></body></html>"""
    return web.Response(text=body, content_type="text/html", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    })


def token(request: web.Request) -> str:
    sid = request.cookies.get(COOKIE, "")
    record = SESSIONS.get(sid)
    if not record or record[0] < time.monotonic():
        SESSIONS.pop(sid, None)
        raise web.HTTPSeeOther("/login")
    return record[1]


async def form(request: web.Request, csrf: str):
    data = await request.post()
    if not secrets.compare_digest(str(data.get("csrf", "")), csrf):
        raise web.HTTPForbidden(text="Invalid CSRF token")
    return data


@web.middleware
async def guard(request: web.Request, handler):
    domain = PANEL_DOMAIN_PATH.read_text(encoding="ascii").strip() if PANEL_DOMAIN_PATH.exists() else ""
    backend_port = 8766 if domain else 8765
    local_hosts = {f"127.0.0.1:{backend_port}", f"localhost:{backend_port}", f"[::1]:{backend_port}"}
    if domain and request.remote == "127.0.0.1" and request.headers.get("X-Forwarded-Proto") == "https":
        request = request.clone(scheme="https")
    if request.host not in local_hosts and not (request.secure and request.host == f"{domain}:8765"):
        raise web.HTTPForbidden(text="Invalid panel host or insecure connection")
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and origin != f"{request.scheme}://{request.host}" and not (origin == "null" and request.path in NULL_ORIGIN_CSRF_PATHS):
            logging.warning(
                "Rejected panel POST origin=%r expected=%r path=%r",
                origin[:200], f"{request.scheme}://{request.host}", request.path[:100],
            )
            raise web.HTTPForbidden(text="Invalid origin")
    return await handler(request)


async def login(request: web.Request):
    if request.method == "GET":
        now = time.monotonic()
        for sid, (expires, _) in list(LOGIN_CHALLENGES.items()):
            if expires < now:
                LOGIN_CHALLENGES.pop(sid, None)
        if len(LOGIN_CHALLENGES) >= 10000:
            LOGIN_CHALLENGES.clear()
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        LOGIN_CHALLENGES[sid] = (now + 600, csrf)
        response = page("Sign in", f'<div class="card"><form method="post"><input type="hidden" name="login_csrf" value="{esc(csrf)}"><label>Panel password</label><input type="password" name="password" autocomplete="current-password" required><p><button>Sign in</button></p></form></div>')
        response.set_cookie(LOGIN_COOKIE, sid, httponly=True, secure=request.secure, samesite="Strict", path="/login", max_age=600)
        return response
    now = time.monotonic()
    client_ip = request.headers.get("X-Real-IP", "") if request.remote == "127.0.0.1" and request.headers.get("X-Forwarded-Proto") == "https" else request.remote or "unknown"
    attempts = FAILURES[client_ip] = [at for at in FAILURES.get(client_ip, []) if now - at < 900]
    if len(attempts) >= 10:
        raise web.HTTPTooManyRequests(text="Too many sign-in attempts. Try again later.")
    data = await request.post()
    sid = request.cookies.get(LOGIN_COOKIE, "")
    challenge = LOGIN_CHALLENGES.get(sid)
    if not challenge or challenge[0] < now or not secrets.compare_digest(str(data.get("login_csrf", "")), challenge[1]):
        raise web.HTTPForbidden(text="Invalid login form; refresh the sign-in page and try again")
    if not verify_panel_password(str(data.get("password", ""))):
        attempts.append(now)
        return page("Sign in", '<p class="error">Invalid password.</p><p><a href="/login">Try again</a></p>')
    LOGIN_CHALLENGES.pop(sid, None)
    FAILURES.pop(client_ip, None)
    sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    SESSIONS[sid] = (now + 8 * 3600, csrf)
    response = web.HTTPSeeOther("/")
    response.del_cookie(LOGIN_COOKIE, path="/login")
    response.set_cookie(COOKIE, sid, httponly=True, secure=request.secure, samesite="Strict", path="/", max_age=8 * 3600)
    raise response


async def logout(request: web.Request):
    csrf = token(request)
    await form(request, csrf)
    SESSIONS.pop(request.cookies.get(COOKIE, ""), None)
    response = web.HTTPSeeOther("/login")
    response.del_cookie(COOKIE, path="/")
    raise response


async def overview(request: web.Request):
    csrf = token(request)
    if not CONFIG_PATH.exists():
        return page("Finish setup", '<div class="card"><p>The panel is ready. Add the Telegram bot token, admin IDs, channel, 3x-ui API and payment settings in <a href="/settings">Bot settings</a>. The bot will start after valid settings are saved.</p></div>', csrf)
    with database() as db:
        counts = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("users", "orders", "services", "trials")}
        pending = db.execute("SELECT count(*) FROM orders WHERE status='card_pending_review'").fetchone()[0]
        recent = db.execute("SELECT id,telegram_id,kind,toman_amount,status,created_at FROM orders ORDER BY id DESC LIMIT 12").fetchall()
    cards = "".join(f'<div class="card"><h2>{esc(label)}</h2><strong>{value}</strong></div>' for label, value in (*counts.items(), ("Receipts awaiting review", pending)))
    rows = "".join(f"<tr><td>{r['id']}</td><td>{r['telegram_id']}</td><td>{esc(r['kind'])}</td><td>{r['toman_amount']:,}</td><td>{esc(r['status'])}</td><td>{esc(r['created_at'])}</td></tr>" for r in recent)
    return page("Overview", f'<p class="muted">Payment receipts must be verified against your bank account before approval in Telegram.</p><div class="row">{cards}</div><div class="card scroll"><h2>Recent orders</h2><table><tr><th>ID</th><th>User</th><th>Kind</th><th>Toman</th><th>Status</th><th>Created</th></tr>{rows}</table></div>', csrf)


async def users(request: web.Request):
    csrf = token(request)
    with database() as db:
        rows = db.execute("SELECT telegram_id,username,full_name,terms_version,created_at FROM users ORDER BY updated_at DESC LIMIT 200").fetchall()
    body = "".join(f"<tr><td>{r['telegram_id']}</td><td>{esc(r['username'])}</td><td>{esc(r['full_name'])}</td><td>{esc(r['terms_version'])}</td><td>{esc(r['created_at'])}</td></tr>" for r in rows)
    return page("Users", f'<div class="card scroll"><p>Latest 200 users</p><table><tr><th>Telegram ID</th><th>Username</th><th>Name</th><th>Terms</th><th>Joined</th></tr>{body}</table></div>', csrf)


def resolve_user(db: sqlite3.Connection, identifier: str) -> int:
    identifier = identifier.strip()
    if re.fullmatch(r"[1-9]\d{0,19}", identifier):
        return int(identifier)
    if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", identifier):
        matches = db.execute(
            "SELECT telegram_id FROM users WHERE lower(username)=lower(?)",
            (identifier[1:],),
        ).fetchall()
        if len(matches) == 1:
            return int(matches[0][0])
        raise web.HTTPBadRequest(text="Username is not uniquely known. Ask the user to start the bot, or use their numeric Telegram ID.")
    raise web.HTTPBadRequest(text="Enter a numeric Telegram ID or a known @username")


async def manual_services(request: web.Request):
    csrf = token(request)
    with database() as db:
        rows = db.execute(
            "SELECT m.id,m.telegram_id,u.username,m.label,m.plan_code,m.subscription_url,m.created_at "
            "FROM manual_services m JOIN users u ON u.telegram_id=m.telegram_id ORDER BY m.id DESC LIMIT 200"
        ).fetchall()
        pending = db.execute(
            "SELECT id,username,label,plan_code,subscription_url,created_at "
            "FROM pending_services ORDER BY id DESC LIMIT 200"
        ).fetchall()
        plans = db.execute("SELECT code,title_fa FROM plans ORDER BY traffic_gb,code").fetchall()
    options = '<option value="">No plan label</option>' + "".join(
        f'<option value="{esc(row["code"])}">{esc(row["title_fa"])} ({esc(row["code"])})</option>'
        for row in plans
    )
    form_html = (
        f'<div class="card"><h2>Assign an existing service</h2><form method="post" action="/services">'
        f'<input type="hidden" name="csrf" value="{esc(csrf)}">'
        '<label>Telegram ID or @username<input name="user" required></label>'
        f'<label>Plan<select name="plan_code">{options}</select></label>'
        '<label>Service label (optional)<input name="label" maxlength="80" placeholder="Existing 3x-ui service"></label>'
        '<label>Subscription link<input name="subscription_url" type="url" maxlength="2048" required></label>'
        '<p class="muted">If the @username has not started the bot, this link waits here and attaches automatically on their first /start. Telegram usernames can change or be reassigned before then; use a numeric ID when available.</p>'
        '<p class="muted">This adds the link to My Services. It does not edit the 3x-ui client or enable renewal for this imported service.</p>'
        '<button>Assign service</button></form></div>'
    )
    body = "".join(
        f'<tr><td>{row["id"]}</td><td>{row["telegram_id"]}</td><td>{esc("@" + row["username"] if row["username"] else "")}</td>'
        f'<td>{esc(row["label"])}</td><td>{esc(row["plan_code"])}</td><td><code>{esc(row["subscription_url"])}</code></td>'
        f'<td><form method="post" action="/services"><input type="hidden" name="csrf" value="{esc(csrf)}">'
        f'<input type="hidden" name="action" value="remove"><input type="hidden" name="id" value="{row["id"]}"><button class="danger">Remove</button></form></td></tr>'
        for row in rows
    )
    pending_body = "".join(
        f'<tr><td>{row["id"]}</td><td>{esc("@" + row["username"])}</td>'
        f'<td>{esc(row["label"])}</td><td>{esc(row["plan_code"])}</td>'
        f'<td><code>{esc(row["subscription_url"])}</code></td>'
        f'<td><form method="post" action="/services"><input type="hidden" name="csrf" value="{esc(csrf)}">'
        f'<input type="hidden" name="action" value="remove_pending"><input type="hidden" name="id" value="{row["id"]}">'
        '<button class="danger">Remove</button></form></td></tr>'
        for row in pending
    )
    pending_section = (
        '<div class="card scroll"><h2>Waiting for first /start</h2>'
        '<table><tr><th>ID</th><th>Username</th><th>Service</th><th>Plan</th><th>Link</th><th></th></tr>'
        f'{pending_body}</table></div>'
    )
    assigned_section = (
        '<div class="card scroll"><h2>Assigned to Telegram accounts</h2>'
        '<table><tr><th>ID</th><th>Telegram ID</th><th>Username</th><th>Service</th><th>Plan</th><th>Link</th><th></th></tr>'
        f'{body}</table></div>'
    )
    return page("Assigned services", form_html + pending_section + assigned_section, csrf, request.query.get("message", ""))


async def save_manual_service(request: web.Request):
    csrf = token(request)
    data = await form(request, csrf)
    pending_username = None
    with database() as db:
        if data.get("action") in {"remove", "remove_pending"}:
            try:
                service_id = int(data.get("id", ""))
            except ValueError:
                raise web.HTTPBadRequest(text="Invalid service ID")
            pending = data.get("action") == "remove_pending"
            db.execute("DELETE FROM pending_services WHERE id=?" if pending else "DELETE FROM manual_services WHERE id=?", (service_id,))
            audit(db, "panel.pending_service_removed" if pending else "panel.manual_service_removed", {"id": service_id})
        else:
            identifier = str(data.get("user", "")).strip()
            pending_username = None
            if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", identifier):
                matches = db.execute(
                    "SELECT telegram_id FROM users WHERE lower(username)=lower(?)", (identifier[1:],)
                ).fetchall()
                if len(matches) > 1:
                    raise web.HTTPBadRequest(text="Username matches multiple known accounts; use a numeric Telegram ID")
                telegram_id = int(matches[0][0]) if matches else None
                pending_username = identifier[1:].lower() if not matches else None
            else:
                telegram_id = resolve_user(db, identifier)
            if telegram_id is not None and telegram_id >= 2**63:
                raise web.HTTPBadRequest(text="Telegram ID is too large")
            label = str(data.get("label", "")).strip() or "Existing 3x-ui service"
            plan_code = str(data.get("plan_code", "")).strip() or None
            link = str(data.get("subscription_url", "")).strip()
            try:
                parsed = urlsplit(link)
                port_valid = parsed.port is None or 1 <= parsed.port <= 65535
            except ValueError:
                raise web.HTTPBadRequest(text="Enter a valid HTTPS subscription link")
            if len(label) > 80 or len(link) > 2048 or parsed.scheme != "https" or not parsed.hostname or not port_valid or parsed.username or parsed.password or parsed.fragment or not parsed.path.strip("/") or any(ord(ch) < 32 for ch in link):
                raise web.HTTPBadRequest(text="Enter a label and valid HTTPS subscription link")
            if plan_code and not db.execute("SELECT 1 FROM plans WHERE code=?", (plan_code,)).fetchone():
                raise web.HTTPBadRequest(text="Unknown plan")
            if db.execute("SELECT 1 FROM manual_services WHERE subscription_url=?", (link,)).fetchone() or db.execute("SELECT 1 FROM pending_services WHERE subscription_url=?", (link,)).fetchone():
                raise web.HTTPBadRequest(text="This subscription link is already assigned")
            now = datetime.now(timezone.utc).isoformat()
            if pending_username:
                db.execute(
                    "INSERT INTO pending_services(username,plan_code,label,subscription_url,created_at) VALUES(?,?,?,?,?)",
                    (pending_username, plan_code, label, link, now),
                )
                audit(db, "panel.pending_service_added", {"username": pending_username, "plan_code": plan_code})
            else:
                db.execute(
                    "INSERT OR IGNORE INTO users(telegram_id,username,full_name,created_at,updated_at) VALUES(?,NULL,'Assigned user',?,?)",
                    (telegram_id, now, now),
                )
                db.execute(
                    "INSERT INTO manual_services(telegram_id,plan_code,label,subscription_url,created_at) VALUES(?,?,?,?,?)",
                    (telegram_id, plan_code, label, link, now),
                )
                audit(db, "panel.manual_service_added", {"telegram_id": telegram_id, "plan_code": plan_code})
    message = "Waiting for username to start the bot" if pending_username else "Service assignment saved" if data.get("action") not in {"remove", "remove_pending"} else "Service removed"
    raise web.HTTPSeeOther("/services?message=" + quote(message))


async def orders(request: web.Request):
    csrf = token(request)
    with database() as db:
        rows = db.execute("SELECT id,telegram_id,kind,plan_code,toman_amount,stars_amount,discount_code,status,provisioned_email,created_at FROM orders ORDER BY id DESC LIMIT 200").fetchall()
    body = "".join(f"<tr><td>{r['id']}</td><td>{r['telegram_id']}</td><td>{esc(r['kind'])}</td><td>{esc(r['plan_code'])}</td><td>{r['toman_amount']:,}</td><td>{r['stars_amount']}</td><td>{esc(r['discount_code'])}</td><td>{esc(r['status'])}</td><td>{esc(r['provisioned_email'])}</td><td>{esc(r['created_at'])}</td></tr>" for r in rows)
    return page("Orders", f'<div class="card scroll"><p>Latest 200 orders. Receipt approval and retries remain in the bot, where provisioning is transactional.</p><table><tr><th>ID</th><th>User</th><th>Kind</th><th>Plan</th><th>Toman</th><th>Stars</th><th>Discount</th><th>Status</th><th>Service</th><th>Created</th></tr>{body}</table></div>', csrf)


async def audit_log(request: web.Request):
    csrf = token(request)
    with database() as db:
        rows = db.execute("SELECT event,telegram_id,details_json,created_at FROM audit_log ORDER BY id DESC LIMIT 200").fetchall()
    body = "".join(f"<tr><td>{esc(r['created_at'])}</td><td>{esc(r['event'])}</td><td>{esc(r['telegram_id'])}</td><td>{esc(r['details_json'])}</td></tr>" for r in rows)
    return page("Audit", f'<div class="card scroll"><table><tr><th>Time</th><th>Event</th><th>Telegram ID</th><th>Details</th></tr>{body}</table></div>', csrf)


async def plans(request: web.Request):
    csrf = token(request)
    with database() as db:
        rows = db.execute("SELECT * FROM plans ORDER BY traffic_gb,code").fetchall()
    forms = ""
    for row in rows:
        fields = "".join(f'<label>{esc(label)}<input name="{key}" type="number" min="{minimum}" value="{row[key]}" required></label>' for key, label, minimum in (
            ("traffic_gb", "Traffic GB", 1), ("duration_days", "Days", 1),
            ("toman_price", "Toman", 0), ("stars_price", "Stars", 0),
        ))
        forms += f'<div class="card"><form method="post" action="/plans"><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="code" value="{esc(row["code"])}"><h2>{esc(row["code"])}</h2><label>Persian title<input name="title_fa" value="{esc(row["title_fa"])}" maxlength="100" required></label><div class="row">{fields}<label>Enabled<select name="enabled"><option value="1" {"selected" if row["enabled"] else ""}>Yes</option><option value="0" {"selected" if not row["enabled"] else ""}>No</option></select></label></div><p><button>Save plan</button></p></form></div>'
    forms += f'<div class="card"><h2>Add plan</h2><form method="post" action="/plans"><input type="hidden" name="csrf" value="{esc(csrf)}"><label>Code<input name="code" pattern="[a-z0-9_-]{{2,32}}" placeholder="my_panel" required></label><label>Persian title<input name="title_fa" maxlength="100" required></label><div class="row">' + "".join(f'<label>{label}<input name="{key}" type="number" min="{minimum}" required></label>' for key,label,minimum in (("traffic_gb","Traffic GB",1),("duration_days","Days",1),("toman_price","Toman",0),("stars_price","Stars",0))) + '</div><button>Add plan</button></form></div>'
    return page("Plans", forms, csrf, request.query.get("message", ""))


async def save_plan(request: web.Request):
    csrf = token(request)
    data = await form(request, csrf)
    code, title = str(data.get("code", "")).strip(), str(data.get("title_fa", "")).strip()
    if not re.fullmatch(r"[a-z0-9_-]{2,32}", code) or not title or len(title) > 100:
        raise web.HTTPBadRequest(text="Invalid plan code or title")
    try:
        values = [int(data[key]) for key in ("traffic_gb", "duration_days", "toman_price", "stars_price")]
    except (ValueError, KeyError):
        raise web.HTTPBadRequest(text="Invalid plan numbers")
    if not (1 <= values[0] <= 100000 and 1 <= values[1] <= 3650 and 0 <= values[2] <= 10**12 and 0 <= values[3] <= 10**9):
        raise web.HTTPBadRequest(text="Plan value out of range")
    enabled = 1 if data.get("enabled", "1") == "1" else 0
    with database() as db:
        db.execute("INSERT INTO plans(code,title_fa,traffic_gb,duration_days,toman_price,stars_price,enabled) VALUES(?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET title_fa=excluded.title_fa,traffic_gb=excluded.traffic_gb,duration_days=excluded.duration_days,toman_price=excluded.toman_price,stars_price=excluded.stars_price,enabled=excluded.enabled", (code, title, *values, enabled))
        audit(db, "panel.plan_saved", {"code": code})
    raise web.HTTPSeeOther("/plans?message=" + quote("Plan saved"))


async def discounts(request: web.Request):
    csrf = token(request)
    with database() as db:
        rows = db.execute("SELECT d.code,d.percent,d.active,d.target_telegram_id,d.target_plan_code,u.username FROM discount_codes d LEFT JOIN users u ON u.telegram_id=d.target_telegram_id ORDER BY d.created_at DESC").fetchall()
        plans = db.execute("SELECT code,title_fa FROM plans ORDER BY traffic_gb,code").fetchall()
    options = '<option value="">All plans</option>' + "".join(f'<option value="{esc(row["code"])}">{esc(row["title_fa"])} ({esc(row["code"])})</option>' for row in plans)
    body = "".join(
        f'<tr><td>{esc(row["code"])}</td><td>{row["percent"]}%</td>'
        f'<td>{esc("@" + row["username"] if row["username"] else row["target_telegram_id"] or "All users")}</td>'
        f'<td>{esc(row["target_plan_code"] or "All plans")}</td><td>{"Active" if row["active"] else "Disabled"}</td>'
        f'<td><form method="post" action="/discounts"><input type="hidden" name="csrf" value="{esc(csrf)}">'
        f'<input type="hidden" name="code" value="{esc(row["code"])}"><input type="hidden" name="action" value="toggle">'
        f'<button>{"Disable" if row["active"] else "Enable"}</button></form></td></tr>' for row in rows
    )
    form_html = (
        f'<div class="card"><h2>Create discount</h2><form method="post" action="/discounts"><input type="hidden" name="csrf" value="{esc(csrf)}">'
        '<div class="row"><label>Code<input name="code" placeholder="LASTOFUS-35" required></label>'
        '<label>Percentage<input name="percent" type="number" min="1" max="100" required></label></div>'
        '<div class="row"><label>Specific user (blank = all users)<input name="user_scope" placeholder="Telegram ID or known @username"></label>'
        f'<label>Plan<select name="plan_scope">{options}</select></label></div>'
        '<p class="muted">A username must already be known to the bot. Use a numeric Telegram ID for users who have not started it yet.</p>'
        '<button>Save discount</button></form></div>'
    )
    return page("Discounts", form_html + f'<div class="card scroll"><table><tr><th>Code</th><th>Percent</th><th>User</th><th>Plan</th><th>Status</th><th></th></tr>{body}</table></div>', csrf, request.query.get("message", ""))


async def save_discount(request: web.Request):
    csrf = token(request)
    data = await form(request, csrf)
    code = str(data.get("code", "")).strip().upper()
    with database() as db:
        if data.get("action") == "toggle":
            db.execute("UPDATE discount_codes SET active=1-active WHERE code=?", (code,))
            audit(db, "panel.discount_toggled", {"code": code})
        else:
            if not re.fullmatch(r"[A-Z0-9_-]{2,32}", code):
                raise web.HTTPBadRequest(text="Invalid discount code")
            try:
                percent = int(data["percent"])
            except (KeyError, ValueError):
                raise web.HTTPBadRequest(text="Invalid percentage")
            if not 1 <= percent <= 100:
                raise web.HTTPBadRequest(text="Percentage must be 1–100")
            user_scope = str(data.get("user_scope", "")).strip()
            target_user = resolve_user(db, user_scope) if user_scope else None
            if target_user is not None and target_user >= 2**63:
                raise web.HTTPBadRequest(text="Telegram ID is too large")
            target_plan = str(data.get("plan_scope", "")).strip() or None
            if target_plan and not db.execute("SELECT 1 FROM plans WHERE code=?", (target_plan,)).fetchone():
                raise web.HTTPBadRequest(text="Unknown plan")
            db.execute(
                "INSERT INTO discount_codes(code,percent,active,target_telegram_id,target_plan_code,created_by,created_at) VALUES(?,?,1,?,?,0,datetime('now')) "
                "ON CONFLICT(code) DO UPDATE SET percent=excluded.percent,active=1,target_telegram_id=excluded.target_telegram_id,target_plan_code=excluded.target_plan_code",
                (code, percent, target_user, target_plan),
            )
            audit(db, "panel.discount_saved", {"code": code, "percent": percent, "target_user": target_user, "target_plan": target_plan})
    raise web.HTTPSeeOther("/discounts?message=" + quote("Discount updated"))


async def settings(request: web.Request):
    csrf = token(request)
    data = read_config()
    sections = []
    for title, keys in SETTING_GROUPS:
        fields = []
        for key in keys:
            if key in {"CARD_PAYMENT_ENABLED", "TRIAL_ENABLED", "CHANNEL_JOIN_REQUIRED"}:
                field = f'<select name="{key}"><option value="true" {"selected" if data.get(key)=="true" else ""}>Enabled</option><option value="false" {"selected" if data.get(key)!="true" else ""}>Disabled</option></select>'
            elif key in {"CARD_PAYMENT_INSTRUCTIONS", "BOT_DESCRIPTION", "WELCOME_MESSAGE"}:
                maxlength = 512 if key == "BOT_DESCRIPTION" else 900 if key == "WELCOME_MESSAGE" else 4096
                field = f'<textarea name="{key}" maxlength="{maxlength}">{esc(data.get(key,""))}</textarea>'
            elif key in SECRET_SETTINGS:
                placeholder = "Saved — leave blank to keep current" if data.get(key) else ""
                field = (
                    f'<div class="secret-field"><input name="{key}" type="password" value="" '
                    f'placeholder="{esc(placeholder)}" maxlength="4096" autocomplete="off">'
                    f'<button type="button" class="reveal-secret" data-secret-key="{key}">Show</button></div>'
                )
            else:
                field = f'<input name="{key}" type="text" value="{esc(data.get(key, ""))}" maxlength="4096" autocomplete="off">'
            fields.append(f'<label>{esc(SETTING_LABELS[key])}{field}</label>')
        hint = '<p class="muted">The description appears before START. The banner and welcome message are sent after /start. Use {name} and {requirements} as optional placeholders in the welcome message.</p>' if title == "Welcome settings" else ""
        sections.append(f'<section class="card setting-group"><h2>{esc(title)}</h2>{hint}<div class="setting-grid">{"".join(fields)}</div></section>')
    intro = "Enter the bot and 3x-ui details to finish setup. Channel membership is optional." if not CONFIG_PATH.exists() else "Saved values remain here after refresh. Secret tokens are hidden; leave them blank to keep the saved value."
    body = f'<p class="muted">{esc(intro)}</p><form method="post" action="/settings"><input type="hidden" name="csrf" value="{esc(csrf)}">{"".join(sections)}<div class="save-bar"><button>Save all settings</button></div></form>'
    return page("Settings", body, csrf, request.query.get("message", ""))


async def buttons(request: web.Request):
    csrf = token(request)
    data = read_config()
    english = {
        "buy": "Buy service", "renew": "Renew service", "services": "My services",
        "trial": "Trial service", "support": "Support", "channel": "Channel",
    }
    fields = []
    for name, persian in MENU_BUTTONS:
        key = MENU_SETTING_KEYS[name]
        enabled = data[key] == "true"
        fields.append(
            f'<label>{esc(english[name])} — {esc(persian)}'
            f'<select name="{key}"><option value="true" {"selected" if enabled else ""}>Enabled</option>'
            f'<option value="false" {"selected" if not enabled else ""}>Disabled</option></select></label>'
        )
    body = (
        '<p class="muted">Choose which buttons users see in the bot. Disabled buttons and their entry points stop accepting new requests; existing orders can still be completed.</p>'
        '<p class="muted">The Channel button is hidden whenever the channel membership requirement is disabled in Settings. Hiding the Channel button alone does not turn off that requirement.</p>'
        f'<form method="post" action="/buttons"><input type="hidden" name="csrf" value="{esc(csrf)}">'
        f'<section class="card setting-group"><h2>Bot buttons</h2><div class="setting-grid">{"".join(fields)}</div></section>'
        '<div class="save-bar"><button>Save bot buttons</button></div></form>'
    )
    return page("Bot buttons", body, csrf, request.query.get("message", ""))


async def save_buttons(request: web.Request):
    csrf = token(request)
    posted = await form(request, csrf)
    values = {key: str(posted.get(key, "")) for key in MENU_SETTING_KEYS.values()}
    if any(value not in {"true", "false"} for value in values.values()):
        raise web.HTTPBadRequest(text="Invalid bot button setting")
    data = read_config()
    data.update(values)
    write_config(data)
    with database() as db:
        audit(db, "panel.buttons_saved", {"enabled": [name for name, key in MENU_SETTING_KEYS.items() if values[key] == "true"]})
    asyncio.create_task(restart_bot())
    raise web.HTTPSeeOther("/buttons?message=" + quote("Bot buttons saved; bot restart requested"))


async def restart_bot():
    await asyncio.sleep(0.4)
    pid_path = Path(read_config()["DB_PATH"]).parent / "bot.pid"
    try:
        pid = int(pid_path.read_text(encoding="ascii"))
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        if b"damavand_bot.launch" in cmdline and b"bot" in cmdline:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass


async def reveal_setting(request: web.Request):
    csrf = token(request)
    posted = await form(request, csrf)
    key = str(posted.get("key", ""))
    if key not in SECRET_SETTINGS:
        raise web.HTTPBadRequest(text="Unknown secret setting")
    value = read_config().get(key, "")
    with database() as db:
        audit(db, "panel.secret_revealed", {"key": key})
    return web.json_response({"value": value}, headers={
        "Cache-Control": "no-store", "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
    })


async def save_settings(request: web.Request):
    csrf = token(request)
    posted = await form(request, csrf)
    data = read_config()
    for key in SETTINGS:
        if key not in posted:
            continue
        value = str(posted.get(key, "")).strip()
        if key in SECRET_SETTINGS and not value:
            continue
        data[key] = value
    try:
        validate_settings(data)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc))
    write_config(data)
    with database() as db:
        audit(db, "panel.settings_saved", {"keys": [key for key in SETTINGS if key not in SECRET_SETTINGS]})
    asyncio.create_task(restart_bot())
    raise web.HTTPSeeOther("/settings?message=" + quote("Settings saved; bot restart requested"))


async def terms(request: web.Request):
    csrf = token(request)
    path = Path(read_config()["TERMS_FILE"])
    body = path.read_text(encoding="utf-8") if path.exists() else ""
    return page("Terms and conditions", f'<div class="card"><p>Saving changes the version and asks users to accept again.</p><form method="post" action="/terms"><input type="hidden" name="csrf" value="{esc(csrf)}"><textarea name="body" style="min-height:350px" maxlength="4000" required>{esc(body)}</textarea><p><button>Save terms</button></p></form></div>', csrf, request.query.get("message", ""))


async def save_terms(request: web.Request):
    csrf = token(request)
    posted = await form(request, csrf)
    body = str(posted.get("body", "")).strip()
    if not body or len(body) > 4000:
        raise web.HTTPBadRequest(text="Terms must be 1–4000 characters")
    data = read_config()
    path = Path(data["TERMS_FILE"])
    if path.parent != CONFIG_DIR or path.name != "terms-fa.txt":
        raise web.HTTPForbidden(text="Invalid terms path")
    temp = CONFIG_DIR / (".terms-" + secrets.token_hex(8))
    try:
        temp.write_text(body + "\n", encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    data["TERMS_VERSION"] = str(time.time_ns())
    write_config(data)
    with database() as db:
        audit(db, "panel.terms_saved", {"version": data["TERMS_VERSION"]})
    asyncio.create_task(restart_bot())
    raise web.HTTPSeeOther("/terms?message=" + quote("Terms saved; users must accept again"))


def make_app() -> web.Application:
    if not AUTH_PATH.exists():
        raise RuntimeError("Panel password not configured")
    app = web.Application(middlewares=[guard], client_max_size=64 * 1024)
    app.router.add_static("/static/", Path(__file__).parent / "static", show_index=False)
    app.router.add_get("/login", login)
    app.router.add_post("/login", login)
    app.router.add_post("/logout", logout)
    app.router.add_post("/settings/reveal", reveal_setting)
    for path, handler in (("/", overview), ("/users", users), ("/services", manual_services), ("/orders", orders), ("/plans", plans), ("/discounts", discounts), ("/settings", settings), ("/buttons", buttons), ("/terms", terms), ("/audit", audit_log)):
        app.router.add_get(path, handler)
    for path, handler in (("/plans", save_plan), ("/discounts", save_discount), ("/services", save_manual_service), ("/settings", save_settings), ("/buttons", save_buttons), ("/terms", save_terms)):
        app.router.add_post(path, handler)
    return app


def main() -> None:
    from .db import Database
    asyncio.run(Database(Path(read_config()["DB_PATH"])).initialize())
    port = 8766 if PANEL_DOMAIN_PATH.exists() else 8765
    web.run_app(make_app(), host="127.0.0.1", port=port, access_log=None)


if __name__ == "__main__":
    main()

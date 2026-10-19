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
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from .management import (
    AUTH_PATH, CONFIG_DIR, CONFIG_PATH, PANEL_DOMAIN_PATH,
    SECRET_SETTINGS, SETTINGS,
    read_config, validate_settings, verify_panel_password, write_config,
)

SESSIONS: dict[str, tuple[float, str]] = {}
LOGIN_CHALLENGES: dict[str, tuple[float, str]] = {}
FAILURES: dict[str, list[float]] = {}
COOKIE = "damavand_panel"
LOGIN_COOKIE = "damavand_login"
NULL_ORIGIN_CSRF_PATHS = frozenset({"/login", "/logout", "/plans", "/discounts", "/settings", "/terms"})


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
    nav = "".join(
        f'<a href="{href}">{label}</a>' for href, label in (
            ("/", "Overview"), ("/users", "Users"), ("/orders", "Orders"),
            ("/plans", "Plans"), ("/discounts", "Discounts"),
            ("/settings", "Settings"), ("/terms", "Terms"), ("/audit", "Audit"),
        )
    ) if csrf else ""
    logout = f'<form method="post" action="/logout"><input type="hidden" name="csrf" value="{esc(csrf)}"><button>Sign out</button></form>' if csrf else ""
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Damavand Bot</title>
<style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;font:15px system-ui;background:#0c1424;color:#e5edf7}}
header{{background:#14213a;padding:16px 24px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}}
header b{{font-size:19px}}nav{{display:flex;gap:12px;flex-wrap:wrap}}a{{color:#93c5fd;text-decoration:none}}a:hover{{text-decoration:underline}}
main{{max-width:1180px;margin:28px auto;padding:0 18px}}.card{{background:#17263e;border:1px solid #30435f;border-radius:12px;padding:20px;margin:16px 0}}
h1{{font-size:25px}}h2{{font-size:19px}}label{{display:block;margin:12px 0 5px}}input,textarea,select{{background:#0d1a2d;border:1px solid #526782;color:#f8fafc;border-radius:7px;padding:9px;width:100%;max-width:640px;font:inherit}}
textarea{{min-height:160px}}button{{background:#2563eb;color:white;border:0;border-radius:7px;padding:9px 15px;cursor:pointer;font:inherit}}button.danger{{background:#b91c1c}}
table{{border-collapse:collapse;width:100%;overflow-wrap:anywhere}}th,td{{border-bottom:1px solid #344762;text-align:left;padding:9px}}.scroll{{overflow:auto}}
.muted{{color:#a5b4c7}}.notice{{background:#1b4332;border-radius:8px;padding:12px}}.error{{background:#6d2020;border-radius:8px;padding:12px}}.row{{display:flex;gap:12px;flex-wrap:wrap;align-items:end}}
.row>label{{flex:1 1 150px}}code{{font-size:13px}}small{{color:#afbdd0}}
</style></head><body><header><b>Damavand Bot</b><nav>{nav}</nav>{logout}</header>
<main><h1>{esc(title)}</h1>{f'<p class="notice">{esc(message)}</p>' if message else ''}{content}</main></body></html>"""
    return web.Response(text=body, content_type="text/html", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
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
    return page("Overview", f'<p class="muted">Local-only panel. Payment receipts must be verified against your bank account before approval in Telegram.</p><div class="row">{cards}</div><div class="card scroll"><h2>Recent orders</h2><table><tr><th>ID</th><th>User</th><th>Kind</th><th>Toman</th><th>Status</th><th>Created</th></tr>{rows}</table></div>', csrf)


async def users(request: web.Request):
    csrf = token(request)
    with database() as db:
        rows = db.execute("SELECT telegram_id,username,full_name,terms_version,created_at FROM users ORDER BY updated_at DESC LIMIT 200").fetchall()
    body = "".join(f"<tr><td>{r['telegram_id']}</td><td>{esc(r['username'])}</td><td>{esc(r['full_name'])}</td><td>{esc(r['terms_version'])}</td><td>{esc(r['created_at'])}</td></tr>" for r in rows)
    return page("Users", f'<div class="card scroll"><p>Latest 200 users</p><table><tr><th>Telegram ID</th><th>Username</th><th>Name</th><th>Terms</th><th>Joined</th></tr>{body}</table></div>', csrf)


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
    forms += f'<div class="card"><h2>Add plan</h2><form method="post" action="/plans"><input type="hidden" name="csrf" value="{esc(csrf)}"><label>Code<input name="code" pattern="[a-z0-9_-]{{2,32}}" required></label><label>Persian title<input name="title_fa" maxlength="100" required></label><div class="row">' + "".join(f'<label>{label}<input name="{key}" type="number" min="{minimum}" required></label>' for key,label,minimum in (("traffic_gb","Traffic GB",1),("duration_days","Days",1),("toman_price","Toman",0),("stars_price","Stars",0))) + '</div><button>Add plan</button></form></div>'
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
        rows = db.execute("SELECT code,percent,active,created_at FROM discount_codes ORDER BY created_at DESC").fetchall()
    body = "".join(f'<tr><td>{esc(r["code"])}</td><td>{r["percent"]}%</td><td>{"Active" if r["active"] else "Disabled"}</td><td>{esc(r["created_at"])}</td><td><form method="post" action="/discounts"><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="code" value="{esc(r["code"])}"><input type="hidden" name="action" value="toggle"><button>{"Disable" if r["active"] else "Enable"}</button></form></td></tr>' for r in rows)
    return page("Discounts", f'<div class="card"><form method="post" action="/discounts"><input type="hidden" name="csrf" value="{esc(csrf)}"><label>New code (CODE-35 also accepted)<input name="code" required></label><label>Percentage<input name="percent" type="number" min="1" max="100" required></label><button>Save discount</button></form></div><div class="card scroll"><table><tr><th>Code</th><th>Percent</th><th>Status</th><th>Created</th><th></th></tr>{body}</table></div>', csrf, request.query.get("message", ""))


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
            db.execute("INSERT INTO discount_codes(code,percent,active,created_by,created_at) VALUES(?,?,1,0,datetime('now')) ON CONFLICT(code) DO UPDATE SET percent=excluded.percent,active=1", (code, percent))
            audit(db, "panel.discount_saved", {"code": code, "percent": percent})
    raise web.HTTPSeeOther("/discounts?message=" + quote("Discount updated"))


async def settings(request: web.Request):
    csrf = token(request)
    data = read_config()
    fields = ""
    for key in SETTINGS:
        if key in {"XUI_VERIFY_SSL", "DEMO_MODE", "CARD_PAYMENT_ENABLED", "TRIAL_ENABLED"}:
            field = f'<select name="{key}"><option value="true" {"selected" if data.get(key)=="true" else ""}>Enabled</option><option value="false" {"selected" if data.get(key)!="true" else ""}>Disabled</option></select>'
        elif key == "CARD_PAYMENT_INSTRUCTIONS":
            field = f'<textarea name="{key}" maxlength="4096">{esc(data.get(key,""))}</textarea>'
        else:
            value = "" if key in SECRET_SETTINGS else esc(data.get(key, ""))
            placeholder = "Leave blank to keep current" if key in SECRET_SETTINGS else ""
            field = f'<input name="{key}" value="{value}" placeholder="{placeholder}" {"type=password" if key in SECRET_SETTINGS else ""} maxlength="4096">'
        fields += f'<label>{esc(key)}{field}</label>'
    intro = "First-run setup: enter the Telegram bot token, at least one admin ID, channel, support username, and 3x-ui connection. No bot token is needed in the installer." if not CONFIG_PATH.exists() else "Saving restarts the bot. Secrets are never displayed. The panel remains available if the bot configuration fails."
    return page("Bot settings", f'<div class="card"><p class="muted">{esc(intro)}</p><form method="post" action="/settings"><input type="hidden" name="csrf" value="{esc(csrf)}">{fields}<p><button>Save and restart bot</button></p></form></div>', csrf, request.query.get("message", ""))


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


async def save_settings(request: web.Request):
    csrf = token(request)
    posted = await form(request, csrf)
    data = read_config()
    for key in SETTINGS:
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
    app.router.add_get("/login", login)
    app.router.add_post("/login", login)
    app.router.add_post("/logout", logout)
    for path, handler in (("/", overview), ("/users", users), ("/orders", orders), ("/plans", plans), ("/discounts", discounts), ("/settings", settings), ("/terms", terms), ("/audit", audit_log)):
        app.router.add_get(path, handler)
    for path, handler in (("/plans", save_plan), ("/discounts", save_discount), ("/settings", save_settings), ("/terms", save_terms)):
        app.router.add_post(path, handler)
    return app


def main() -> None:
    from .db import Database
    asyncio.run(Database(Path(read_config()["DB_PATH"])).initialize())
    port = 8766 if PANEL_DOMAIN_PATH.exists() else 8765
    web.run_app(make_app(), host="127.0.0.1", port=port, access_log=None)


if __name__ == "__main__":
    main()

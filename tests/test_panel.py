import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from damavand_bot import management, panel
from damavand_bot.db import Database


class PanelTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config_patch = patch.object(management, "CONFIG_PATH", root / "config.json")
        self.panel_config_patch = patch.object(panel, "CONFIG_PATH", root / "config.json")
        self.defaults_patch = patch.object(management, "DEFAULTS", {**management.DEFAULTS, "DB_PATH": str(root / "bot.db"), "TERMS_FILE": str(root / "terms-fa.txt")})
        self.auth_patch = patch.object(management, "AUTH_PATH", root / "panel-auth.json")
        self.panel_auth_patch = patch.object(panel, "AUTH_PATH", root / "panel-auth.json")
        self.panel_domain_patch = patch.object(panel, "PANEL_DOMAIN_PATH", root / "panel-domain")
        for item in (self.config_patch, self.panel_config_patch, self.defaults_patch, self.auth_patch, self.panel_auth_patch, self.panel_domain_patch):
            item.start()
            self.addCleanup(item.stop)
        data = dict(management.DEFAULTS)
        data["DB_PATH"] = str(root / "bot.db")
        management.write_config(data)
        management.set_panel_password("a-very-long-panel-password")
        await Database(root / "bot.db").initialize()
        self.client = TestClient(TestServer(panel.make_app()), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.headers = {"Host": "127.0.0.1:8765"}

    async def login_form(self, headers):
        response = await self.client.get("/login", headers=headers)
        self.assertEqual(200, response.status)
        sid = response.cookies[panel.LOGIN_COOKIE].value
        return {**headers, "Cookie": f"{panel.LOGIN_COOKIE}={sid}"}, panel.LOGIN_CHALLENGES[sid][1]

    async def test_auth_csrf_and_plan_edit(self):
        response = await self.client.get("/", headers=self.headers, allow_redirects=False)
        self.assertEqual(303, response.status)
        headers, login_csrf = await self.login_form(self.headers)
        response = await self.client.post("/login", headers=headers, data={"password": "wrong", "login_csrf": login_csrf}, allow_redirects=False)
        self.assertEqual(200, response.status)
        headers, login_csrf = await self.login_form(self.headers)
        response = await self.client.post("/login", headers=headers, data={"password": "a-very-long-panel-password", "login_csrf": login_csrf}, allow_redirects=False)
        self.assertEqual(303, response.status)
        sid = response.cookies[panel.COOKIE].value
        csrf = panel.SESSIONS[sid][1]
        response = await self.client.get("/plans", headers=self.headers)
        self.assertEqual(200, response.status)
        response = await self.client.post("/plans", headers={**self.headers, "Origin": "null"}, data={"csrf": "wrong"}, allow_redirects=False)
        self.assertEqual(403, response.status)
        response = await self.client.post("/plans", headers={**self.headers, "Origin": "null"}, allow_redirects=False, data={
            "csrf": csrf, "code": "gb10", "title_fa": "Test plan", "traffic_gb": "12",
            "duration_days": "45", "toman_price": "123456", "stars_price": "0", "enabled": "0",
        })
        self.assertEqual(303, response.status)
        with panel.database() as db:
            plan = db.execute("SELECT title_fa,traffic_gb,enabled FROM plans WHERE code='gb10'").fetchone()
        self.assertEqual(("Test plan", 12, 0), tuple(plan))

    async def test_host_guard_and_secret_not_rendered(self):
        response = await self.client.get("/login", headers={"Host": "malicious.example"})
        self.assertEqual(403, response.status)
        data = management.read_config()
        data["BOT_TOKEN"] = "123456789:" + "A" * 35
        management.write_config(data)
        headers, login_csrf = await self.login_form(self.headers)
        response = await self.client.post("/login", headers=headers, data={"password": "a-very-long-panel-password", "login_csrf": login_csrf}, allow_redirects=False)
        self.assertEqual(303, response.status)
        response = await self.client.get("/settings", headers=self.headers)
        self.assertEqual(200, response.status)
        self.assertNotIn(data["BOT_TOKEN"], await response.text())

    async def test_public_https_origin_is_checked(self):
        panel.PANEL_DOMAIN_PATH.write_text("bot.example.com", encoding="ascii")
        headers = {
            "Host": "bot.example.com:8765",
            "X-Forwarded-Proto": "https",
            "Origin": "https://bot.example.com:8765",
        }
        headers, login_csrf = await self.login_form(headers)
        response = await self.client.post("/login", headers=headers, data={"password": "wrong", "login_csrf": login_csrf}, allow_redirects=False)
        self.assertEqual(200, response.status)
        response = await self.client.post("/login", headers={**headers, "Origin": "https://attacker.example"}, data={"password": "wrong", "login_csrf": login_csrf}, allow_redirects=False)
        self.assertEqual(403, response.status)
        response = await self.client.post("/login", headers={**headers, "Origin": "null"}, data={"password": "wrong", "login_csrf": login_csrf}, allow_redirects=False)
        self.assertEqual(200, response.status)
        response = await self.client.post("/login", headers={**headers, "Origin": "null"}, data={"password": "wrong"}, allow_redirects=False)
        self.assertEqual(403, response.status)

    async def test_first_run_settings_are_entered_in_panel(self):
        management.CONFIG_PATH.unlink()
        headers, login_csrf = await self.login_form(self.headers)
        response = await self.client.post("/login", headers=headers, data={"password": "a-very-long-panel-password", "login_csrf": login_csrf}, allow_redirects=False)
        self.assertEqual(303, response.status)
        sid = response.cookies[panel.COOKIE].value
        csrf = panel.SESSIONS[sid][1]
        response = await self.client.get("/", headers=self.headers)
        self.assertIn("Finish setup", await response.text())
        data = {key: value for key, value in management.DEFAULTS.items() if key in management.SETTINGS}
        data.update({
            "csrf": csrf,
            "BOT_TOKEN": "123456789:" + "A" * 35,
            "ADMIN_IDS": "123456789",
            "CHANNEL_USERNAME": "@publicchannel",
            "SUPPORT_USERNAME": "@supportuser",
            "XUI_BASE_URL": "https://127.0.0.1:2053/panel",
            "XUI_API_TOKEN": "secret",
            "XUI_SUBSCRIPTION_BASE": "https://example.com/sub",
        })
        response = await self.client.post("/settings", headers=self.headers, data=data, allow_redirects=False)
        self.assertEqual(303, response.status)
        self.assertEqual(data["BOT_TOKEN"], management.read_config()["BOT_TOKEN"])

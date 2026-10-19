import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from damavand_bot import management


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config_patch = patch.object(management, "CONFIG_PATH", root / "config.json")
        self.auth_patch = patch.object(management, "AUTH_PATH", root / "panel-auth.json")
        self.config_patch.start()
        self.auth_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.addCleanup(self.auth_patch.stop)

    def test_private_config_round_trip(self):
        config = dict(management.DEFAULTS)
        config.update({
            "BOT_TOKEN": "123456789:" + "A" * 35,
            "ADMIN_IDS": "123456789",
            "CHANNEL_USERNAME": "@publicchannel",
            "SUPPORT_USERNAME": "@supportuser",
            "XUI_BASE_URL": "https://127.0.0.1:2053/panel",
            "XUI_API_TOKEN": "secret",
            "XUI_SUBSCRIPTION_BASE": "https://example.com/sub",
        })
        management.validate_settings(config)
        management.write_config(config)
        self.assertEqual(config, management.read_config())
        if os.name != "nt":
            self.assertEqual(0o600, management.CONFIG_PATH.stat().st_mode & 0o777)

    def test_remote_http_is_rejected(self):
        config = dict(management.DEFAULTS)
        config.update({
            "BOT_TOKEN": "123456789:" + "A" * 35,
            "ADMIN_IDS": "123456789",
            "CHANNEL_USERNAME": "@publicchannel",
            "SUPPORT_USERNAME": "@supportuser",
            "XUI_BASE_URL": "http://example.com/panel",
            "XUI_API_TOKEN": "secret",
            "XUI_SUBSCRIPTION_BASE": "https://example.com/sub",
        })
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            management.validate_settings(config)

    def test_private_channel_id_and_join_link(self):
        config = dict(management.DEFAULTS)
        config.update({
            "BOT_TOKEN": "123456789:" + "A" * 35,
            "ADMIN_IDS": "123456789",
            "CHANNEL_ID": "-1001234567890",
            "CHANNEL_JOIN_URL": "https://t.me/+ExampleInvite",
            "SUPPORT_USERNAME": "@supportuser",
            "XUI_BASE_URL": "https://127.0.0.1:2053/panel",
            "XUI_API_TOKEN": "secret",
            "XUI_SUBSCRIPTION_BASE": "https://example.com/sub",
        })
        management.validate_settings(config)
        config["CHANNEL_JOIN_URL"] = "https://evil.example/invite"
        with self.assertRaisesRegex(ValueError, "Telegram channel link"):
            management.validate_settings(config)

    def test_password_hash_and_check(self):
        management.set_panel_password("a-very-long-panel-password")
        record = json.loads(management.AUTH_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("a-very-long-panel-password", str(record))
        self.assertTrue(management.verify_panel_password("a-very-long-panel-password"))
        self.assertFalse(management.verify_panel_password("wrong-password"))

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from damavand_bot import management, setup


class SetupTests(unittest.TestCase):
    def test_generate_password_without_prompt(self):
        with TemporaryDirectory() as directory:
            with patch.object(management, "AUTH_PATH", Path(directory) / "panel-auth.json"):
                with patch("sys.argv", ["setup", "generate"]), patch("getpass.getpass") as prompt, patch("builtins.print") as output:
                    setup.main()
                prompt.assert_not_called()
                password = output.call_args.args[0]
                self.assertGreaterEqual(len(password), 16)
                self.assertTrue(management.verify_panel_password(password))

    def test_installer_only_prompts_for_panel_password(self):
        with TemporaryDirectory() as directory:
            with patch.object(management, "AUTH_PATH", Path(directory) / "panel-auth.json"):
                with patch("sys.argv", ["setup", "password"]), patch("getpass.getpass", side_effect=["long-panel-password", "long-panel-password"]) as prompt:
                    setup.main()
                self.assertEqual(2, prompt.call_count)
                self.assertTrue(management.verify_panel_password("long-panel-password"))

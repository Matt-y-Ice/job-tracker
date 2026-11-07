import unittest
from unittest.mock import patch

from src.platform_text import dashboard_hint, ollama_not_running, setup_hint


class PlatformTextTests(unittest.TestCase):
    def test_windows_strings_stay_exactly_as_they_are(self):
        with patch("src.platform_text.sys.platform", "win32"):
            self.assertEqual(setup_hint(), "setup.bat")
            self.assertEqual(dashboard_hint(), "double-click dashboard.bat")
            self.assertEqual(
                ollama_not_running(),
                "Ollama is not running. Open the Ollama app from the Start menu "
                "(or run 'ollama serve'), then try again.",
            )

    def test_mac_strings_name_the_mac_launcher(self):
        with patch("src.platform_text.sys.platform", "darwin"):
            self.assertEqual(setup_hint(), "setup.command")
            self.assertEqual(dashboard_hint(), "double-click dashboard.command")
            self.assertIn("Ollama", ollama_not_running())
            self.assertNotIn("Start menu", ollama_not_running())
            self.assertIn("ollama serve", ollama_not_running())

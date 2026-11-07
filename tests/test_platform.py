import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from src.platform_text import dashboard_hint, ollama_not_running, run_hint, setup_hint


class PlatformTextTests(unittest.TestCase):
    def test_windows_strings_stay_exactly_as_they_are(self):
        with patch("src.platform_text.sys.platform", "win32"):
            self.assertEqual(setup_hint(), "setup.bat")
            self.assertEqual(run_hint(), "run.bat")
            self.assertEqual(dashboard_hint(), "double-click dashboard.bat")
            self.assertEqual(
                ollama_not_running(),
                "Ollama is not running. Open the Ollama app from the Start menu "
                "(or run 'ollama serve'), then try again.",
            )

    def test_mac_strings_name_the_mac_launcher(self):
        with patch("src.platform_text.sys.platform", "darwin"):
            self.assertEqual(setup_hint(), "setup.command")
            self.assertEqual(run_hint(), "run.command")
            self.assertEqual(dashboard_hint(), "double-click dashboard.command")
            self.assertIn("Ollama", ollama_not_running())
            self.assertNotIn("Start menu", ollama_not_running())
            self.assertIn("ollama serve", ollama_not_running())

    def test_windows_model_missing_sentence_keeps_two_spaces(self):
        with patch("src.platform_text.sys.platform", "win32"):
            model = "qwen2.5:14b"
            sentence = f"The model '{model}' is not downloaded. Run {setup_hint()}, or run:  ollama pull {model}"
            self.assertEqual(
                sentence,
                "The model 'qwen2.5:14b' is not downloaded. Run setup.bat, or run:  ollama pull qwen2.5:14b",
            )

    def test_production_windows_copy_still_names_bats(self):
        root = Path(__file__).resolve().parents[1]
        ollama = (root / "src" / "ollama_client.py").read_text(encoding="utf-8")
        self.assertIn("from .platform_text import ollama_not_running, setup_hint", ollama)
        self.assertNotIn("Start menu", ollama)
        text = (root / "src" / "platform_text.py").read_text(encoding="utf-8")
        self.assertIn("Open the Ollama app from the Start menu", text)
        self.assertIn('return "setup.bat"', text)
        pipeline = (root / "src" / "pipeline.py").read_text(encoding="utf-8")
        self.assertIn("dashboard_hint()", pipeline)
        self.assertNotIn("double-click dashboard.bat", pipeline)
        system = (root / "src" / "system_check.py").read_text(encoding="utf-8")
        self.assertIn("setup_hint()", system)
        self.assertNotIn("Run setup.bat again", system)
        questionnaire = (root / "src" / "questionnaire.py").read_text(encoding="utf-8")
        self.assertIn("run_hint()", questionnaire)
        self.assertNotIn("run.bat", questionnaire)
        generator = (root / "src" / "documents" / "generator.py").read_text(encoding="utf-8")
        self.assertIn("setup_hint()", generator)
        self.assertNotIn("setup.bat", generator)


class AppleSiliconTests(unittest.TestCase):
    def test_apple_silicon_reports_unified_memory(self):
        from src.system_check import detect_gpus, documents_model_fits
        with patch("src.system_check.sys.platform", "darwin"), \
             patch("src.system_check.os.uname") as uname, \
             patch("src.system_check.subprocess.run") as run, \
             patch("src.system_check.windows_gpus", return_value=[]), \
             patch("src.system_check.nvidia_gpus", return_value=[]):
            uname.return_value.machine = "arm64"
            run.return_value.returncode = 0
            run.return_value.stdout = str(16 * 1024**3)
            gpus = detect_gpus()
            self.assertEqual(gpus[0].name, "Apple Silicon")
            self.assertEqual(gpus[0].vram_gb, 16.0)
            self.assertFalse(documents_model_fits("qwen2.5:32b"))


class OllamaLookupTests(unittest.TestCase):
    def test_find_ollama_windows_path_unchanged(self):
        from src import system_check as sc
        with patch.object(sc.shutil, "which", return_value=None), \
             patch.object(sc.sys, "platform", "win32"), \
             patch.object(sc.os, "environ", {"LOCALAPPDATA": r"C:\Users\Taylor\AppData\Local"}), \
             patch.object(sc.Path, "exists", return_value=True):
            found = sc.find_ollama()
            self.assertTrue(found.replace("/", "\\").endswith(
                r"AppData\Local\Programs\Ollama\ollama.exe"))

    def test_find_ollama_mac_app_bundle(self):
        from src import system_check as sc

        def exists(self):
            return str(self) == "/Applications/Ollama.app/Contents/Resources/ollama"

        with patch.object(sc.shutil, "which", return_value=None), \
             patch.object(sc.sys, "platform", "darwin"), \
             patch.object(sc.Path, "exists", exists):
            self.assertEqual(sc.find_ollama(),
                             "/Applications/Ollama.app/Contents/Resources/ollama")

    def test_start_ollama_mac_opens_the_app(self):
        from src import system_check as sc

        def exists(self):
            return str(self) == "/Applications/Ollama.app"

        with patch.object(sc.sys, "platform", "darwin"), \
             patch.object(sc.Path, "exists", exists), \
             patch.object(sc.subprocess, "Popen") as popen, \
             patch.object(sc, "ollama_running", return_value=True):
            self.assertTrue(sc.start_ollama("/Applications/Ollama.app/Contents/Resources/ollama",
                                            "http://127.0.0.1:11434"))
            self.assertEqual(popen.call_args.args[0], ["open", "-a", "Ollama"])
            self.assertNotIn("creationflags", popen.call_args.kwargs)


class PdfTests(unittest.TestCase):
    def test_windows_pdf_command_is_unchanged(self):
        from src.documents import writer
        if not hasattr(writer.subprocess, "CREATE_NO_WINDOW"):
            writer.subprocess.CREATE_NO_WINDOW = 0x08000000
        with patch.object(writer.sys, "platform", "win32"), \
             patch.object(writer.subprocess, "run") as run, \
             patch.object(writer.Path, "exists", return_value=True):
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            writer.to_pdf([Path("a.docx"), Path("b.docx")])
            argv = run.call_args.args[0]
            self.assertEqual(argv[:6], ["powershell", "-NoProfile", "-NonInteractive",
                                        "-ExecutionPolicy", "Bypass", "-File"])
            self.assertTrue(str(argv[6]).endswith("docx_to_pdf.ps1"))
            self.assertEqual(run.call_args.kwargs["creationflags"],
                             writer.subprocess.CREATE_NO_WINDOW)

    def test_mac_pdf_does_not_call_powershell(self):
        from src.documents import writer
        with patch.object(writer.sys, "platform", "darwin"), \
             patch.object(writer, "_pdf_via_word_mac", side_effect=writer.PdfUnavailable("no word")), \
             patch.object(writer, "_pdf_via_libreoffice", side_effect=writer.PdfUnavailable("no office")), \
             patch.object(writer.subprocess, "run") as run:
            with self.assertRaises(writer.PdfUnavailable):
                writer.to_pdf([Path("a.docx")])
            run.assert_not_called()


class ExcelLockTests(unittest.TestCase):
    def _workbook(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        path = folder / "Job_Rankings.xlsx"
        wb = Workbook()
        wb.active["A1"] = "keep"
        wb.save(path)
        return wb, path

    def test_mac_lock_file_raises_workbook_locked(self):
        from src.excel_builder import WorkbookLocked, save
        wb, path = self._workbook()
        path.with_name(f"~${path.name}").write_bytes(b"lock")
        with patch("src.excel_builder.sys.platform", "darwin"):
            with self.assertRaisesRegex(WorkbookLocked, "Close the workbook in Excel"):
                save(wb, path)
        self.assertTrue(path.exists())
        self.assertFalse(path.with_name(f"~tmp_{path.name}").exists())

    def test_windows_ignores_mac_lock_file(self):
        from src.excel_builder import save
        wb, path = self._workbook()
        path.with_name(f"~${path.name}").write_bytes(b"lock")
        with patch("src.excel_builder.sys.platform", "win32"):
            save(wb, path)
        from openpyxl import load_workbook
        self.assertEqual(load_workbook(path).active["A1"].value, "keep")


class DashboardPortTests(unittest.TestCase):
    def test_mac_skips_a_busy_default_port(self):
        from src.dashboard.__main__ import choose_port
        busy = {5000}
        port = choose_port(5000, user_set_port=False, platform="darwin",
                           free=lambda p: p not in busy)
        self.assertEqual(port, 5050)

    def test_windows_does_not_skip(self):
        from src.dashboard.__main__ import choose_port
        port = choose_port(5000, user_set_port=False, platform="win32",
                           free=lambda p: p != 5000)
        self.assertEqual(port, 5000)

    def test_explicit_port_is_kept(self):
        from src.dashboard.__main__ import choose_port
        self.assertEqual(choose_port(5000, True, "darwin", lambda p: False), 5000)


class CommandFileTests(unittest.TestCase):
    def test_command_files_exist_and_are_executable(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("setup.command", "run.command", "dashboard.command", "scrub.command"):
            path = root / name
            self.assertTrue(path.is_file(), name)
            self.assertFalse(b"\r\n" in path.read_bytes(), name)
            self.assertTrue(path.stat().st_mode & 0o111, name)
            text = path.read_text(encoding="utf-8")
            self.assertIn('cd "$(dirname "$0")"', text)
            self.assertNotIn("rm -rf /", text)
            self.assertNotIn("rm -rf $HOME", text)

    def test_scrub_command_requires_typed_word_and_stays_in_repo(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scrub.command").read_text(encoding="utf-8")
        for token in ("config/preferences.json", "config/api_keys.json",
                      "config/skill_catalog.json", "SCRUB", "run.py", "src/pipeline.py"):
            self.assertIn(token, text)
        self.assertNotIn("rm -rf $HOME", text)
        self.assertNotIn("rm -rf /", text)
        self.assertNotIn("powershell", text.lower())

    def test_bats_were_not_rewritten(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("setup.bat", "run.bat", "dashboard.bat", "scrub.bat"):
            self.assertTrue((root / name).is_file())


class AppLauncherTests(unittest.TestCase):
    def test_app_only_opens_command_files(self):
        root = Path(__file__).resolve().parents[1]
        plist = (root / "Job Tracker.app" / "Contents" / "Info.plist").read_text(encoding="utf-8")
        self.assertIn("<key>CFBundleExecutable</key>", plist)
        self.assertIn("<string>launcher</string>", plist)
        self.assertIn("<string>local.jobtracker.launcher</string>", plist)
        launcher = root / "Job Tracker.app" / "Contents" / "MacOS" / "launcher"
        self.assertTrue(launcher.stat().st_mode & 0o111)
        text = launcher.read_text(encoding="utf-8")
        self.assertIn('cd "$(dirname "$0")/../../.."', text)
        for name in ("setup.command", "run.command", "dashboard.command", "scrub.command"):
            self.assertIn(name, text)
        self.assertNotIn("pip", text)
        self.assertNotIn("rm ", text)


class ReadmeTests(unittest.TestCase):
    def test_readme_keeps_windows_and_adds_mac(self):
        text = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertIn("setup.bat", text)
        self.assertIn("setup.command", text)
        self.assertIn("Job Tracker.app", text)
        self.assertIn("AirPlay", text)
        self.assertIn("run.bat", text)
        self.assertIn("dashboard.bat", text)

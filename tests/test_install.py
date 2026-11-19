"""Exercise installation in temporary directories without touching user services."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def test_isolated_install_preserves_existing_data_and_backs_up_code(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            prefix, bin_dir, unit_dir = base / "app with spaces", base / "bin", base / "units"
            (prefix / ".venv/bin").mkdir(parents=True)
            (prefix / ".venv/bin/python").symlink_to(sys.executable)
            (prefix / "state.json").write_text('{"sent": ["already-delivered"]}')
            (prefix / "raindrop_kindle.py").write_text("# Previous installed version\n")
            result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--prefix", str(prefix),
                                     "--bin-dir", str(bin_dir), "--unit-dir", str(unit_dir),
                                     "--skip-dependencies", "--skip-systemd"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((prefix / "state.json").read_text(), '{"sent": ["already-delivered"]}')
            for name in ["ai_client.py", "raindrop_kindle.py", "youtube_kindle.py", "requirements.txt"]:
                self.assertEqual((prefix / name).read_bytes(), (ROOT / name).read_bytes())
            backups = list((prefix / "backups").glob("*/00-raindrop_kindle.py"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "# Previous installed version\n")
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            for app in ["raindrop-kindle", "youtube-kindle"]:
                help_result = subprocess.run([str(bin_dir / app), "--help"], capture_output=True, text=True)
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                service = (unit_dir / f"{app}.service").read_text()
                self.assertIn(f'ExecStart="{prefix}/.venv/bin/python"', service)
                self.assertTrue((unit_dir / f"{app}.timer").is_file())

    def test_timer_activation_requires_systemd(self):
        result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--skip-systemd", "--enable-timers"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot be combined", result.stderr)

    def test_source_checkout_is_not_an_installation_target(self):
        result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--prefix", str(ROOT),
                                 "--skip-systemd", "--skip-dependencies"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("outside the source checkout", result.stderr)


if __name__ == "__main__":
    unittest.main()

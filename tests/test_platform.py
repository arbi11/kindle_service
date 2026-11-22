"""Run these on every native build host: locks protect the reading database."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from process_lock import acquire, release

ROOT = Path(__file__).resolve().parents[1]


class ProcessLockTests(unittest.TestCase):
    def test_another_process_cannot_acquire_until_the_holder_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'profile with spaces.lock'
            script = 'from process_lock import acquire;import sys;f=open(sys.argv[1],"a+");acquire(f)'
            with path.open('a+') as stream:
                acquire(stream)
                blocked = subprocess.run([sys.executable, '-c', script, str(path)], cwd=ROOT, capture_output=True)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn(b'BlockingIOError', blocked.stderr)
                release(stream)
                available = subprocess.run([sys.executable, '-c', script, str(path)], cwd=ROOT, capture_output=True)
                self.assertEqual(available.returncode, 0, available.stderr)

    def test_closing_the_owner_releases_the_os_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'instance.lock'
            owner = path.open('a+')
            acquire(owner)
            owner.close()
            with path.open('a+') as replacement:
                acquire(replacement)
                release(replacement)


if __name__ == '__main__':
    unittest.main()

"""Install/extract the native release in a temporary location and smoke-test it."""
from __future__ import annotations

from pathlib import Path
import platform
import subprocess
import tempfile

from smoke_desktop import check

ROOT = Path(__file__).resolve().parent.parent


def main():
    system = platform.system()
    with tempfile.TemporaryDirectory(prefix='quib27 installer ') as folder:
        temporary = Path(folder)
        destination = temporary / 'installed app'
        profile = temporary / 'reading profile'
        profile.mkdir()
        sentinel = profile / 'keep-my-library.txt'
        sentinel.write_text('This reading data must survive uninstall.', encoding='utf-8')
        if system == 'Linux':
            package = next((ROOT / 'dist').glob('quib27-*-linux-x64.deb'))
            subprocess.run(['dpkg-deb', '--extract', str(package), str(destination)], check=True)
            check(destination / 'opt/quib27/Quib27', profile)
            assert (destination / 'usr/share/applications/quib27.desktop').is_file()
        elif system == 'Windows':
            package = next((ROOT / 'dist').glob('quib27-*-windows-x64-setup.exe'))
            subprocess.run([str(package), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/DIR=' + str(destination)],
                           check=True, timeout=180)
            check(destination / 'Quib27.exe', profile)
            subprocess.run([str(destination / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], check=True, timeout=180)
            assert not (destination / 'Quib27.exe').exists()
        else:
            arch = 'arm64' if platform.machine() == 'arm64' else 'x64'
            package = next((ROOT / 'dist').glob(f'quib27-*-macos-{arch}.dmg'))
            mount = temporary / 'mounted image'
            mount.mkdir()
            subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(package)], check=True)
            try:
                assert (mount / 'Applications').is_symlink()
                subprocess.run(['ditto', str(mount / 'Quib27.app'), str(destination / 'Quib27.app')], check=True)
            finally:
                subprocess.run(['hdiutil', 'detach', str(mount)], check=True)
            check(destination / 'Quib27.app/Contents/MacOS/Quib27', profile)
        assert sentinel.read_text(encoding='utf-8') == 'This reading data must survive uninstall.'
        print('Native installer verified. Reading data was retained.', flush=True)


if __name__ == '__main__':
    main()

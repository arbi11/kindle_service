#!/usr/bin/env python3
"""Build an explicit-file-list desktop distribution; never bundles user data."""
from __future__ import annotations
import argparse
import hashlib
import json
from importlib import metadata
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from desktop import __version__

ROOT = Path(__file__).resolve().parent


def notices(target):
    folder = target / "licenses"
    folder.mkdir(exist_ok=True)
    lines = [f"Quib27 {__version__} — third-party notices", "",
        "Qt/PySide6 libraries are dynamically linked and remain replaceable in _internal/.",
        "Their LGPL/GPL/commercial alternatives and notices are reproduced below; only",
        "the Qt modules required by the desktop shell are bundled.",
        "Qt and PySide source: https://code.qt.io/qt/ and https://code.qt.io/pyside/pyside-setup.git/",
        "Upstream releases: https://download.qt.io/official_releases/qt/",
        "Qt WebEngine includes Chromium and its third-party notices.",
        "PyInstaller: GPL with a distribution exception for the generated application.",
        "FFmpeg is not bundled. Optional audio transcription uses the user's installation.",
        "EbookLib and wordfreq are not bundled in this desktop application.",
        "vis-network 10.1.5: https://github.com/visjs/vis-network (MIT OR Apache-2.0).",
        "Its unmodified standalone build runs locally. License texts are in licenses/vis-network/.", ""]
    lines += ["Pi agent-core and pi-ai (MIT): https://github.com/earendil-works/pi",
              "Pi and npm dependency notices: _internal/agent/dist/licenses/.",
              "Bundled Node.js runtime notices: _internal/agent/runtime/NODE-LICENSE.txt.", ""]
    graph_vendor = ROOT / "desktop/ui/vendor/vis-network"
    graph_notices = folder / "vis-network"
    graph_notices.mkdir(exist_ok=True)
    for name in ("LICENSE-MIT", "LICENSE-APACHE-2.0", "README.txt"):
        shutil.copy2(graph_vendor / name, graph_notices / name)
    excluded = {"ebooklib", "wordfreq", "locate", "langcodes", "msgpack", "faster-whisper"}
    for distribution in sorted(metadata.distributions(), key=lambda d:d.metadata.get("Name", "").lower()):
        name = distribution.metadata.get("Name", "unknown")
        if name.lower() in excluded or name.lower() in {"pip", "setuptools"}:
            continue
        license_name = distribution.metadata.get("License-Expression") or distribution.metadata.get("License") or "See reproduced license files / upstream project"
        lines += [f"{name} {distribution.version}", "License: " + license_name[:1000]]
        urls = distribution.metadata.get_all("Project-URL") or []
        lines.extend(urls[:3])
        count = 0
        for file in distribution.files or []:
            parts = str(file).lower()
            if not any(term in parts for term in ("license", "copying", "thirdpartynotice", "third_party_notice")):
                continue
            source = Path(distribution.locate_file(file))
            if source.is_file() and source.stat().st_size < 8 * 1024 * 1024:
                destination = folder / name / str(file).replace("../", "_")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                count += 1
        lines += [f"Reproduced notice files: {count}", ""]
    (target / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(lines), encoding="utf-8")


def icons():
    """Render the existing app mark; no downloaded or user-provided assets."""
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PIL import Image
    app = QGuiApplication.instance() or QGuiApplication([])
    folder = ROOT / 'build/icons'
    folder.mkdir(parents=True, exist_ok=True)
    canvas = QImage(1024, 1024, QImage.Format.Format_ARGB32)
    canvas.fill(0)
    painter = QPainter(canvas)
    QSvgRenderer(str(ROOT / 'desktop/ui/icon.svg')).render(painter)
    painter.end()
    canvas.save(str(folder / 'icon.png'))
    image = Image.open(folder / 'icon.png')
    image.save(folder / 'icon.ico', sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    image.save(folder / 'icon.icns')
    image.resize((256, 256)).save(folder / 'icon-256.png')
    return folder


def architecture():
    return {'AMD64':'x64', 'amd64':'x64', 'x86_64':'x64', 'aarch64':'arm64', 'arm64':'arm64'}[platform.machine()]


def checksum(files):
    lines = []
    for path in files:
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        lines.append(digest + '  ' + path.name)
    output = ROOT / 'dist' / f'SHA256SUMS-{platform.system().lower()}-{architecture()}.txt'
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return output


def linux_packages(target, icon_folder):
    archive = ROOT / 'dist' / f'quib27-{__version__}-linux-x64.tar.gz'
    partial = archive.with_name(archive.name + '.partial')
    with tarfile.open(partial, 'w:gz', compresslevel=3) as output:
        output.add(target, arcname='Quib27')
    partial.replace(archive)
    package = ROOT / 'build/debian'
    if package.exists():
        shutil.rmtree(package)
    shutil.copytree(target, package / 'opt/quib27', symlinks=True)
    (package / 'DEBIAN').mkdir()
    size = sum(p.stat().st_size for p in (package / 'opt').rglob('*') if p.is_file() and not p.is_symlink()) // 1024
    control = f"""Package: quib27
Version: {__version__}
Section: news
Priority: optional
Architecture: amd64
Maintainer: Arbaaz Khan <25873155+arbi11@users.noreply.github.com>
Installed-Size: {size}
Homepage: https://github.com/arbi11/kindle_service
Depends: libc6 (>= 2.35), libglib2.0-0, libnss3, libnspr4, libdbus-1-3, libx11-6, libx11-xcb1, libxcb1, libxcb-cursor0, libxkbcommon0, libxkbcommon-x11-0, libfontconfig1, libfreetype6, libegl1, libopengl0, libasound2 | libasound2t64
Suggests: ffmpeg
Description: Quib27 personal reading room
 Collect articles and video summaries, read locally, and deliver editions
 through your own email or Kindle. Free proprietary software.
"""
    (package / 'DEBIAN/control').write_text(control, encoding='utf-8')
    applications = package / 'usr/share/applications'
    applications.mkdir(parents=True)
    (applications / 'quib27.desktop').write_text('[Desktop Entry]\nType=Application\nName=Quib27\nComment=Your personal reading room\nExec=/opt/quib27/Quib27\nIcon=quib27\nTerminal=false\nCategories=Office;News;\nStartupWMClass=Quib27\n', encoding='utf-8')
    icons_dir = package / 'usr/share/icons/hicolor/256x256/apps'
    icons_dir.mkdir(parents=True)
    shutil.copy2(icon_folder / 'icon-256.png', icons_dir / 'quib27.png')
    (package / 'usr/bin').mkdir(parents=True)
    (package / 'usr/bin/quib27').symlink_to('/opt/quib27/Quib27')
    deb = ROOT / 'dist' / f'quib27-{__version__}-linux-x64.deb'
    subprocess.run(['dpkg-deb', '--build', '--root-owner-group', '-Zxz', '-z3', str(package), str(deb)], check=True)
    return [archive, deb]


def windows_packages(target, icon_folder):
    compiler = shutil.which('ISCC.exe') or str(Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Inno Setup 6/ISCC.exe')
    subprocess.run([compiler, '/Qp', f'/DAppVersion={__version__}', f'/DSourceDir={target}',
                    f'/DOutputDir={ROOT / "dist"}', f'/DAppIcon={icon_folder / "icon.ico"}',
                    str(ROOT / 'packaging/windows.iss')], check=True)
    return [ROOT / 'dist' / f'quib27-{__version__}-windows-x64-setup.exe']


def macos_packages(bundle):
    # Ad-hoc signing permits the bundle to run on a build machine. It is not
    # Developer ID signing or notarization; downloads are labelled accordingly.
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(bundle)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    image = ROOT / 'dist' / f'quib27-{__version__}-macos-{architecture()}.dmg'
    image.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='quib27-dmg-') as folder:
        staging = Path(folder)
        subprocess.run(['ditto', str(bundle), str(staging / bundle.name)], check=True)
        (staging / 'Applications').symlink_to('/Applications')
        shutil.copy2(ROOT / 'INSTALL.md', staging / 'READ-ME.txt')
        subprocess.run(['hdiutil', 'create', '-volname', 'Quib27', '-srcfolder', str(staging), '-ov', '-format', 'UDZO', str(image)], check=True)
    return [image]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-agent', action='store_true', help='reuse a previously built agent for this exact OS/architecture')
    parser.add_argument('--package-only', action='store_true', help='finish packaging an existing native bundle without rebuilding it')
    args = parser.parse_args()
    system = platform.system()
    if system not in ('Linux', 'Windows', 'Darwin'):
        parser.error('Build on Linux, Windows or macOS')
    if not args.package_only and not args.skip_agent:
        subprocess.run([sys.executable, str(ROOT / 'build_agent.py')], check=True, cwd=ROOT)
    icon_folder = icons()
    executable = 'node.exe' if system == 'Windows' else 'node'
    runtime = ROOT / 'agent/runtime' / executable
    if not runtime.is_file():
        parser.error('Build the native agent runtime first')
    if not args.package_only:
        environment = dict(os.environ)
        environment['PYINSTALLER_CONFIG_DIR'] = str(ROOT / 'artifacts/pyinstaller-cache')
        command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--name', 'Quib27',
            '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build/pyinstaller'), '--specpath', str(ROOT / 'build'),
            '--python-option', 'X utf8', '--noupx',
            '--additional-hooks-dir', str(ROOT / 'packaging/hooks'),
            '--add-data', str(ROOT / 'desktop/ui') + ':desktop/ui',
            '--add-data', str(ROOT / 'connections') + ':connections',
            '--add-data', str(ROOT / 'agent/dist') + ':agent/dist',
            '--add-binary', str(runtime) + ':agent/runtime',
            '--add-data', str(runtime.parent / 'NODE-LICENSE.txt') + ':agent/runtime',
            '--add-data', str(runtime.parent / 'version.txt') + ':agent/runtime',
            '--collect-data', 'trafilatura', '--collect-data', 'justext', '--collect-data', 'dateparser', '--collect-data', 'tzdata',
            '--collect-submodules', 'keyring.backends', '--copy-metadata', 'keyring',
            '--collect-all', 'ddgs', '--collect-all', 'primp',
            '--exclude-module', 'wordfreq', '--exclude-module', 'faster_whisper', '--exclude-module', 'ebooklib', '--exclude-module', 'tkinter']
        # Keep the WebEngine binding dependencies, without unrelated optional
        # modules or the QML plugin catalogue (see packaging/hooks).
        for module in ('QtQuick3D', 'QtCharts', 'QtDataVisualization', 'QtGraphs', 'QtVirtualKeyboard', 'QtWebEngineQuick'):
            command += ['--exclude-module', 'PySide6.' + module]
        if system == 'Windows':
            # Keep stdio for bounded search workers, but hide the app's console.
            command += ['--hide-console', 'hide-early', '--icon', str(icon_folder / 'icon.ico')]
        elif system == 'Darwin':
            command += ['--windowed', '--icon', str(icon_folder / 'icon.icns'), '--osx-bundle-identifier', 'com.quib27.reader', '--target-architecture', platform.machine()]
        command.append(str(ROOT / 'daily_edition.py'))
        subprocess.run(command, check=True, cwd=ROOT, env=environment)
    target = ROOT / 'dist/Quib27'
    if system == 'Darwin':
        target = ROOT / 'dist/Quib27.app/Contents/Resources'
    import re
    binary_root = ROOT / ('dist/Quib27.app/Contents/Frameworks' if system == 'Darwin' else 'dist/Quib27/_internal')
    unnecessary = re.compile(r'^(?:lib)?Qt6?(?:Charts|DataVisualization|Graphs|Quick3D|VirtualKeyboard)', re.I)
    if any(unnecessary.match(path.name) for path in binary_root.rglob('*') if path.is_file()):
        raise RuntimeError('The bundle contains unrelated optional Qt components; check the packaging hooks')
    (target / 'agent').mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'agent/TOOLS.md', target / 'agent/TOOLS.md')
    for source, name in [('desktop/COPYRIGHT.txt', 'COPYRIGHT.txt'), ('INSTALL.md', 'READ-ME.txt')]:
        shutil.copy2(ROOT / source, target / name)
    notices(target)
    if system == 'Linux':
        shutil.copy2(ROOT / 'desktop/install.py', target / 'install.py')
        shutil.copy2(ROOT / 'desktop/ui/icon.svg', target / 'icon.svg')
        shutil.copy2(icon_folder / 'icon-256.png', target / 'icon.png')
        (target / 'install.sh').write_text('#!/bin/sh\nset -eu\nQUIB27_INSTALL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec python3 "$QUIB27_INSTALL_DIR/install.py" --launch "$@"\n', encoding='utf-8')
        (target / 'install.sh').chmod(0o755)
        files = linux_packages(target, icon_folder)
    elif system == 'Windows':
        files = windows_packages(target, icon_folder)
    else:
        files = macos_packages(ROOT / 'dist/Quib27.app')
    manifest = ROOT / 'dist' / f'build-info-{system.lower()}-{architecture()}.json'
    manifest.write_text(json.dumps({'version': __version__, 'system': system, 'architecture': architecture(),
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'developer_signed': False, 'notarized': False, 'packages': [p.name for p in files]}, indent=2) + '\n', encoding='utf-8')
    sums = checksum(files)
    print('Desktop release: ' + ', '.join(p.name for p in [*files, sums, manifest]), flush=True)


if __name__ == '__main__':
    main()

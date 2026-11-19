#!/usr/bin/env python3
"""Build an explicit-file-list desktop distribution; never bundles user data."""
from __future__ import annotations
import hashlib
from importlib import metadata
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent


def notices(target):
    folder = target / "licenses"
    folder.mkdir(exist_ok=True)
    lines = ["Daily Edition 0.1.0 — third-party notices", "",
        "Qt/PySide6 libraries are dynamically linked and remain replaceable in _internal/.",
        "Their LGPL/GPL/commercial alternatives and notices are reproduced below; only",
        "the Qt modules required by the desktop shell are bundled.",
        "Qt and PySide source: https://code.qt.io/qt/ and https://code.qt.io/pyside/pyside-setup.git/",
        "Upstream releases: https://download.qt.io/official_releases/qt/",
        "Qt WebEngine includes Chromium and its third-party notices.",
        "PyInstaller: GPL with a distribution exception for the generated application.",
        "FFmpeg is not bundled. Optional audio transcription uses the user's installation.",
        "EbookLib and wordfreq are not bundled in this desktop application.", ""]
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
    (target / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(lines))


def main():
    if sys.platform != "linux":
        raise SystemExit("This release builder currently targets Linux. Build other platforms separately.")
    dist = ROOT / "dist"
    work = ROOT / "build"
    environment = dict(os.environ)
    environment["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "artifacts/pyinstaller-cache")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", "DailyEdition",
        "--distpath", str(dist), "--workpath", str(work / "pyinstaller"), "--specpath", str(work),
        "--add-data", str(ROOT / "desktop/ui") + ":desktop/ui",
        "--collect-data", "trafilatura", "--collect-data", "justext", "--collect-data", "dateparser",
        "--collect-submodules", "keyring.backends", "--copy-metadata", "keyring",
        "--exclude-module", "wordfreq", "--exclude-module", "faster_whisper",
        "--exclude-module", "ebooklib", "--exclude-module", "tkinter",
        str(ROOT / "daily_edition.py")]
    subprocess.run(command, check=True, cwd=ROOT, env=environment)
    target = dist / "DailyEdition"
    shutil.copy2(ROOT / "desktop/install.py", target / "install.py")
    shutil.copy2(ROOT / "desktop/ui/icon.svg", target / "icon.svg")
    shutil.copy2(ROOT / "desktop/COPYRIGHT.txt", target / "COPYRIGHT.txt")
    shutil.copy2(ROOT / "DESKTOP.md", target / "READ-ME.txt")
    installer = target / "install.sh"
    installer.write_text('#!/bin/sh\nset -eu\nEDITION_INSTALL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec python3 "$EDITION_INSTALL_DIR/install.py" --launch "$@"\n')
    installer.chmod(0o755)
    notices(target)
    archive = dist / f"daily-edition-0.1.0-linux-{platform.machine()}.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(target, arcname="DailyEdition")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (dist / (archive.name + ".sha256")).write_text(digest + "  " + archive.name + "\n")
    print(f"Desktop release: {archive}", flush=True)


if __name__ == "__main__":
    main()

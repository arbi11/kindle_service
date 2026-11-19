#!/usr/bin/env python3
"""Install the packaged Linux desktop app for this user. Never changes reading data."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def desktop_quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%") + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local/opt/quib27")
    parser.add_argument("--applications-dir", type=Path, default=Path.home() / ".local/share/applications")
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    parser.add_argument("--launch", action="store_true")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    if not (source / "Quib27").exists():
        parser.error("Run this installer from the extracted Quib27 release folder")
    target = args.prefix.expanduser().resolve()
    if target != source:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
    executable = target / "Quib27"
    executable.chmod(0o755)
    args.applications_dir.mkdir(parents=True, exist_ok=True)
    args.bin_dir.mkdir(parents=True, exist_ok=True)
    icon = target / "icon.svg"
    entry = args.applications_dir / "quib27.desktop"
    entry.write_text(f"[Desktop Entry]\nType=Application\nName=Quib27\nComment=Quib27 — your personal reading room\nExec={desktop_quote(executable)}\nIcon={icon}\nTerminal=false\nCategories=Office;News;\nStartupWMClass=Quib27\n")
    entry.chmod(0o755)
    launcher = args.bin_dir / "quib27"
    # A symlink preserves sys.executable for launch-at-login.
    if launcher.is_symlink():
        launcher.unlink()
    elif launcher.exists():
        parser.error(f"{launcher} already exists and is not a symlink; choose a different --bin-dir")
    launcher.symlink_to(executable)
    update = shutil.which("update-desktop-database")
    if update:
        subprocess.run([update, str(args.applications_dir)], capture_output=True)
    print(f"Installed Quib27: {executable}")
    print("Open Quib27 from your application menu.")
    if args.launch:
        subprocess.Popen([str(executable)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

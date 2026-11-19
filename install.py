#!/usr/bin/env python3
"""Install the Kindle services for the current Linux user without changing configuration."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import venv
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
APPS = ("raindrop-kindle", "youtube-kindle")
RUNTIME = ("ai_client.py", "epub_writer.py", "raindrop_kindle.py", "youtube_kindle.py", "requirements.txt")


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def unit_state(unit: str) -> str:
    result = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True)
    state = result.stdout.strip()
    if state not in ("active", "activating", "deactivating", "inactive", "failed", "unknown"):
        raise RuntimeError("Cannot reach the systemd user manager. Use --skip-systemd to install files only.")
    return state


def systemd_quote(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def service_text(template: str, prefix: Path, app: str) -> str:
    command = ("ExecStart=" + systemd_quote(prefix / ".venv/bin/python") + " "
               + systemd_quote(prefix / (app.replace("-", "_") + ".py")) + " tick")
    return "\n".join(command if line.startswith("ExecStart=") else line
                     for line in template.splitlines()) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local/share/raindrop-kindle")
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    parser.add_argument("--unit-dir", type=Path, default=Path.home() / ".config/systemd/user")
    parser.add_argument("--skip-dependencies", action="store_true", help="copy files without installing Python packages")
    parser.add_argument("--skip-systemd", action="store_true", help="copy unit files without talking to systemd")
    parser.add_argument("--enable-timers", action="store_true", help="enable and start both timers after configuration is ready")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or later is required")
    if args.enable_timers and args.skip_systemd:
        parser.error("--enable-timers cannot be combined with --skip-systemd")
    for command in ("ffmpeg", "ffprobe"):
        if not shutil.which(command):
            parser.error(f"{command} is required; install FFmpeg first")
    args.prefix = args.prefix.expanduser().resolve()
    args.bin_dir = args.bin_dir.expanduser().resolve()
    args.unit_dir = args.unit_dir.expanduser().resolve()
    if args.prefix == SOURCE:
        parser.error("Install outside the source checkout to keep the virtual environment and backups separate")
    active_timers = []
    if not args.skip_systemd:
        active_timers = [app + ".timer" for app in APPS if unit_state(app + ".timer") == "active"]
    try:
        for timer in active_timers:
            run("systemctl", "--user", "stop", timer)
        if not args.skip_systemd and any(unit_state(app + ".service") in ("active", "activating", "deactivating") for app in APPS):
            raise RuntimeError("A Kindle batch is running. Let it finish, then run the installer again.")
        for directory in (args.prefix, args.bin_dir, args.unit_dir):
            directory.mkdir(parents=True, exist_ok=True)
        python = args.prefix / ".venv/bin/python"
        if not python.exists():
            venv.EnvBuilder(with_pip=not args.skip_dependencies).create(args.prefix / ".venv")
        if not args.skip_dependencies:
            run(python, "-m", "pip", "install", "-r", SOURCE / "requirements.txt")
        changes = [(args.prefix / name, (SOURCE / name).read_bytes(), 0o755 if name.endswith("kindle.py") else 0o644)
                   for name in RUNTIME]
        for app in APPS:
            script = args.prefix / (app.replace("-", "_") + ".py")
            launcher = f"#!/bin/sh\nexec {shlex.quote(str(python))} {shlex.quote(str(script))} \"$@\"\n"
            changes.append((args.bin_dir / app, launcher.encode(), 0o755))
            for suffix in ("service", "timer"):
                name = f"{app}.{suffix}"
                content = (SOURCE / "systemd" / name).read_text()
                if suffix == "service":
                    content = service_text(content, args.prefix, app)
                changes.append((args.unit_dir / name, content.encode(), 0o644))
        existing = [(path, content, mode) for path, content, mode in changes if path.exists() and path.read_bytes() != content]
        if existing:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = args.prefix / "backups" / stamp
            backup.mkdir(parents=True, mode=0o700)
            for n, (path, _, _) in enumerate(existing):
                target = backup / f"{n:02d}-{path.name}"
                target.write_bytes(path.read_bytes())
                target.chmod(0o600)
            print(f"Previous installed files saved to {backup}")
        for path, content, mode in changes:
            tmp = path.with_name(path.name + ".install-tmp")
            tmp.write_bytes(content)
            tmp.chmod(mode)
            os.replace(tmp, path)
        if not args.skip_systemd:
            run("systemctl", "--user", "daemon-reload")
            if args.enable_timers:
                run("systemctl", "--user", "enable", "--now", *(app + ".timer" for app in APPS))
        print(f"Installed both services in {args.prefix}")
        print("Configuration, credentials, channel subscriptions and delivery history were preserved.")
        if not args.enable_timers and not active_timers:
            print("Configure both services before enabling their timers; see README.md.")
        return 0
    finally:
        for timer in active_timers:
            run("systemctl", "--user", "start", timer)


if __name__ == "__main__":
    raise SystemExit(main())

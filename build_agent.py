#!/usr/bin/env python3
"""Build Pi from pinned npm packages and bundle a verified private Node runtime."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
NODE_VERSION = "v24.22.3"
NODE_DOWNLOADS = {
    ('Linux', 'x86_64'): ('linux-x64.tar.xz', '4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb'),
    ('Darwin', 'x86_64'): ('darwin-x64.tar.gz', '977614af0504c81045e7fd85596dbca0e5f951b96681516dadafedcca8c447f7'),
    ('Darwin', 'arm64'): ('darwin-arm64.tar.gz', 'ff6e17d4874b76d272212b908cc020d6e815fba5a6c7eaaa06add725840307fd'),
    ('Windows', 'amd64'): ('win-x64.zip', 'e612af99f626ab415ea225af6bce5c971feb448cb5cae250bb1c3fa50c5f7d9f'),
}


def main():
    system, machine = platform.system(), platform.machine().lower()
    suffix, expected = NODE_DOWNLOADS.get((system, machine), ('', ''))
    if not suffix:
        raise SystemExit(f'No pinned Node runtime for {system} {machine}')
    filename = f'node-{NODE_VERSION}-{suffix}'
    name = filename.removesuffix('.tar.xz').removesuffix('.tar.gz').removesuffix('.zip')
    cache = ROOT / "artifacts/node-download"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / filename
    if not archive.exists():
        with urllib.request.urlopen(f"https://nodejs.org/dist/{NODE_VERSION}/{archive.name}", timeout=90) as source, archive.open("wb") as output:
            shutil.copyfileobj(source, output)
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise SystemExit("The Node download did not match its pinned SHA-256.")
    windows = system == 'Windows'
    executable = 'node.exe' if windows else 'node'
    binary = cache / name / ('node.exe' if windows else 'bin/node')
    if not binary.is_file():
        if windows:
            with zipfile.ZipFile(archive) as source:
                source.extractall(cache)
        else:
            with tarfile.open(archive) as source:
                source.extractall(cache, filter="data")
    agent = ROOT / "agent"
    runtime = agent / "runtime"
    runtime.mkdir(exist_ok=True)
    shutil.copy2(binary, runtime / executable)
    shutil.copy2(cache / name / "LICENSE", runtime / "NODE-LICENSE.txt")
    (runtime / "version.txt").write_text(f"{NODE_VERSION}\n{expected}  {archive.name}\n", encoding='utf-8')
    npm = cache / name / ('node_modules/npm/bin/npm-cli.js' if windows else 'lib/node_modules/npm/bin/npm-cli.js')
    environment = dict(os.environ)
    environment["PATH"] = str(runtime) + os.pathsep + environment.get("PATH", "")
    for command in (["ci", "--ignore-scripts", "--no-audit", "--no-fund"], ["test"], ["run", "build"]):
        if command == ["run", "build"] and (agent / "dist").exists():
            shutil.rmtree(agent / "dist")
        subprocess.run([str(runtime / executable), str(npm), *command], cwd=agent, env=environment, check=True)
    # Include notices for the npm dependencies alongside the bundled code.
    notices = agent / "dist/licenses"
    notices.mkdir(parents=True, exist_ok=True)
    packages = []
    for package in (agent / "node_modules").glob("**/package.json"):
        if "node_modules" in package.parent.relative_to(agent / "node_modules").parts:
            continue
        data = json.loads(package.read_text(encoding='utf-8'))
        if not data.get("name") or not data.get("version"):
            continue
        packages.append({"name": data["name"], "version": data["version"], "license": data.get("license", "See package notice")})
        for file in package.parent.iterdir():
            if file.is_file() and file.name.lower().startswith(("license", "copying", "notice")):
                target = notices / data["name"].replace("/", "_")
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target / file.name)
    (notices / "packages.json").write_text(json.dumps(packages, indent=2) + "\n")
    print("Built the pinned Pi reading agent and bundled Node runtime.", flush=True)


if __name__ == "__main__":
    main()

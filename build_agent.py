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

ROOT = Path(__file__).resolve().parent
NODE_VERSION = "v24.22.3"
NODE_SHA256 = "4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb"


def main():
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("The bundled Pi runtime currently targets Linux x86_64.")
    name = f"node-{NODE_VERSION}-linux-x64"
    cache = ROOT / "artifacts/node-download"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / (name + ".tar.xz")
    if not archive.exists():
        with urllib.request.urlopen(f"https://nodejs.org/dist/{NODE_VERSION}/{archive.name}", timeout=90) as source, archive.open("wb") as output:
            shutil.copyfileobj(source, output)
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != NODE_SHA256:
            raise SystemExit("The Node download did not match its pinned SHA-256.")
    if not (cache / name / "bin/node").is_file():
        with tarfile.open(archive) as source:
            source.extractall(cache, filter="data")
    agent = ROOT / "agent"
    runtime = agent / "runtime"
    runtime.mkdir(exist_ok=True)
    shutil.copy2(cache / name / "bin/node", runtime / "node")
    shutil.copy2(cache / name / "LICENSE", runtime / "NODE-LICENSE.txt")
    (runtime / "version.txt").write_text(f"{NODE_VERSION}\n{NODE_SHA256}  {archive.name}\n")
    npm = cache / name / "lib/node_modules/npm/bin/npm-cli.js"
    environment = dict(os.environ)
    environment["PATH"] = str(runtime) + os.pathsep + environment.get("PATH", "")
    for command in (["ci", "--ignore-scripts", "--no-audit", "--no-fund"], ["test"], ["run", "build"]):
        if command == ["run", "build"] and (agent / "dist").exists():
            shutil.rmtree(agent / "dist")
        subprocess.run([str(runtime / "node"), str(npm), *command], cwd=agent, env=environment, check=True)
    # Include notices for the npm dependencies alongside the bundled code.
    notices = agent / "dist/licenses"
    notices.mkdir(parents=True, exist_ok=True)
    packages = []
    for package in (agent / "node_modules").glob("**/package.json"):
        if "node_modules" in package.parent.relative_to(agent / "node_modules").parts:
            continue
        data = json.loads(package.read_text())
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

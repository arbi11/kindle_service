"""Run the bundled Pi selection agent with credentials passed only over stdin."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess


class PiSelection:
    def __init__(self):
        self.folder = Path(__file__).resolve().parent.parent / "agent"

    def select(self, request, *, provider, model, key):
        runtime = self.folder / "runtime/node"
        node = str(runtime) if runtime.is_file() else shutil.which("node")
        script = self.folder / "dist/reader-agent.mjs"
        if not node or not script.is_file():
            raise ValueError("The Pi reading agent is missing from this build. Rebuild the desktop app with its agent runtime.")
        try:
            result = subprocess.run([node, str(script)], input=json.dumps({**request, "provider": provider,
                                    "model": model, "api_key": key}), text=True, capture_output=True,
                                    timeout=105, check=False)
            if len(result.stdout) > 64000:
                raise ValueError("Pi returned an oversized article selection")
            data = json.loads(result.stdout)
            if not isinstance(data, dict):
                raise ValueError("Pi returned an invalid article selection")
            if result.returncode or data.get("error"):
                raise ValueError(str(data.get("error") or "Pi could not select articles")[:700])
            return data
        except subprocess.TimeoutExpired:
            raise ValueError("Pi article selection timed out. Try again with a shorter preference.") from None
        except (json.JSONDecodeError, OSError):
            raise ValueError("The Pi reading agent could not start. Check this desktop build.") from None

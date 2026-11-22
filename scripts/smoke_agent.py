"""Confirm this platform's pinned Node binary runs the packaged Pi entry point."""
import json
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parent.parent
system = platform.system()
base = ROOT / ('dist/Quib27.app/Contents/Frameworks' if system == 'Darwin' else 'dist/Quib27/_internal')
runtime = base / ('agent/runtime/node.exe' if system == 'Windows' else 'agent/runtime/node')
script = base / 'agent/dist/reader-agent.mjs'
assert runtime.is_file() and script.is_file(), 'The bundled Pi runtime is missing'
result = subprocess.run([str(runtime), str(script)], input=json.dumps({
    'provider':'openai', 'model':'gpt-6-luna', 'api_key':'synthetic-offline-check',
    'preferences':'', 'maximum':1, 'articles':[]}), capture_output=True, text=True, check=True, timeout=30)
data = json.loads(result.stdout)
assert data['runtime'] == 'pi-agent-core' and data['model_turns'] == 0 and data['selected'] == []
print('Native Pi runtime verified; no model requests were made.')

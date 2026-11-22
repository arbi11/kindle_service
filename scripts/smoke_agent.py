"""Verify Qt bindings and the actual packaged Pi bridge without model requests."""
import json
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parent.parent
system = platform.system()
executable = ROOT / ('dist/Quib27.app/Contents/MacOS/Quib27' if system == 'Darwin' else
                     'dist/Quib27/Quib27.exe' if system == 'Windows' else 'dist/Quib27/Quib27')
result = subprocess.run([str(executable), '--check-desktop-components'], capture_output=True,
                        text=True, check=True, timeout=45)
data = json.loads(result.stdout)
assert data['pi'] == 'pi-agent-core' and data['model_requests'] == 0 and data['qt'].startswith('6.')
print('Native Qt bindings and packaged Pi bridge verified; no model requests were made.')

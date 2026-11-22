"""Exercise a packaged app against disposable data, without AI calls or delivery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def check(executable, root):
    root.mkdir(parents=True, exist_ok=True)
    log = (root / 'smoke.log').open('wb')
    process = subprocess.Popen([str(executable), '--headless', '--no-keyring', '--no-scheduler', '--data-dir', str(root)],
                               stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise AssertionError('Packaged app exited: ' + (root / 'smoke.log').read_text(encoding='utf-8', errors='replace')[-3000:])
            try:
                runtime = json.loads((root / 'runtime.json').read_text(encoding='utf-8'))
                origin = 'http://127.0.0.1:' + str(runtime['port'])
                def api(path):
                    request = urllib.request.Request(origin + path, headers={'Cookie':'edition_session=' + runtime['token']})
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return response.read()
                data = json.loads(api('/api/bootstrap'))
                break
            except (OSError, ValueError, urllib.error.URLError):
                time.sleep(.2)
        else:
            raise AssertionError('The packaged app did not start in time')
        assert data['stats']['total'] == 3 and len(data['sources']) == 0
        assert not any(data['secrets']['configured'].values())
        assert not data['settings']['schedule_enabled'] and not data['settings']['auto_send']
        assert 'library-unread-count' in api('/app.js').decode('utf-8')
        assert len(json.loads(api('/api/items'))['items']) == 3
        assert json.loads(api('/api/knowledge/graph?connection=none'))['nodes'] == []
        assert json.loads(api('/api/knowledge/graph?connection=youtube%2Craindrop'))['stats']['readings'] == 3
        # A second app process must reuse this profile, rather than start a writer.
        second = subprocess.run([str(executable), '--headless', '--no-keyring', '--no-scheduler', '--data-dir', str(root)],
                                capture_output=True, timeout=30)
        assert second.returncode == 0, second.stderr
        assert json.loads((root / 'runtime.json').read_text(encoding='utf-8'))['pid'] == process.pid
        # The bounded helper must still have working stdio in native GUI bundles.
        worker = subprocess.run([str(executable), '--publication-search'], input=b'{}', capture_output=True, timeout=20)
        assert worker.returncode == 1 and 'error' in json.loads(worker.stdout)
        print(json.dumps({'version':data['version'], 'sample_stories':3, 'configured_credentials':0,
                          'single_instance':True, 'helper_stdio':True, 'executable':executable.name}), flush=True)
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=10)
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='quib27 smoke ') as folder:
        check(args.executable.resolve(), Path(folder) / 'reading profile')


if __name__ == '__main__':
    main()

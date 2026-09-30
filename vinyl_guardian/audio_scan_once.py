"""Persist successful scan consumption, with optional Supervisor UI reset."""
import json
import os
from pathlib import Path
import tempfile
import urllib.request


class StartupScanGate:
    def __init__(self, root):
        self.path = Path(root) / 'audio_scan_startup.json'

    def saved(self):
        try:
            saved = json.loads(self.path.read_text())
            return saved if isinstance(saved, dict) else {}
        except (OSError, ValueError):
            return {}

    def should_run(self, enabled):
        if not enabled:
            self.path.unlink(missing_ok=True)
            return False
        return not self.saved().get('completed', False)

    def effective_source(self, preference):
        saved = self.saved()
        return 'auto' if saved.get('completed') and preference == saved.get('previous_preference') else preference

    def complete(self, preference):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as handle:
            json.dump({'completed': True, 'previous_preference': preference}, handle)
            tmp = handle.name
        os.replace(tmp, self.path)


def reset_scan_options(options_path='/data/options.json'):
    token = os.environ.get('SUPERVISOR_TOKEN')
    if not token:
        return False
    with open(options_path) as handle:
        options = json.load(handle)
    options.update(audio_scan_on_start=False, audio_source='auto')
    request = urllib.request.Request(
        'http://supervisor/addons/self/options',
        data=json.dumps({'options': options}).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.load(response)
    return result.get('result') == 'ok'

"""Always-on stylus use, stored privately inside the add-on's persistent data."""
import json
import math
import os
import time
from pathlib import Path

USAGE_FILE = Path('/data/stylus_usage.json')
CHECKPOINT_SECONDS = 60


class StylusUsage:
    def __init__(self, path=USAGE_FILE, clock=time.monotonic):
        self.path = Path(path)
        self.backup = self.path.with_suffix('.backup.json')
        self.clock = clock
        self.last_checkpoint = clock()
        self.dirty = False
        candidates = []
        errors = []
        for candidate in (self.path, self.backup):
            if not candidate.exists():
                continue
            try:
                data = json.loads(candidate.read_text())
                if data.get('format_version') != 1:
                    raise ValueError('Unsupported counter format')
                values = [data['music_seconds'], data['runout_seconds']]
                if any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) or v < 0 for v in values):
                    raise ValueError('Invalid accumulated durations')
                candidates.append((candidate, data))
            except (OSError, ValueError, KeyError, AttributeError) as error:
                errors.append(f'{candidate}: {error}')
        if errors and not candidates:
            raise ValueError('Stylus use counter cannot be loaded; refusing to reset it. ' + '; '.join(errors))
        selected, data = max(candidates, key=lambda item:item[1]['music_seconds']+item[1]['runout_seconds']) if candidates else (None,{})
        self.recovered = bool(errors) or selected == self.backup
        self.music_seconds = float(data.get('music_seconds',0))
        self.runout_seconds = float(data.get('runout_seconds',0))
        self.dirty = True
        self.flush()

    def observe(self, frame, sample_count, rate, known_off=False):
        if rate <= 0 or sample_count < 0:
            raise ValueError('Invalid audio duration')
        status = frame.get('status')
        active = not known_off and status in ('Playing','Runout Groove')
        if active:
            seconds = sample_count / float(rate)
            if status == 'Playing':
                self.music_seconds += seconds
            else:
                self.runout_seconds += seconds
            self.dirty = self.dirty or seconds > 0
        if self.dirty and (not active or self.clock()-self.last_checkpoint >= CHECKPOINT_SECONDS):
            self.flush()

    @property
    def total_seconds(self):
        return self.music_seconds + self.runout_seconds

    @property
    def hours(self):
        return self.total_seconds / 3600

    @property
    def saved_hours(self):
        return (self.saved['music_seconds'] + self.saved['runout_seconds']) / 3600

    def saved_snapshot(self):
        return dict(self.saved, total_seconds=self.saved['music_seconds']+self.saved['runout_seconds'])

    def snapshot(self):
        return {'format_version':1, 'music_seconds':self.music_seconds,
                'runout_seconds':self.runout_seconds}

    def flush(self):
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True,exist_ok=True)
        payload = json.dumps(self.snapshot(),indent=2)
        # Save the current snapshot twice. If interrupted between replacements,
        # startup selects the valid snapshot with the greatest cumulative total.
        for path in (self.backup,self.path):
            temporary = path.with_suffix(path.suffix+'.tmp')
            with temporary.open('w') as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary,path)
        directory_fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        self.saved = self.snapshot()
        self.dirty = False
        self.last_checkpoint = self.clock()

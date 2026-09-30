"""Resume recording stages without retaining incompatible later recordings."""
from pathlib import Path
import shutil
import wave
import calibration_control as control

TITLES = ['Input gain', 'Quiet baseline', 'Motor startup', 'Music to runout',
          'Needle lift', 'Motor shutdown', 'Room disturbances']


def complete_recordings(files):
    for path in files.values():
        try:
            with wave.open(str(path), 'rb') as recording:
                if recording.getsampwidth() != 2 or recording.getnframes() <= 0:
                    return False
                frame_size = recording.getnchannels() * 2
                remaining = recording.getnframes()
                while remaining:
                    count = min(8192, remaining)
                    if len(recording.readframes(count)) != count * frame_size:
                        return False
                    remaining -= count
        except (OSError, EOFError, wave.Error):
            return False
    return bool(files)


class CalibrationSession:
    def __init__(self):
        self.initialized = False
        self.next_stage = 0
        self.gain = None
        self.use_existing = False
        self.files = {}

    def prepare(self, directory, files, reuse, saved_gain):
        if self.initialized:
            return
        self.initialized = True
        self.files = files
        self.use_existing = reuse and complete_recordings(files)
        self.gain = saved_gain if self.use_existing else None
        if self.use_existing:
            self.next_stage = 7
            control.set_stage(6, 'Reuse saved calibration audio')
        else:
            if not reuse and Path(directory).exists():
                shutil.rmtree(directory)
            Path(directory).mkdir(parents=True, exist_ok=True)

    def navigate(self, request, log):
        target = 0 if request.action == 'restart' else request.stage
        if self.gain is None:
            target = 0
        self.use_existing = False
        self.next_stage = target
        if target == 0:
            self.gain = None
        for index, path in enumerate(self.files.values(), start=1):
            if index >= target:
                Path(path).unlink(missing_ok=True)
        control.rewind(target)
        log(f'Restarting from {TITLES[target]}. This step and all later recording stages will be recorded again.')

    def record(self, callbacks, log):
        if self.use_existing:
            control.set_stage(6, 'Reuse saved calibration audio')
            log('Reusing all six saved calibration recordings automatically. No confirmation or new recording is needed.')
            control.checkpoint()
            return self.gain
        else:
            while self.next_stage < len(callbacks):
                stage = self.next_stage
                control.set_stage(stage, TITLES[stage])
                control.checkpoint()
                result = callbacks[stage]()
                if stage == 0:
                    self.gain = result
                self.next_stage += 1
        control.set_stage(7, 'Review recordings')
        control.wait_for_confirmation('All recording stages are complete. Choose a step to repeat if needed. Press Continue to analyse and save this calibration.', log)
        return self.gain

"""Shared calibration state and confirmations for MQTT and the ingress screen."""
from collections import deque
import threading
import time

_ready = threading.Event()
_lock = threading.Lock()
_waiting = False
_publisher = None
_state = {'phase': 'inactive', 'instruction': 'Enable calibration_mode in the app configuration and restart to begin.', 'stage': -1, 'stage_title': 'Not running', 'step_id': 0}
_logs = deque(maxlen=400)


def configure(publisher):
    global _publisher
    _publisher = publisher


def begin(enabled):
    global _waiting
    with _lock:
        _waiting = False
        _ready.clear()
        _logs.clear()
        _state.update(phase='starting' if enabled else 'inactive', stage=-1,
                      stage_title='Starting' if enabled else 'Not running',
                      instruction='Preparing calibration…' if enabled else 'Enable calibration_mode in the app configuration and restart to begin.')


def snapshot():
    with _lock:
        return dict(_state, waiting=_waiting, logs=list(_logs))


def append_log(message):
    with _lock:
        _logs.append({'time': time.strftime('%H:%M:%S'), 'message': str(message)})


def set_stage(stage, title):
    with _lock:
        _state.update(stage=stage, stage_title=title)


def set_status(message, phase=None):
    global _waiting
    with _lock:
        _state['instruction'] = message
        if phase:
            _state['phase'] = phase
            if phase in ('complete', 'failed', 'inactive'):
                _waiting = False
    if _publisher:
        _publisher(message)


def confirm(step_id=None):
    global _waiting
    with _lock:
        if not _waiting or (step_id is not None and step_id != _state['step_id']):
            return False
        _waiting = False
        _state.update(phase='recording', instruction='Recording / analysing. Follow the action window in the live log and wait for the next instruction.')
        _ready.set()
        return True


def wait_for_confirmation(instruction, logger=print):
    global _waiting
    if _publisher is None:
        raise RuntimeError('Start calibration through the add-on so the calibration screen can confirm each step.')
    message = instruction + ' When ready, press Continue on the calibration screen or the Home Assistant device.'
    with _lock:
        _ready.clear()
        _waiting = True
        _state.update(phase='waiting', instruction=message, step_id=_state['step_id'] + 1)
    logger(message)
    if _publisher:
        _publisher(message)
    try:
        _ready.wait()
    finally:
        with _lock:
            _waiting = False
            _ready.clear()
    if _publisher:
        _publisher(snapshot()['instruction'])

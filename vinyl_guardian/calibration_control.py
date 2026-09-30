"""Shared calibration state and confirmations for MQTT and the ingress screen."""
from collections import deque
import threading
import time

_ready = threading.Event()
_lock = threading.RLock()
_waiting = False
_publisher = None
_state = {'phase': 'inactive', 'instruction': 'Enable calibration_mode in the app configuration and restart to begin.', 'stage': -1, 'stage_title': 'Not running', 'step_id': 0}
_logs = deque(maxlen=400)


def configure(publisher):
    global _publisher
    _publisher = publisher


def begin(enabled):
    global _waiting, _navigation
    with _lock:
        _navigation = None
        _interrupt.clear()
        _waiting = False
        _ready.clear()
        _logs.clear()
        _state.update(phase='starting' if enabled else 'inactive', stage=-1, max_stage=-1,
                      stage_title='Starting' if enabled else 'Not running',
                      instruction='Preparing calibration…' if enabled else 'Enable calibration_mode in the app configuration and restart to begin.')


def snapshot():
    with _lock:
        return dict(_state, waiting=_waiting, logs=list(_logs), can_restart=_state['phase'] not in ('inactive', 'starting', 'saving', 'changing'), repeat_stages=list(range(min(6, _state.get('max_stage', -1)) + 1)) if _state['phase'] not in ('inactive', 'starting', 'saving', 'changing') else [])


def append_log(message):
    with _lock:
        _logs.append({'time': time.strftime('%H:%M:%S'), 'message': str(message)})


def set_stage(stage, title):
    with _lock:
        _state.update(stage=stage, stage_title=title, max_stage=max(_state.get('max_stage', -1), min(6, stage)), step_id=_state['step_id'] + 1)


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
        checkpoint()
        _ready.clear()
        _waiting = True
        _state.update(phase='waiting', instruction=message, step_id=_state['step_id'] + 1)
    try:
        checkpoint()
        logger(message)
        if _publisher:
            _publisher(message)
        _ready.wait()
    finally:
        with _lock:
            _waiting = False
            _ready.clear()
    checkpoint()
    if _publisher:
        _publisher(snapshot()['instruction'])


class CalibrationNavigation(BaseException):
    """Cooperative control signal, kept separate from capture/analysis errors."""
    def __init__(self, action, stage=None):
        self.action = action
        self.stage = stage


_interrupt = threading.Event()
_navigation = None


def request_navigation(action, step_id, stage=None):
    global _navigation, _waiting
    with _lock:
        if (step_id != _state['step_id'] or _navigation is not None
                or _state['phase'] in ('inactive', 'starting', 'saving', 'changing')):
            return False
        if action == 'repeat':
            if type(stage) is not int or not 0 <= stage <= min(6, _state.get('max_stage', -1)):
                return False
        elif action != 'restart':
            return False
        _navigation = CalibrationNavigation(action, stage)
        _waiting = False
        _state.update(phase='changing', instruction='Stopping the current step. Wait for the new preparation instructions.', step_id=_state['step_id'] + 1)
        _interrupt.set()
        _ready.set()
        return True


def checkpoint():
    global _navigation
    with _lock:
        navigation = _navigation
        if navigation is not None:
            _navigation = None
            _interrupt.clear()
    if navigation is not None:
        raise navigation


def pause(seconds):
    _interrupt.wait(seconds)
    checkpoint()


def wait_for_navigation():
    _interrupt.wait()
    checkpoint()


def freeze_for_save():
    # Once profile persistence starts, reject navigation until it completes.
    with _lock:
        checkpoint()
        _state.update(phase='saving', instruction='Saving the calibration result…', step_id=_state['step_id'] + 1)


def rewind(stage):
    with _lock:
        _state.update(max_stage=stage, stage=stage)

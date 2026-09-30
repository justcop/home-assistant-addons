"""Explicit, non-queued calibration confirmations via Home Assistant MQTT."""
import threading

_ready = threading.Event()
_lock = threading.Lock()
_waiting = False
_publisher = None


def configure(publisher):
    global _publisher
    _publisher = publisher


def set_status(message):
    if _publisher:
        _publisher(message)


def confirm():
    with _lock:
        if not _waiting:
            return False
        _ready.set()
        return True


def wait_for_confirmation(instruction, logger=print):
    global _waiting
    if _publisher is None:
        raise RuntimeError('Start calibration through the add-on so Home Assistant can confirm each step.')
    with _lock:
        _ready.clear()
        _waiting = True
    message = instruction + ' When ready, press Continue Calibration in Home Assistant.'
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
        _publisher('Recording / analysing. Wait for the next instruction.')

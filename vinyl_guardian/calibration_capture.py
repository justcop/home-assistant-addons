"""Read calibration PCM, failing loudly instead of producing empty WAVs."""
import time


def capture_bytes(open_capture, duration, rate, channels, clock=time.monotonic, checkpoint=lambda: None):
    checkpoint()
    try:
        device = open_capture()
    except Exception as exc:
        raise RuntimeError(f'Cannot open audio input. Check the selected input before calibrating: {exc}') from exc
    target = int(rate * duration)
    frames = 0
    audio = bytearray()
    started = last_progress = clock()
    try:
        while frames < target:
            checkpoint()
            length, data = device.read()
            checkpoint()
            now = clock()
            if length < 0:
                raise RuntimeError('Audio input reported a capture error; calibration aborted.')
            if length > 0:
                if len(data) != length * channels * 2:
                    raise RuntimeError('Audio input returned incomplete PCM frames; calibration aborted.')
                audio.extend(data)
                frames += length
                last_progress = now
            elif now - last_progress > 5:
                raise RuntimeError('Audio input supplied no samples for five seconds; calibration aborted.')
            else:
                time.sleep(0.01)
            if now - started > duration + 15:
                raise RuntimeError('Audio input stalled; calibration aborted.')
    finally:
        device.close()
    return audio

"""Validate persistent recording storage without silently selecting another disk."""
import io
import os
from pathlib import Path
import shutil
import tempfile
import wave

DEFAULT_RECORDING_DIRECTORY = '/share/vinyl_guardian'


def storage_diagnostics(value):
    """Report the filesystem visible INSIDE the add-on, even on failure."""
    path = Path(str(value)).resolve()
    existing = path
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    lines = [f'Requested recording folder: {value}', f'Resolved recording folder: {path}',
             f'Calibration WAV folder: {path / "calibration_data"}']
    try:
        usage = shutil.disk_usage(existing)
        lines.append(f'Filesystem free space: {usage.free} bytes ({usage.free / 1024**3:.2f} GiB)')
        matches = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            left, right = line.split(' - ', 1)
            fields, filesystem = left.split(), right.split()
            mount = Path(fields[4].replace('\\040', ' ').replace('\\134', '\\'))
            if path == mount or mount in path.parents:
                matches.append((len(str(mount)), mount, fields[5], filesystem[0]))
        if matches:
            _, mount, options, kind = max(matches)
            lines.append(f'Visible mount: {mount}; filesystem: {kind}; mount options: {options}')
    except (OSError, ValueError) as exc:
        lines.append(f'Could not inspect filesystem: {exc}')
    return lines


def _check_folder(path):
    """Verify the operations used by capture and capture metadata persistence."""
    probe = renamed = None
    try:
        path.mkdir(exist_ok=True)
        payload = io.BytesIO()
        with wave.open(payload, 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(44100)
            output.writeframes(b'\0\0' * 64)
        expected = payload.getvalue()
        with tempfile.NamedTemporaryFile(prefix='.vinyl-write-test-', dir=path, delete=False) as handle:
            probe = Path(handle.name)
            handle.write(expected)
            handle.flush()
            os.fsync(handle.fileno())
        if probe.read_bytes() != expected:
            raise OSError('Storage read-back did not match the written WAV data')
        renamed = probe.with_suffix('.renamed')
        os.replace(probe, renamed)
        renamed.unlink()
    except OSError as exc:
        raise ValueError(f'Storage check failed for {path}: {type(exc).__name__}: {exc}. '
                         'Check that the drive is mounted inside the add-on, writable and has free space.') from exc
    finally:
        for candidate in (probe, renamed):
            if candidate is not None:
                candidate.unlink(missing_ok=True)


def prepare_recording_directory(value, allowed_roots=('/share', '/media', '/data')):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('recording_directory must be a non-empty absolute folder path')
    path = Path(value.strip())
    if not path.is_absolute():
        raise ValueError('recording_directory must be absolute, for example /media/Recordings/vinyl_guardian')
    path = path.resolve()
    if not any(path == Path(root).resolve() or Path(root).resolve() in path.parents
               for root in allowed_roots):
        raise ValueError('recording_directory must be inside a mapped /share, /media or /data folder')
    # Do not create a missing drive/share parent on the internal filesystem.
    if not path.parent.is_dir():
        raise ValueError(f'Recording parent folder {path.parent} does not exist. Mount the storage first.')
    _check_folder(path)
    _check_folder(path / 'calibration_data')
    return str(path)

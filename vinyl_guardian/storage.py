"""Validate persistent recording storage without silently selecting another disk."""
import os
from pathlib import Path
import tempfile


DEFAULT_RECORDING_DIRECTORY = '/share/vinyl_guardian'


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
    # Do not create a missing drive/share directory and accidentally start
    # recording onto the internal filesystem instead.
    if not path.parent.is_dir():
        raise ValueError(f'Recording parent folder {path.parent} does not exist. Mount the storage first.')
    path.mkdir(exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(prefix='.vinyl-write-test-', dir=path) as probe:
            probe.write(b'Vinyl Guardian storage test\n')
            probe.flush()
            os.fsync(probe.fileno())
    except OSError as exc:
        raise ValueError(f'Recording folder {path} is not writable: {exc}') from exc
    return str(path)

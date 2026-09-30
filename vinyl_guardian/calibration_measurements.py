"""Replayable measurements and recording gain provenance."""
import csv
import hashlib
import io
import json
from pathlib import Path
import wave
import zipfile
from detector import extract_features, pcm16_to_mono

NAMES = ('calib_off_floor.wav', 'calib_spin_up.wav', 'calib_music_to_runout.wav',
         'calib_needle_lift.wav', 'calib_power_down.wav', 'calib_disturbance.wav')


def signatures(directory):
    return {name: {'size': (Path(directory) / name).stat().st_size,
                   'mtime_ns': (Path(directory) / name).stat().st_mtime_ns}
            for name in NAMES if (Path(directory) / name).is_file()}


def save_capture_gain(directory, gain):
    path = Path(directory) / 'capture_metadata.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'mic_volume': gain, 'files': signatures(directory)}))
    temp.replace(path)


def capture_gain(directory, share_dir):
    current = signatures(directory)
    if len(current) != len(NAMES):
        return None
    try:
        manifest = json.loads((Path(directory) / 'capture_metadata.json').read_text())
        if manifest['files'] == current:
            return manifest['mic_volume']
    except (OSError, ValueError, KeyError):
        pass
    # Older releases saved the capture gain in the candidate, even if rejected.
    newest_recording = max(item['mtime_ns'] for item in current.values()) / 1e9
    candidates = []
    for path in (Path(share_dir) / 'profiles').glob('profile_*.json'):
        try:
            payload = json.loads(path.read_text())
            names = set(payload.get('metadata', {}).get('calibration_files', {}).values())
            if names == set(NAMES) and payload.get('created_unix', 0) >= newest_recording:
                gain = payload.get('thresholds', {}).get('mic_volume')
                if gain is not None:
                    candidates.append((payload['created_unix'], gain))
        except (OSError, ValueError, KeyError):
            continue
    return min(candidates)[1] if candidates else None


def export_measurements(directory, share_dir):
    """Export fixed calibration files, without raw audio or app options."""
    output = io.BytesIO()
    metadata = {'format_version': 1, 'chunk_frames': 2048,
                'capture_gain': capture_gain(directory, share_dir), 'recordings': {}}
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in NAMES:
            path = Path(directory) / name
            if not path.is_file():
                continue
            digest = hashlib.sha256()
            with path.open('rb') as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(block)
            with wave.open(str(path), 'rb') as recording:
                rate, channels = recording.getframerate(), recording.getnchannels()
                if recording.getsampwidth() != 2:
                    raise ValueError('Measurements require 16-bit PCM recordings.')
                metadata['recordings'][name] = {'rate': rate, 'channels': channels,
                    'frames': recording.getnframes(), 'sha256': digest.hexdigest()}
                csv_text = io.StringIO()
                fields = ['time', 'sample_count', 'rms', 'music_rms', 'hfer', 'crest', 'peak', 'zcr']
                writer = csv.DictWriter(csv_text, fieldnames=fields)
                writer.writeheader()
                frames = 0
                while True:
                    raw = recording.readframes(2048)
                    if len(raw) != 2048 * channels * 2:
                        break
                    frames += 2048
                    writer.writerow(dict(time=frames / rate, sample_count=2048,
                        **extract_features(pcm16_to_mono(raw, channels), rate)))
                archive.writestr(path.stem + '.csv', csv_text.getvalue())
        if not metadata['recordings']:
            raise ValueError('No calibration recordings are available yet.')
        archive.writestr('measurements.json', json.dumps(metadata, indent=2))
        for name in ('calibration_report.txt', 'calibration_quality.json', 'regression_report.json', 'auto_calibration.json'):
            path = Path(share_dir) / name
            if path.is_file():
                archive.writestr(name, path.read_bytes())
        for path in (Path(share_dir) / 'profiles').glob('profile_*.json'):
            archive.writestr('profiles/' + path.name, path.read_bytes())
        extended = Path(share_dir) / 'calibration_measurements'
        for name in ['calibration_feature_analysis.json'] + ['extended_' + Path(name).stem + '.csv' for name in NAMES]:
            path = extended / name
            if path.is_file():
                archive.writestr('extended/' + name, path.read_bytes())
    return output.getvalue()

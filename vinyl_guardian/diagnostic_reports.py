"""Bounded, local diagnostic export. Contains no application credentials."""
import io
import json
import zipfile
from pathlib import Path


def export_diagnostics(recording_directory, max_bytes=128*1024*1024, max_clips=20):
    root = (Path(recording_directory) / 'experiments').resolve()
    files = []
    audio = root / 'event_audio'
    candidates = []
    ignored = set()
    for session_path in (root/'diagnostic_sessions').glob('*.json'):
        if session_path.is_symlink():
            continue
        try:
            session = json.loads(session_path.read_text())
            for event in session.get('events',[]):
                if event.get('event') == 'expected_long_break':
                    ignored.update((session['id'], stamp) for stamp in event.get('capture_times',[]))
        except (OSError, ValueError, KeyError, AttributeError):
            continue
    excluded = 0
    for sidecar in audio.glob('*.json'):
        if sidecar.is_symlink():
            continue
        try:
            metadata = json.loads(sidecar.read_text())
            session_id = metadata.get('details', {}).get('diagnostic_session')
            if session_id:
                if (session_id,metadata.get('trigger_time')) in ignored:
                    excluded += 1
                else:
                    candidates.append((sidecar, metadata))
        except (OSError, ValueError):
            continue
    candidates.sort(key=lambda item: item[1].get('trigger_time', 0), reverse=True)
    used = 0
    included = []
    for sidecar, metadata in candidates[:max_clips]:
        group = [sidecar, audio / Path(str(metadata.get('wav', ''))).name,
                 audio / Path(str(metadata.get('trace', ''))).name]
        if not all(p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root) for p in group):
            continue
        size = sum(p.stat().st_size for p in group)
        if used + size > max_bytes:
            continue
        files.extend(group)
        included.append(metadata)
        used += size
    for path in sorted((root/'diagnostic_sessions').glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:30]:
        if path.is_symlink():
            continue
        size = path.stat().st_size
        if used + size <= max_bytes:
            files.append(path)
            used += size
    manifest = {'included_clips': len(included), 'available_clips': len(candidates), 'excluded_long_break_clips': excluded,
                'max_clips': max_clips, 'uncompressed_bytes': used,
                'events': [{'event': m['event'], 'unix_time': m['trigger_time'],
                            'wav': m['wav'], 'label': m.get('label'),
                            'session_id': m['details']['diagnostic_session']} for m in included],
                'notes': ['Newest complete clips are included, bounded by count and byte size.',
                          'Known-off clips are explicit ground truth. Listening anomalies are review candidates, not music labels.',
                          'Session events include confirmed track timing and scrobble requests, which do not prove a physical state.',
                          'Intentional flip/pause annotations cover the preceding 20 and following 45 seconds.',
                          'The frames.jsonl trace aligns one row per PCM chunk. Recompute all measurements from the WAV for replay.']}
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps(manifest,indent=2))
        for path in files:
            archive.write(path,path.relative_to(root).as_posix())
    return output.getvalue()

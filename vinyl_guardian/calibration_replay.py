"""Whole-sequence calibration checks using the production detector."""
import wave
from detector import GuardianDetector, extract_features, pcm16_to_mono

STAGES = ('floor', 'spinup', 'transition', 'lift', 'powerdown', 'disturbance')


def load_recording_features(files, chunk=2048):
    recordings = {}
    for stage in STAGES:
        with wave.open(files[stage], 'rb') as recording:
            if recording.getsampwidth() != 2:
                raise ValueError('Calibration replay requires 16-bit PCM WAV files')
            rate, channels = recording.getframerate(), recording.getnchannels()
            rows = []
            while True:
                raw = recording.readframes(chunk)
                if len(raw) != chunk * channels * 2:
                    break
                rows.append(extract_features(pcm16_to_mono(raw, channels), rate))
            if not rows:
                raise ValueError(f'No complete audio chunks in {stage}')
            recordings[stage] = {'rate': rate, 'chunk': chunk, 'features': rows}
    return recordings


def evaluate_sequence(recordings, thresholds):
    detector = None
    elapsed = 0.0
    stages, checks = {}, {}
    expected_rpm = thresholds.get('calibration_expected_runout_rpm', '33⅓')
    for stage in STAGES:
        recording = recordings[stage]
        rate, chunk = recording['rate'], recording['chunk']
        if detector is None:
            detector = GuardianDetector(thresholds, rate=rate, channels=1)
        elif detector.rate != rate:
            raise ValueError('All calibration stages must have the same sample rate')
        frames, changes = [], []
        previous = None
        for index, features in enumerate(recording['features']):
            elapsed += chunk / rate
            frame = detector.update_features(features, elapsed, chunk)
            frame = dict(frame, time=(index + 1) * chunk / rate)
            frames.append(frame)
            if frame['status'] != previous:
                changes.append({'time': frame['time'], 'status': frame['status']})
                previous = frame['status']
        statuses = [f['status'] for f in frames]
        tail_start = {'floor': 0, 'spinup': 20, 'lift': 15, 'powerdown': 20, 'disturbance': 0}.get(stage, 0)
        tail = [f for f in frames if f['time'] >= tail_start]
        def fraction(status):
            return sum(f['status'] == status for f in tail) / len(tail) if tail else 0.0
        if stage == 'floor':
            checks['quiet_stays_off'] = all(s == 'Powered Off' for s in statuses)
        elif stage == 'spinup':
            checks['motor_detected_and_stable'] = fraction('Motor Idle') >= .95 and not any(s in ('Playing', 'Runout Groove') for s in statuses)
        elif stage == 'transition':
            playing = next((i for i, s in enumerate(statuses) if s == 'Playing'), None)
            required = ('Playing', 'Between Tracks', 'Motor Idle', 'Runout Groove')
            cursor = 0
            for s in statuses:
                if cursor < len(required) and s == required[cursor]:
                    cursor += 1
            checks['music_to_runout_order'] = cursor == len(required) and statuses[-1] == 'Runout Groove'
            checks['power_continuous_after_music'] = playing is not None and all(s != 'Powered Off' for s in statuses[playing:])
            locks = [f for f in frames if f['runout_locked']]
            checks['runout_matches_record_speed'] = bool(locks) and all(f['runout_rpm'] == expected_rpm for f in locks)
        elif stage == 'lift':
            checks['needle_lift_returns_to_motor'] = fraction('Motor Idle') >= .95 and 'Powered Off' not in statuses
        elif stage == 'powerdown':
            checks['shutdown_stays_off'] = fraction('Powered Off') >= .95 and statuses[-1] == 'Powered Off'
            checks['shutdown_has_no_false_music_or_runout'] = not any(s in ('Playing', 'Between Tracks', 'Runout Groove') for s in statuses)
        elif stage == 'disturbance':
            checks['disturbance_stays_off_in_sequence'] = all(s == 'Powered Off' for s in statuses)
        stages[stage] = {
            'duration': len(frames) * chunk / rate, 'transitions': changes,
            'on_fraction': sum(f['turntable_on'] for f in frames) / len(frames),
            'steady_off_fraction': fraction('Powered Off'),
            'first_off_seconds': next((f['time'] for f in frames if not f['turntable_on']), None),
        }
    return {'passed': all(checks.values()), 'checks': checks, 'stages': stages}


def evaluate_calibration_sequence(files, thresholds):
    return evaluate_sequence(load_recording_features(files), thresholds)


def main():
    """python calibration_replay.py WAV_FOLDER CANDIDATE_JSON [--baseline JSON]."""
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description='Replay all six calibration recordings through the live detector.')
    parser.add_argument('directory')
    parser.add_argument('profile')
    parser.add_argument('--baseline', help='Optional profile for side-by-side comparison')
    args = parser.parse_args()
    names = ('off_floor', 'spin_up', 'music_to_runout', 'needle_lift', 'power_down', 'disturbance')
    files = dict(zip(STAGES, (str(Path(args.directory) / f'calib_{name}.wav') for name in names)))
    recordings = load_recording_features(files)
    def profile(path):
        data = json.loads(Path(path).read_text())
        return data.get('thresholds', data)
    results = {'candidate': evaluate_sequence(recordings, profile(args.profile))}
    if args.baseline:
        results['baseline'] = evaluate_sequence(recordings, profile(args.baseline))
    print(json.dumps(results, indent=2))
    return 0 if results['candidate']['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

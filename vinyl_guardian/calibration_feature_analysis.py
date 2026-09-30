"""Observational extended measurements and blocked validation on saved WAVs."""
import csv
import hashlib
import json
from pathlib import Path
import wave
import numpy as np
from detector import extract_features, pcm16_to_mono
from telemetry import FeatureExtractor, pcm16_channels, stereo_features
from feature_combinations import search


def measurement_rows(path, chunk=2048):
    with wave.open(str(path), 'rb') as recording:
        if recording.getsampwidth() != 2:
            raise ValueError('Extended measurements require 16-bit PCM WAV')
        rate, channels = recording.getframerate(), recording.getnchannels()
        extractor = FeatureExtractor(rate)
        elapsed = 0.0
        while True:
            raw = recording.readframes(chunk)
            if len(raw) != chunk * channels * 2:
                break
            mono = pcm16_to_mono(raw, channels)
            elapsed += chunk / rate
            core = extract_features(mono, rate)
            yield dict(extractor.extract(mono), **stereo_features(pcm16_channels(raw, channels)),
                       **{'time': elapsed, 'sample_count': chunk,
                          'detector_hfer': core['hfer'],
                          'detector_music_rms': core['music_rms']})


def rank_pair(positive, negative):
    """Fit each one-feature threshold on early blocks; validate on later blocks.

    Scores are balanced by class and then by recording, so long files do not
    dominate. No detector-predicted state is used as a label.
    """
    candidates = []
    fields = set(next(iter(positive.values()))[0]) - {'time', 'sample_count', 'input_channels'}
    def split(rows):
        boundary = max(1, int(len(rows) * .6))
        return rows[:boundary], rows[boundary:]
    for field in sorted(fields):
        def values(groups, part):
            return {name: np.asarray([float(r[field]) for r in split(rows)[part]]) for name, rows in groups.items()}
        train_pos, train_neg = values(positive, 0), values(negative, 0)
        pos_med = float(np.median([np.median(v) for v in train_pos.values()]))
        neg_med = float(np.median([np.median(v) for v in train_neg.values()]))
        if not np.isfinite(pos_med + neg_med) or pos_med == neg_med:
            continue
        threshold, higher = (pos_med + neg_med) / 2, pos_med > neg_med
        per_recording = {}
        for label, groups in ((True, values(positive, 1)), (False, values(negative, 1))):
            for name, v in groups.items():
                if len(v):
                    predicted = v > threshold if higher else v < threshold
                    per_recording[name] = float(np.mean(predicted == label))
        pos_scores = [per_recording[name] for name in positive if name in per_recording]
        neg_scores = [per_recording[name] for name in negative if name in per_recording]
        if not pos_scores or not neg_scores:
            continue
        candidates.append({'feature': field, 'threshold': threshold, 'positive_when_higher': higher,
            'held_out_balanced_accuracy': float((np.mean(pos_scores) + np.mean(neg_scores)) / 2),
            'worst_recording_accuracy': min(per_recording.values()),
            'held_out_recording_accuracy': per_recording})
    return sorted(candidates, key=lambda x: (x['held_out_balanced_accuracy'], x['worst_recording_accuracy']), reverse=True)


def analyse_features(files, output_directory):
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    recordings = {}
    sources = {}
    for name, path in files.items():
        rows = list(measurement_rows(path))
        if not rows:
            raise ValueError(f'No measurements in {name}')
        recordings[name] = rows
        digest = hashlib.sha256()
        with Path(path).open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
        sources[name] = {'filename': Path(path).name, 'sha256': digest.hexdigest(),
                         'chunks': len(rows), 'duration': rows[-1]['time']}
        with (directory / f'extended_{Path(path).stem}.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    def interval(name, start=0, end=float('inf')):
        return [r for r in recordings[name] if start <= r['time'] <= end]
    motor = {'motor_startup_steady': interval('spinup', 20),
             'needle_lift_steady': interval('lift', 15)}
    off = {'quiet_off': interval('floor'), 'shutdown_steady': interval('powerdown', 20),
           'disturbance_off': interval('disturbance')}
    duration = recordings['transition'][-1]['time']
    runout = {'runout_tail': interval('transition', duration - 20)}
    music = {'music_steady': interval('transition', 25, duration - 40)}
    comparisons = {'motor_vs_off': (motor, off), 'motor_vs_runout': (motor, runout),
                   'runout_vs_off': (runout, off), 'power_vs_off': (dict(motor, **runout), off)}
    if len(music['music_steady']) >= 40:
        comparisons['music_vs_runout'] = (music, runout)
    report = {'format_version': 2, 'observational_only': True,
        'measurement_count': len(recordings['floor'][0]) - 2, 'recordings': sources,
        'validation': 'First 60% of each labelled steady block trains thresholds; last 40% validates. Classes and recordings are equally weighted.',
        'limitations': ['One calibration session is not independent validation across different records or days.',
                       'Labels use instructed steady intervals. Transition music uses 25s through duration minus 40s; runout uses the last 20s.',
                       'Frequency resolution is determined by a 2048-frame chunk; low-frequency and within-chunk periodicity estimates are approximate.',
                       'Rankings do not automatically change live detection. Promoted features must pass sequence and historical regressions.'],
        'comparisons': {name: rank_pair(pos, neg) for name, (pos, neg) in comparisons.items()
                        if all(pos.values()) and all(neg.values())}}
    report['combination_analysis'] = {}
    for name, (positive, negative) in comparisons.items():
        if all(len(rows) >= 10 for rows in list(positive.values()) + list(negative.values())):
            report['combination_analysis'][name] = search(positive, negative)
    (directory / 'calibration_feature_analysis.json').write_text(json.dumps(report, indent=2))
    return report

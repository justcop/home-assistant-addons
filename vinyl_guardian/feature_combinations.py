"""Offline feature-combination experiments. Never modifies the live detector."""
from itertools import combinations
import numpy as np

# Fixed before evaluation, excluding duplicated channel/amplitude measurements.
FEATURES = ('band_60_120', 'band_4k_8k', 'band_20_60', 'band_120_250',
            'band_low_ratio', 'band_high_ratio', 'spectral_bandwidth_hz',
            'spectral_flatness', 'spectral_entropy', 'spectral_flux',
            'autocorr_periodicity', 'subframe_rms_cv')


def blocks(groups):
    partitions = [{}, {}, {}]
    for name, rows in groups.items():
        first, second = int(len(rows)*.6), int(len(rows)*.8)
        if min(first, second-first, len(rows)-second) < 2:
            raise ValueError(f'Not enough rows for blocked fitting/selection/test in {name}')
        for part, subset in zip(partitions, (rows[:first], rows[first:second], rows[second:])):
            part[name] = subset
    return partitions


def fit(positive, negative, features):
    def arrays(groups):
        values = [np.asarray([[row[f] for f in features] for row in rows]) for rows in groups.values()]
        weights = [np.full(len(a), 1/len(values)/len(a)) for a in values]
        return np.vstack(values), np.concatenate(weights)
    p, pw = arrays(positive)
    n, nw = arrays(negative)
    pm, nm = np.sum(p*pw[:,None],axis=0), np.sum(n*nw[:,None],axis=0)
    scale = np.sqrt((np.sum((p-pm)**2*pw[:,None],axis=0) + np.sum((n-nm)**2*nw[:,None],axis=0))/2)
    scale = np.maximum(scale, 1e-8)
    ps, ns = (p-pm)/scale, (n-nm)/scale
    covariance = ((ps*pw[:,None]).T @ ps + (ns*nw[:,None]).T @ ns)/2
    # Shrink covariance to handle correlated bands without double-counting.
    covariance = .8*covariance + .2*np.eye(len(features))
    weight = np.linalg.solve(covariance, (pm-nm)/scale)
    return {'features': list(features), 'centre': ((pm+nm)/2).tolist(),
            'scale': scale.tolist(), 'weight': weight.tolist()}


def logits(model, rows):
    x = np.asarray([[row[f] for f in model['features']] for row in rows])
    return ((x-np.asarray(model['centre']))/np.asarray(model['scale'])) @ np.asarray(model['weight'])


def score(model, row):
    value = float(logits(model, [row])[0])
    return float(1/(1+np.exp(-np.clip(value,-30,30))))


def evaluate(model, positive, negative):
    per_recording = {}
    classes = []
    for expected, groups in ((True,positive),(False,negative)):
        rates = []
        for name, rows in groups.items():
            accuracy = float(np.mean((logits(model,rows)>0)==expected))
            per_recording[name] = accuracy
            rates.append(accuracy)
        classes.append(float(np.mean(rates)))
    return {'balanced_accuracy': float(np.mean(classes)), 'positive_recall': classes[0],
            'negative_recall': classes[1], 'worst_recording_accuracy': min(per_recording.values()),
            'recording_accuracy': per_recording}


def search(positive, negative):
    pt,pv,pe = blocks(positive)
    nt,nv,ne = blocks(negative)
    candidates = []
    for size in (1,2,3):
        for fields in combinations(FEATURES,size):
            model = fit(pt,nt,fields)
            validation = evaluate(model,pv,nv)
            candidates.append({'model': model, 'selection': validation})
    # Test blocks are untouched during fitting and model selection.
    candidates.sort(key=lambda x:(x['selection']['balanced_accuracy'],x['selection']['worst_recording_accuracy'],-len(x['model']['features'])), reverse=True)
    winners = []
    for size in (1,2,3):
        candidate = next(x for x in candidates if len(x['model']['features'])==size)
        candidate['test'] = evaluate(candidate['model'],pe,ne)
        winners.append(candidate)
    return {'candidate_count':len(candidates), 'winners_by_size': winners,
            'validation': 'First 60% fits, next 20% selects, final 20% tests. Recordings and classes receive equal weight. Same-session test blocks are not independent recordings.'}


def replay_candidate(recordings, thresholds, model, blend=1.0):
    """Inject extended evidence into an offline detector; no live activation."""
    from detector import GuardianDetector
    from calibration_replay import evaluate_sequence
    if not 0 <= blend <= 1:
        raise ValueError('Blend must be between zero and one')
    class Prototype(GuardianDetector):
        def _profile_motor_score(self, features):
            original = super()._profile_motor_score(features)
            if original is None:
                raise ValueError('Prototype replay needs a calibrated motor profile')
            return (1-blend)*original + blend*score(model, features)
    return evaluate_sequence(recordings, thresholds, Prototype)

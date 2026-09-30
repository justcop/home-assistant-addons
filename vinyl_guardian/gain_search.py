"""Bounded midpoint search, with a verified safe fallback between gain steps."""
import math

PREFERRED_MIN = 0.50
PREFERRED_MAX = 0.80
SAFE_MAX = 0.85
MIN_SIGNAL = 0.01


def find_input_gain(set_gain, measure_peak, log=print, settle=lambda: None):
    low, high = 1, 100
    safe_candidates = set()
    current_volume = None

    def apply(volume):
        nonlocal current_volume
        if volume != current_volume:
            set_gain(volume)
            settle()
            current_volume = volume

    def measure(seconds):
        peak = float(measure_peak(seconds))
        if not math.isfinite(peak) or peak < 0:
            raise RuntimeError('Invalid audio level during gain calibration or verification.')
        return peak

    while low <= high:
        volume = (low + high) // 2
        apply(volume)
        peak = measure(3.0)
        log(f'Gain {volume}%: peak {peak:.3f}, search range {low}–{high}%.')
        if PREFERRED_MIN <= peak <= PREFERRED_MAX:
            log(f'Verifying {volume}% over a loud 10-second passage…')
            peak = measure(10.0)
            if PREFERRED_MIN <= peak <= SAFE_MAX:
                return volume
        if MIN_SIGNAL <= peak <= PREFERRED_MAX:
            safe_candidates.add(volume)
        if peak > PREFERRED_MAX:
            high = volume - 1
        else:
            low = volume + 1

    # The preferred peak range is a target, not a requirement. Real music and
    # integer/device gain steps can put one setting below it and the next above
    # the clipping limit. Verify the highest safe sampled setting first.
    candidates = sorted(safe_candidates)
    first, last = 0, len(candidates) - 1
    best = None
    index = last
    while first <= last:
        volume = candidates[index]
        apply(volume)
        log(f'Preferred range falls between gain settings. Verifying safe candidate {volume}% over 10 seconds…')
        peak = measure(10.0)
        log(f'Gain {volume}% verification: peak {peak:.3f}.')
        if MIN_SIGNAL <= peak <= SAFE_MAX:
            best = volume
            first = index + 1
        elif peak > SAFE_MAX:
            last = index - 1
        else:
            # A quiet passage cannot establish a useful gain. Do not pretend
            # that going to a lower setting fixes an absent input signal.
            raise RuntimeError('Audio became too quiet during verification. Play a loud passage and repeat Input gain.')
        index = (first + last) // 2
    if best is not None:
        apply(best)
        log(f'Using verified safe gain {best}%. A peak below the preferred range is acceptable; detector thresholds are calibrated from the recordings.')
        return best
    if high == 0:
        raise RuntimeError('Input is too loud at minimum gain. Reduce the physical input level.')
    if low == 101:
        raise RuntimeError('Input remains too quiet at maximum gain. Check the input and play a loud record.')
    raise RuntimeError('No unclipped input gain could be verified. Play a steady loud passage and repeat Input gain.')

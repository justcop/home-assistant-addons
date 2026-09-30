"""Bounded midpoint search for a stable, unclipped input gain."""
import math


def find_input_gain(set_gain, measure_peak, log=print, settle=lambda: None):
    low, high = 1, 100
    while low <= high:
        volume = (low + high) // 2
        set_gain(volume)
        settle()
        peak = float(measure_peak(3.0))
        if not math.isfinite(peak) or peak < 0:
            raise RuntimeError('Invalid audio level during gain calibration.')
        log(f'Gain {volume}%: peak {peak:.3f}, search range {low}–{high}%.')
        if 0.50 <= peak <= 0.80:
            log(f'Verifying {volume}% over a loud 10-second passage…')
            peak = float(measure_peak(10.0))
            if not math.isfinite(peak) or peak < 0:
                raise RuntimeError('Invalid audio level during gain verification.')
            if 0.50 <= peak <= 0.85:
                return volume
        if peak > 0.80:
            high = volume - 1
        else:
            low = volume + 1
    if high == 0:
        raise RuntimeError('Input is too loud at minimum gain. Reduce the physical input level.')
    if low == 101:
        raise RuntimeError('Input remains too quiet at maximum gain. Check the input and play a loud record.')
    raise RuntimeError('No consistent input gain found. Play a steady loud passage and repeat Input gain.')

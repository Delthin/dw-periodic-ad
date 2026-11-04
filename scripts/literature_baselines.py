"""Independent, causal NumPy baselines; see docs/literature-baselines-implementation.md.

No fit, labels, global normalization, or external detector implementations are used.
Scores belong to the last observed point of each query window.
"""
from __future__ import annotations

import operator

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


# Bound temporary matrices rather than materializing N x history x window.
_SR_BATCH = 512
_MP_BATCH = 128
_LOG_FLOOR = 1e-12


def _integer(name: str, value: int, minimum: int) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be an integer >= {minimum}")
    try:
        result = operator.index(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be an integer >= {minimum}") from exc
    if result < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return result


def _values(values: np.ndarray) -> np.ndarray:
    if np.iscomplexobj(values):
        raise ValueError("values must be a finite, real, one-dimensional array")
    try:
        result = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("values must be a finite, real, one-dimensional array") from exc
    if result.ndim != 1 or not np.isfinite(result).all():
        raise ValueError("values must be a finite, real, one-dimensional array")
    return result


def _power_scale(rows: np.ndarray) -> np.ndarray:
    """Window-local power-of-two scaling, preserving small representable changes."""
    exponents = np.frexp(np.max(np.abs(rows), axis=1))[1]
    return np.ldexp(rows, -exponents[:, None])


def spectral_residual_online(
    values: np.ndarray, window: int = 64, mag_window: int = 3,
    score_window: int = 21,
) -> np.ndarray:
    """Sliding-window SR, with m=5 gradient extrapolation and kappa=5 padding.

    Frequency smoothing is trailing with partial means at the first bins. The
    score is abs(s[t] - mean(s[max(0,t-z+1):t+1])) / mean(...), within the current
    saliency map, including t but excluding all five estimated tail points.
    No /10 or [0, 1] clipping is applied. Constant windows score zero.
    """
    x = _values(values)
    window = _integer("window", window, 12)
    mag_window = _integer("mag_window", mag_window, 1)
    score_window = _integer("score_window", score_window, 1)
    if mag_window > window + 5:
        raise ValueError("mag_window must not exceed window + 5")
    scores = np.full(x.size, np.nan, dtype=np.float64)
    if x.size < window:
        return scores

    windows = sliding_window_view(x, window)
    length = window + 5
    bins = np.arange(length)
    left = np.maximum(0, bins - mag_window + 1)
    counts = bins - left + 1
    batch_size = min(_SR_BATCH, max(1, 1_048_576 // length))
    for start in range(0, len(windows), batch_size):
        rows = _power_scale(windows[start:start + batch_size])
        constant = np.all(rows == rows[:, :1], axis=1)
        # Paper Eq. (8): mean of the five secant slopes to the last point.
        slopes = (rows[:, -1, None] - rows[:, -1 - np.arange(1, 6)]) / np.arange(1, 6)
        # Paper Eq. (9): x[n-m+1] + m*g, NOT x[n] + g.
        estimate = rows[:, -5] + 5.0 * slopes.mean(axis=1)
        padded = np.concatenate((rows, np.repeat(estimate[:, None], 5, axis=1)), axis=1)
        spectrum = np.fft.fft(padded, axis=1)
        magnitude = np.abs(spectrum)
        floor = np.maximum(_LOG_FLOOR * magnitude.max(axis=1), np.finfo(np.float64).tiny)
        log_magnitude = np.log(np.maximum(magnitude, floor[:, None]))
        cumulative = np.pad(np.cumsum(log_magnitude, axis=1), ((0, 0), (1, 0)))
        average = (cumulative[:, bins + 1] - cumulative[:, left]) / counts
        residual = log_magnitude - average
        saliency = np.abs(np.fft.ifft(np.exp(residual + 1j * np.angle(spectrum)), axis=1))
        local_mean = saliency[:, max(0, window - score_window):window].mean(axis=1)
        batch = np.divide(
            np.abs(saliency[:, window - 1] - local_mean), local_mean,
            out=np.zeros(len(rows), dtype=np.float64), where=local_mean > 0,
        )
        batch[constant] = 0.0
        scores[start + window - 1:start + window - 1 + len(rows)] = batch
    return scores


def _unit_windows(rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Centered unit vectors and exact-constant flags, without E[x^2]-E[x]^2."""
    centered = _power_scale(rows)
    centered -= centered[:, :1].copy()
    centered -= centered.mean(axis=1, keepdims=True)
    # A second local scale protects the norm from underflow for tiny variation.
    scale = np.max(np.abs(centered), axis=1)
    constant = scale == 0
    np.divide(centered, scale[:, None], out=centered, where=~constant[:, None])
    norm = np.sqrt(np.einsum("ij,ij->i", centered, centered))
    np.divide(centered, norm[:, None], out=centered, where=~constant[:, None])
    return centered, constant


def left_matrix_profile(
    values: np.ndarray, window: int, history: int = 2048,
    exclusion: int | None = None,
) -> np.ndarray:
    """Exact bounded left-profile, not the DAMP pruning/approximation algorithm.

    For query start i, eligible starts are
    max(0, i-exclusion-history+1) <= j <= i-exclusion (inclusive).
    exclusion defaults to window and must be >= window: references never overlap
    the query. history counts eligible starts, NOT elapsed points before i.
    Population-z Euclidean distance is assigned to t=i+window-1; no candidate
    gives NaN. Constant/constant distance is 0, constant/nonconstant sqrt(window).
    """
    x = _values(values)
    window = _integer("window", window, 8)
    history = _integer("history", history, 1)
    exclusion = window if exclusion is None else _integer("exclusion", exclusion, window)
    scores = np.full(x.size, np.nan, dtype=np.float64)
    if x.size < window or exclusion > x.size - window:
        return scores

    windows = sliding_window_view(x, window)
    # More starts than exist is equivalent to unbounded history, and capping
    # here also keeps arbitrarily large Python integers out of NumPy indexing.
    history = min(history, len(windows))
    # Limit both distance matrices and normalized reference-window temporaries.
    batch_size = min(_MP_BATCH, max(1, 1_048_576 // window))
    # References are also tiled when history or window is unusually large.
    reference_size = min(2048, max(1, 1_048_576 // window))
    roundoff = 64 * np.finfo(np.float64).eps * window
    root_m = np.sqrt(float(window))
    for begin in range(exclusion, len(windows), batch_size):
        end = min(begin + batch_size, len(windows))
        query, qconstant = _unit_windows(windows[begin:end])
        query_starts = np.arange(begin, end)
        best = np.full(end - begin, np.inf, dtype=np.float64)
        reference_begin = max(0, begin - exclusion - history + 1)
        reference_end = end - exclusion
        for first in range(reference_begin, reference_end, reference_size):
            last = min(first + reference_size, reference_end)
            reference, rconstant = _unit_windows(windows[first:last])
            reference_starts = np.arange(first, last)
            allowed = (
                (reference_starts[None, :] <= query_starts[:, None] - exclusion)
                & (reference_starts[None, :] >= query_starts[:, None] - exclusion - history + 1)
            )
            squared = (2.0 - 2.0 * (query @ reference.T)) * window
            np.maximum(squared, 0.0, out=squared)
            either_constant = qconstant[:, None] | rconstant[None, :]
            squared[either_constant] = float(window)
            squared[qconstant[:, None] & rconstant[None, :]] = 0.0
            squared[~allowed] = np.inf
            nearest = np.argmin(squared, axis=1)
            for row, col in enumerate(nearest):
                distance2 = squared[row, col]
                if not np.isfinite(distance2):
                    continue
                if qconstant[row] or rconstant[col]:
                    distance = np.sqrt(distance2)
                else:
                    # Direct vector differences avoid cancellation for repeats.
                    # Refine every numerically tied near-zero candidate, not
                    # just an arbitrary argmin that could hide an exact match.
                    if distance2 <= roundoff:
                        tied = np.flatnonzero(squared[row] <= distance2 + roundoff)
                        delta = reference[tied] - query[row]
                        distance = root_m * np.sqrt(np.min(np.einsum("ij,ij->i", delta, delta)))
                    else:
                        delta = reference[col] - query[row]
                        distance = root_m * np.sqrt(np.dot(delta, delta))
                best[row] = min(best[row], distance)
        scores[begin + window - 1:end + window - 1] = best
    return scores

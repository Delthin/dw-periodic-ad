"""Mathematical oracles deliberately use scalar windows, not production helpers."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from literature_baselines import left_matrix_profile, spectral_residual_online


def direct_sr(values, window=64, mag_window=3, score_window=21):
    result = np.full(len(values), np.nan, dtype=np.float64)
    for t in range(window - 1, len(values)):
        observed = np.array(values[t - window + 1:t + 1], dtype=np.float64)
        if np.all(observed == observed[0]):
            result[t] = 0.0
            continue
        # Eqs. (8)-(9), written with explicit scalar gradients and indices.
        gradient = sum((observed[-1] - observed[-1 - k]) / k for k in range(1, 6)) / 5
        predicted = observed[len(observed) - 5] + 5 * gradient
        padded = np.array([*observed, *([predicted] * 5)])
        fourier = np.fft.fft(padded)
        amplitude = np.abs(fourier)
        logs = np.log(np.maximum(amplitude, max(amplitude.max() * 1e-12, np.finfo(float).tiny)))
        smoothed = np.array([
            sum(logs[max(0, f - mag_window + 1):f + 1]) / min(f + 1, mag_window)
            for f in range(len(logs))
        ])
        saliency = np.abs(np.fft.ifft(np.exp(logs - smoothed + 1j * np.angle(fourier))))
        average = sum(saliency[max(0, window - score_window):window]) / min(window, score_window)
        result[t] = abs(saliency[window - 1] - average) / average if average else 0.0
    return result


def direct_z_distance(a, b):
    # Center each raw window separately. No rolling moments/dot-product identity.
    def z(v):
        v = np.asarray(v, dtype=np.float64)
        centered = v - v[0]
        centered = centered - sum(centered) / len(v)
        if np.all(centered == 0):
            return None
        scale = max(abs(centered))
        centered /= scale
        return centered / np.sqrt(sum(centered * centered) / len(v))

    za, zb = z(a), z(b)
    if za is None and zb is None:
        return 0.0
    if za is None or zb is None:
        return np.sqrt(float(len(a)))
    return np.sqrt(sum((za - zb) ** 2))


def direct_left(values, window, history=2048, exclusion=None):
    """O(N^2*m) exhaustive scalar-window definition (bounded when history < N)."""
    exclusion = window if exclusion is None else exclusion
    result = np.full(len(values), np.nan, dtype=np.float64)
    for i in range(exclusion, len(values) - window + 1):
        candidates = range(max(0, i - exclusion - history + 1), i - exclusion + 1)
        result[i + window - 1] = min(
            direct_z_distance(values[i:i + window], values[j:j + window]) for j in candidates
        )
    return result


class LiteratureBaselinesTest(unittest.TestCase):
    def cases(self):
        rng = np.random.default_rng(719)
        return {
            "random": rng.normal(size=112),
            "periodic": np.tile(np.array([0., 2., -1., 4., 1., -3., 2., 0.]), 14),
            "constant": np.full(112, 7.),
            "level_shift": np.r_[np.zeros(48), np.full(32, 12.), np.full(32, -4.)],
        }

    def test_sr_matches_direct_math(self):
        for name, values in self.cases().items():
            for window, mag, score in [(12, 3, 21), (32, 1, 1), (64, 3, 21), (16, 7, 5)]:
                with self.subTest(case=name, window=window, mag=mag, score=score):
                    np.testing.assert_allclose(
                        spectral_residual_online(values, window, mag, score),
                        direct_sr(values, window, mag, score), rtol=2e-10, atol=2e-10,
                    )

    def test_left_profile_matches_exhaustive_math(self):
        for name, values in self.cases().items():
            for window, history, exclusion in [(8, 2048, None), (16, 1, None), (12, 21, 18)]:
                with self.subTest(case=name, window=window, history=history):
                    np.testing.assert_allclose(
                        left_matrix_profile(values, window, history, exclusion),
                        direct_left(values, window, history, exclusion), rtol=2e-12, atol=2e-12,
                    )

    def test_future_changes_and_truncation_preserve_prefix_across_batches(self):
        values = np.random.default_rng(27).normal(size=700)
        for detector in [spectral_residual_online, lambda x: left_matrix_profile(x, 16, 37)]:
            full = detector(values)
            for cut in [0, 11, 12, 31, 32, 143, 527, 641]:
                with self.subTest(detector=detector, cut=cut):
                    altered = values.copy()
                    altered[cut:] = 1e20 * np.arange(1, len(values) - cut + 1)
                    np.testing.assert_allclose(detector(altered)[:cut], full[:cut], rtol=0, atol=2e-12)
                    np.testing.assert_allclose(detector(values[:cut]), full[:cut], rtol=0, atol=2e-12)
        # Oracle equivalence beyond both batch boundaries, not just prefix equality.
        np.testing.assert_allclose(spectral_residual_online(values), direct_sr(values), rtol=2e-10, atol=2e-10)
        np.testing.assert_allclose(left_matrix_profile(values, 16, 7), direct_left(values, 16, 7), atol=2e-12)

    def test_warmup_tail_alignment_and_constant_conventions(self):
        for n in [0, 1, 11, 12, 15, 16, 31, 32, 50]:
            for name, scores, first in [
                ("sr", spectral_residual_online(np.ones(n), 12), 11),
                ("mp", left_matrix_profile(np.ones(n), 8), 15),
                ("mp_exclusion", left_matrix_profile(np.ones(n), 8, exclusion=11), 18),
            ]:
                with self.subTest(name=name, n=n):
                    self.assertEqual(scores.shape, (n,))
                    self.assertEqual(scores.dtype, np.dtype("float64"))
                    self.assertTrue(np.isnan(scores[:first]).all())
                    self.assertTrue(np.isfinite(scores[first:]).all())
                    np.testing.assert_array_equal(scores[first:], np.zeros(max(0, n - first)))
        m = 8
        values = np.r_[np.arange(m), np.arange(m)[::-1]]
        self.assertAlmostEqual(left_matrix_profile(values, m)[-1], 2 * np.sqrt(m))
        self.assertAlmostEqual(left_matrix_profile(np.r_[np.zeros(m), np.arange(m)], m)[-1], np.sqrt(m))
        # Explicitly attribute the score to the observed impulse, never its start.
        values = np.r_[np.zeros(40), 20., np.zeros(10)]
        self.assertTrue((spectral_residual_online(values, 16)[15:40] == 0).all())
        sr_impulse = spectral_residual_online(values, 16)[40]
        self.assertAlmostEqual(sr_impulse, direct_sr(values, 16)[40], places=12)
        self.assertGreater(sr_impulse, 0)
        self.assertGreater(left_matrix_profile(values, 8)[40], 0)
        unbounded_example = np.random.default_rng(23).normal(size=4000) + np.sin(np.arange(4000) * 2 * np.pi / 48)
        self.assertGreater(np.nanmax(spectral_residual_online(unbounded_example)), 1)

    def test_history_is_count_of_eligible_starts_and_reference_tiles_are_exact(self):
        rng = np.random.default_rng(44)
        values = rng.normal(size=2230)
        # Repeat an old pattern, outside the most recent 10 eligible starts.
        values[-16:] = values[:16]
        self.assertLess(left_matrix_profile(values, 16, history=2200)[-1], 1e-12)
        self.assertGreater(left_matrix_profile(values, 16, history=10)[-1], 0.1)
        result = left_matrix_profile(values, 16, history=2057, exclusion=23)
        for t in [38, 129, 2048, 2229]:
            i = t - 16 + 1
            expected = min(direct_z_distance(values[i:i + 16], values[j:j + 16])
                           for j in range(max(0, i - 23 - 2057 + 1), i - 23 + 1))
            self.assertAlmostEqual(result[t], expected, places=11)

    def test_local_numerics_large_offsets_extreme_scales_and_repeats(self):
        base = np.random.default_rng(55).integers(-100, 100, 160).astype(float)
        base[80:96] = base[16:32]
        for values in [1e14 + base / 8, base * 1e290, base * 1e-290]:
            with self.subTest(scale=values[0]):
                np.testing.assert_allclose(left_matrix_profile(values, 16, 83), direct_left(values, 16, 83), atol=2e-11)
                self.assertEqual(left_matrix_profile(values, 16, 83)[95], 0.0)
                self.assertTrue(np.isfinite(spectral_residual_online(values)[63:]).all())
        # Finite float64 extremes must not overflow FFT, extrapolation or moments.
        extremes = np.tile([np.finfo(float).max, -np.finfo(float).max, 0., 1.], 40)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            self.assertTrue(np.isfinite(spectral_residual_online(extremes)[63:]).all())
            self.assertTrue(np.isfinite(left_matrix_profile(extremes, 16)[31:]).all())
        for tiny in [0., np.nextafter(0., 1.)]:
            np.testing.assert_array_equal(spectral_residual_online(np.full(80, tiny))[63:], 0.)
            np.testing.assert_array_equal(left_matrix_profile(np.full(80, tiny), 16)[31:], 0.)

    def test_invalid_values_and_parameters_are_rejected_without_mutation(self):
        for detector in [spectral_residual_online, lambda x: left_matrix_profile(x, 8)]:
            for values in [[1., np.nan], [np.inf], [-np.inf], [[1., 2.]], 1., [1 + 2j]]:
                with self.subTest(values=values), self.assertRaises(ValueError):
                    detector(values)
            values = np.arange(96., dtype=np.float64)[::-2]
            before = values.copy()
            detector(values)
            np.testing.assert_array_equal(values, before)
        for kwargs in [dict(window=11), dict(window=12.5), dict(window=True), dict(mag_window=0),
                       dict(mag_window=70), dict(score_window=-1), dict(score_window=2.5)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                spectral_residual_online(np.zeros(80), **kwargs)
        for kwargs in [dict(window=7), dict(window=False), dict(window=8.5), dict(window=8, history=0),
                       dict(window=8, history=1.5), dict(window=8, exclusion=7), dict(window=8, exclusion=True)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                left_matrix_profile(np.zeros(80), **kwargs)
        # NumPy integer scalar parameters are accepted, including tiny histories.
        self.assertTrue(np.isfinite(left_matrix_profile(np.arange(32.), np.int64(8), np.int64(1))[-1]))
        np.testing.assert_array_equal(left_matrix_profile(np.arange(32.), 8, history=10**100),
                                      left_matrix_profile(np.arange(32.), 8))


if __name__ == "__main__":
    unittest.main()

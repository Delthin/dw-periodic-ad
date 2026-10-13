"""Scoped unit tests for scripts/prepare_data.py.

These tests cover the mechanical contract only:
  * UTC-coordinate time encoding without local-timezone dependence,
  * rejection of duplicates / out-of-order / non-grid / non-finite series,
  * split boundaries and counts,
  * that a failed run does not materialise processed artefacts.

They do not exercise or claim anything about the (unimplemented) detector.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import prepare_data as prep  # noqa: E402


def make_grid(start_ms: int = prep.ORIGIN_MS, n: int = prep.EXPECTED_ROWS, step: int = prep.STEP_MS) -> np.ndarray:
    return start_ms + np.arange(n, dtype="int64") * step


class UtcEncodingTests(unittest.TestCase):
    def test_initial_epoch_matches_spec(self) -> None:
        got = prep.encode_source_timestamp_to_ms("2014-07-01 00:00:00")
        self.assertEqual(got, 1404172800000)

    def test_matches_manual_utc_timestamp(self) -> None:
        samples = [
            "2014-07-01 00:00:00",
            "2014-09-01 00:00:00",
            "2014-10-16 00:00:00",
            "2015-01-31 23:30:00",
        ]
        encoded = prep.encode_source_timestamps_to_ms(samples)
        for text, got in zip(samples, encoded.tolist()):
            expected = int(
                datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=timezone.utc)
                .timestamp()
                * 1000
            )
            self.assertEqual(got, expected, text)

    def test_independent_of_local_timezone(self) -> None:
        if not hasattr(time, "tzset"):
            self.skipTest("time.tzset not available on this platform")
        original = os.environ.get("TZ")
        try:
            results = {}
            for zone in ("UTC", "America/New_York", "Asia/Shanghai"):
                os.environ["TZ"] = zone
                time.tzset()
                results[zone] = prep.encode_source_timestamp_to_ms("2014-07-01 00:00:00")
            self.assertEqual(set(results.values()), {1404172800000}, results)
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()

    def test_label_timestamps_with_microseconds(self) -> None:
        got = prep.encode_source_timestamp_to_ms("2014-10-30 15:30:00.000000", fmt=None)
        self.assertEqual(got, 1414683000000)


class SeriesValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ts = make_grid(n=48)
        self.values = np.linspace(1.0, 2.0, 48)

    def test_accepts_valid_fixed_grid(self) -> None:
        prep.validate_series(self.ts, self.values, step_ms=prep.STEP_MS, origin_ms=prep.ORIGIN_MS)

    def test_rejects_duplicate_timestamps(self) -> None:
        ts = self.ts.copy()
        ts[10] = ts[9]
        with self.assertRaises(prep.DataValidationError):
            prep.validate_series(ts, self.values, step_ms=prep.STEP_MS)

    def test_rejects_out_of_order(self) -> None:
        ts = self.ts.copy()
        ts[10], ts[11] = ts[11], ts[10]
        with self.assertRaises(prep.DataValidationError):
            prep.validate_series(ts, self.values, step_ms=prep.STEP_MS)

    def test_rejects_non_grid(self) -> None:
        ts = self.ts.copy()
        ts[20] = ts[20] + 1
        with self.assertRaises(prep.DataValidationError):
            prep.validate_series(ts, self.values, step_ms=prep.STEP_MS)

    def test_rejects_non_finite(self) -> None:
        values = self.values.copy()
        values[5] = np.nan
        with self.assertRaises(prep.DataValidationError):
            prep.validate_series(self.ts, values, step_ms=prep.STEP_MS)
        values[5] = np.inf
        with self.assertRaises(prep.DataValidationError):
            prep.validate_series(self.ts, values, step_ms=prep.STEP_MS)

    def test_rejects_empty(self) -> None:
        with self.assertRaises(prep.DataValidationError):
            prep.validate_series(np.array([], dtype="int64"), np.array([]), step_ms=prep.STEP_MS)

    def test_rejects_wrong_origin(self) -> None:
        with self.assertRaises(prep.DataValidationError):
            prep.validate_series(self.ts, self.values, step_ms=prep.STEP_MS, origin_ms=0)


class SplitTests(unittest.TestCase):
    def test_boundary_epochs(self) -> None:
        expected = {
            "train": (1404172800000, 1409529600000),
            "validation": (1409529600000, 1413417600000),
            "test": (1413417600000, 1422748800000),
        }
        for name, src, end in prep.SPLIT_BOUNDARIES:
            self.assertEqual(prep.encode_source_timestamp_to_ms(src), expected[name][0], name)
            self.assertEqual(prep.encode_source_timestamp_to_ms(end), expected[name][1], name)

    def test_counts_over_full_grid(self) -> None:
        ts = make_grid()
        info = prep.build_splits(ts)
        self.assertEqual(info["counts"], {"train": 2976, "validation": 2160, "test": 5184})
        self.assertEqual(info["total"], 10320)
        self.assertEqual(sum(info["counts"].values()), ts.size)

    def test_first_and_last_point_assignment(self) -> None:
        ts = make_grid()
        info = prep.build_splits(ts)
        train_start = info["boundaries"]["train"]["start_ms"]
        test_end = info["boundaries"]["test"]["end_ms_exclusive"]
        self.assertEqual(int(ts[0]), train_start)
        self.assertEqual(int(ts[-1]), prep.EXPECTED_LAST_MS)
        self.assertLess(int(ts[-1]), test_end)

    def test_label_coverage_inclusive(self) -> None:
        ts = make_grid()
        windows = [
            {"index": 0, "start_ms": int(ts[0]), "end_ms": int(ts[1])},
        ]
        coverage = prep.label_coverage(ts, windows)
        self.assertEqual(coverage["train"]["label_points"], 2)
        self.assertEqual(coverage["train"]["label_windows_overlapping"], 1)
        self.assertEqual(coverage["validation"]["label_points"], 0)


class LagCorrelationTests(unittest.TestCase):
    def test_perfect_periodic_signal(self) -> None:
        period = 12
        base = np.tile(np.arange(period, dtype="float64"), 5)
        corr = prep.lag_pearson(base, period)
        self.assertIsNotNone(corr)
        self.assertAlmostEqual(corr, 1.0, places=9)

    def test_constant_signal_returns_none(self) -> None:
        self.assertIsNone(prep.lag_pearson(np.ones(100), 7))

    def test_lag_too_large_returns_none(self) -> None:
        self.assertIsNone(prep.lag_pearson(np.arange(10, dtype="float64"), 50))


class FailureNoArtefactsTests(unittest.TestCase):
    """A failed run must not leave seemingly-successful processed outputs."""

    def _write_raw(self, raw_dir: Path, *, break_series: bool) -> None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        ts = make_grid()
        values = np.arange(ts.size, dtype="int64")
        if break_series:
            ts[10] = ts[9]  # duplicate -> validation failure
        with (raw_dir / "nyc_taxi.csv").open("w", encoding="utf-8") as fh:
            fh.write("timestamp,value\n")
            for ms, val in zip(ts.tolist(), values.tolist()):
                # Format the ms back into source text using the UTC convention.
                text = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                fh.write(f"{text},{val}\n")
        (raw_dir / "combined_windows.json").write_text(
            json.dumps({prep.LABEL_KEY: [["2014-10-30 15:30:00.000000", "2014-11-03 22:30:00.000000"]]}),
            encoding="utf-8",
        )
        (raw_dir / "data_README.md").write_text("dummy readme\n", encoding="utf-8")
        (raw_dir / "LICENSE.txt").write_text("dummy license\n", encoding="utf-8")

    def test_validation_failure_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = prep.repo_paths(root)
            self._write_raw(paths["raw"], break_series=True)
            # Ignore the real SHA expectations so the series check is what fails.
            relaxed = {name: (remote, None) for name, (remote, _) in prep.RAW_FILES.items()}
            with mock.patch.dict(prep.RAW_FILES, relaxed, clear=True):
                with self.assertRaises(prep.DataValidationError):
                    prep.prepare(paths, download=False)
            self.assertFalse((paths["processed"] / "nyc_taxi.csv").exists())
            self.assertFalse((paths["processed"] / "events.json").exists())
            self.assertFalse((paths["results"] / "summary.json").exists())
            self.assertFalse(paths["manifest"].exists())

    def test_hash_mismatch_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = prep.repo_paths(root)
            self._write_raw(paths["raw"], break_series=False)
            with self.assertRaises(prep.PrepError):
                prep.prepare(paths, download=False)
            self.assertFalse((paths["processed"] / "nyc_taxi.csv").exists())
            self.assertFalse(paths["manifest"].exists())


class RepoRootTests(unittest.TestCase):
    def test_finds_marker_upwards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            nested = root / "a" / "b" / "c"
            nested.mkdir(parents=True)
            self.assertEqual(prep.find_repo_root(nested), root.resolve())


if __name__ == "__main__":
    unittest.main()

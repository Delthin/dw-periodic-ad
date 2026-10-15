#!/usr/bin/env python3
"""Mechanical data preparation for the periodic-aware anomaly detection project.

Downloads the NAB nyc_taxi series and its combined window labels (plus the NAB
data README and license) at a fixed commit, preserves the original bytes,
verifies SHA-256, then performs *format-only* conversion:

    data/raw/nyc_taxi.csv            -> data/processed/nyc_taxi.csv
    data/raw/combined_windows.json   -> data/processed/events.json
    (splits)                         -> data/processed/splits.json
    (check artifacts)                -> results/data-check/{summary.json,overview.png}
    (provenance)                     -> configs/data-manifest.json

No filling, smoothing, normalisation, label-based cleaning, or algorithm run is
performed here.  The detector is *not* implemented in this script.

Time convention: the source files carry no timezone.  The naive source
timestamps are encoded as if they were UTC coordinates ("UTC-coordinate
encoding"), so the same text yields the same integer on any machine.  This is a
technical encoding, not a claim that the business time is UTC.  Local system
timezone must never influence the result.

Usage:
    python scripts/prepare_data.py --download   # fetch + verify + convert
    python scripts/prepare_data.py              # verify + convert existing raw files

The entry point locates the repository root from its own location, so it can be
run from any working directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NAB_REPO_URL = "https://github.com/numenta/NAB.git"
NAB_RAW_BASE = "https://raw.githubusercontent.com/numenta/NAB/{commit}/{path}"

# Commit resolved from NAB master during the first acquisition on 2026-10-13.
# --skip-ls-remote reuses this fixed commit for later reproduction.
NAB_REFERENCE_COMMIT = "ea702d75cc2258d9d7dd35ca8e5e2539d71f3140"

STEP_MS = 1_800_000  # 30 minutes
ORIGIN_MS = 1_404_172_800_000  # 2014-07-01 00:00:00 under UTC-coordinate encoding
EXPECTED_ROWS = 10_320
EXPECTED_FIRST_MS = 1_404_172_800_000
EXPECTED_LAST_MS = 1_422_747_000_000

LABEL_KEY = "realKnownCause/nyc_taxi.csv"
EXPECTED_LABEL_WINDOWS = 5

SOURCE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# name -> (remote path, expected sha256 or None)
RAW_FILES = {
    "nyc_taxi.csv": (
        "data/realKnownCause/nyc_taxi.csv",
        "d8fa6f7f0734bf5c8be12c52a94e20a82664c397d9dec4449156bd453d32856d",
    ),
    "combined_windows.json": (
        "labels/combined_windows.json",
        "1e1fbc4601321aad8d0f8b3784c8134299379f68f6c1f7777565f8ffd57ab6b1",
    ),
    "data_README.md": ("data/README.md", None),
    "LICENSE.txt": ("LICENSE.txt", None),
}

# Split boundaries, as naive source-time strings (half-open [start, end)).
SPLIT_BOUNDARIES = [
    ("train", "2014-07-01 00:00:00", "2014-09-01 00:00:00"),
    ("validation", "2014-09-01 00:00:00", "2014-10-16 00:00:00"),
    ("test", "2014-10-16 00:00:00", "2015-02-01 00:00:00"),
]

LAG_DAILY = 48  # 48 * 30 min = 24 h
LAG_WEEKLY = 336  # 336 * 30 min = 7 days


class PrepError(RuntimeError):
    """Raised when preparation cannot proceed or an integrity check fails."""


class DataValidationError(PrepError):
    """Raised when the input series fails a structural check."""


# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------


def find_repo_root(start: Path) -> Path:
    """Walk upwards from *start* until a repository root marker is found."""
    cur = start.resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / ".git").exists() or (candidate / "AGENTS.md").is_file():
            return candidate
    raise PrepError(f"could not locate repository root above {start}")


def repo_paths(root: Path) -> dict[str, Path]:
    return {
        "root": root,
        "raw": root / "data" / "raw",
        "processed": root / "data" / "processed",
        "results": root / "results" / "data-check",
        "manifest": root / "configs" / "data-manifest.json",
    }


# ---------------------------------------------------------------------------
# Small IO helpers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, obj: object) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Time encoding
# ---------------------------------------------------------------------------


def encode_source_timestamps_to_ms(values, fmt: str | None = SOURCE_TIMESTAMP_FORMAT):
    """Encode naive source timestamps as UTC-coordinate epoch milliseconds.

    ``utc=True`` makes pandas interpret the naive strings as UTC, so the local
    system timezone has no effect.  Passing ``fmt=None`` allows pandas to infer
    the format (used for the label file, which carries microseconds).
    """
    dt = pd.to_datetime(list(values), format=fmt, utc=True, errors="raise")
    # Normalise to milliseconds explicitly so the result does not depend on
    # pandas' internal datetime unit (pandas >= 3 uses microseconds).
    if isinstance(dt, pd.Series):
        dt = dt.dt.as_unit("ms")
    else:
        dt = dt.as_unit("ms")
    return dt.astype("int64")


def encode_source_timestamp_to_ms(value: str, fmt: str | None = SOURCE_TIMESTAMP_FORMAT) -> int:
    return int(encode_source_timestamps_to_ms([value], fmt=fmt)[0])


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


def validate_series(
    ts_ms: np.ndarray,
    values: np.ndarray,
    step_ms: int = STEP_MS,
    origin_ms: int | None = None,
) -> None:
    """Reject structurally invalid series.  No repair or resampling is done."""
    ts = np.asarray(ts_ms)
    vals = np.asarray(values)
    if ts.size == 0:
        raise DataValidationError("series is empty")
    if ts.ndim != 1 or vals.ndim != 1 or ts.size != vals.size:
        raise DataValidationError("timestamp and value arrays must be 1-D of equal length")
    if pd.isna(ts).any():
        raise DataValidationError("timestamp contains null/NaT")
    if pd.isna(vals).any():
        raise DataValidationError("value contains empty/null entries")
    if not np.issubdtype(vals.dtype, np.number):
        raise DataValidationError("value column is not numeric")
    if not np.all(np.isfinite(vals)):
        raise DataValidationError("value contains non-finite entries (NaN/Inf)")
    if np.unique(ts).size != ts.size:
        raise DataValidationError("timestamp contains duplicates")
    diffs = np.diff(ts)
    if not np.all(diffs > 0):
        raise DataValidationError("timestamps are not strictly increasing")
    if not np.all(diffs == step_ms):
        bad = np.unique(diffs)
        raise DataValidationError(f"timestamps are not on a fixed {step_ms} ms grid (diffs={bad.tolist()})")
    if origin_ms is not None and int(ts[0]) != origin_ms:
        raise DataValidationError(f"first timestamp {int(ts[0])} != expected origin {origin_ms}")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def resolve_master_commit(repo_url: str = NAB_REPO_URL) -> str:
    """Resolve the current master commit with ``git ls-remote``."""
    try:
        proc = subprocess.run(
            ["git", "ls-remote", repo_url, "refs/heads/master"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise PrepError(f"git executable not found: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise PrepError(f"git ls-remote failed ({exc.returncode}): {exc.stderr.strip()}") from exc
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise PrepError(f"git ls-remote returned no refs for {repo_url}")
    return lines[0].split()[0].strip()


def download_url(url: str, timeout: float = 60.0) -> bytes:
    """Download *url* and return the raw bytes, preserving them unmodified."""
    request = urllib.request.Request(url, headers={"User-Agent": "dw-periodic-ad-data-prep/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise PrepError(f"download failed for {url}: {exc}") from exc


def download_raw_files(raw_dir: Path, commit: str) -> dict[str, dict]:
    """Download all raw files at *commit*.  Returns per-file metadata."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    meta: dict[str, dict] = {}
    for name, (remote_path, expected) in RAW_FILES.items():
        url = NAB_RAW_BASE.format(commit=commit, path=remote_path)
        data = download_url(url)
        dest = raw_dir / name
        atomic_write_bytes(dest, data)
        digest = sha256_bytes(data)
        meta[name] = {
            "url": url,
            "retrieved_utc": utc_now_iso(),
            "sha256": digest,
            "expected_sha256": expected,
            "sha256_match": None if expected is None else (digest == expected),
            "bytes": len(data),
            "path": str(Path("data") / "raw" / name),
        }
    return meta


# ---------------------------------------------------------------------------
# Verification of raw bytes
# ---------------------------------------------------------------------------


def describe_raw_files(raw_dir: Path) -> dict[str, dict]:
    """Hash and describe the locally present raw files."""
    meta: dict[str, dict] = {}
    for name, (remote_path, expected) in RAW_FILES.items():
        path = raw_dir / name
        if not path.is_file():
            raise PrepError(f"missing raw file: {path} (run with --download to fetch it)")
        digest = sha256_file(path)
        meta[name] = {
            "path": str(Path("data") / "raw" / name),
            "sha256": digest,
            "expected_sha256": expected,
            "sha256_match": None if expected is None else (digest == expected),
            "bytes": path.stat().st_size,
        }
    return meta


def check_expected_hashes(file_meta: dict[str, dict]) -> None:
    mismatches = []
    for name, info in file_meta.items():
        expected = info.get("expected_sha256")
        if expected is not None and info.get("sha256") != expected:
            mismatches.append(
                f"  {name}: expected {expected}, got {info.get('sha256')}"
            )
    if mismatches:
        raise PrepError(
            "SHA-256 mismatch for expected files (not overwriting expectations):\n"
            + "\n".join(mismatches)
        )


# ---------------------------------------------------------------------------
# Loading + building artefacts
# ---------------------------------------------------------------------------


def load_nyc_series(raw_path: Path):
    """Load the raw taxi CSV and return (source_timestamps, ts_ms, values)."""
    raw = pd.read_csv(raw_path, dtype={"timestamp": str})
    columns = list(raw.columns)
    if columns[:2] != ["timestamp", "value"]:
        raise DataValidationError(f"unexpected columns in {raw_path.name}: {columns}")
    if raw.shape[1] != 2:
        raise DataValidationError(f"unexpected extra columns in {raw_path.name}: {columns[2:]}")

    source = raw["timestamp"]
    if source.isna().any() or (source.str.strip() == "").any():
        raise DataValidationError("timestamp column contains empty/null entries")

    values = pd.to_numeric(raw["value"], errors="coerce").to_numpy(dtype="float64")
    ts_ms = encode_source_timestamps_to_ms(source, fmt=SOURCE_TIMESTAMP_FORMAT)
    validate_series(ts_ms, values, step_ms=STEP_MS, origin_ms=ORIGIN_MS)

    if ts_ms.size != EXPECTED_ROWS:
        raise DataValidationError(f"expected {EXPECTED_ROWS} rows, found {ts_ms.size}")
    if int(ts_ms[0]) != EXPECTED_FIRST_MS or int(ts_ms[-1]) != EXPECTED_LAST_MS:
        raise DataValidationError(
            f"unexpected span: {int(ts_ms[0])} .. {int(ts_ms[-1])}"
        )
    return source, ts_ms, values


def load_label_windows(labels_path: Path) -> list[tuple[str, str]]:
    """Return the raw (start, end) source-time strings for the taxi series."""
    with labels_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if LABEL_KEY not in data:
        raise DataValidationError(f"label key {LABEL_KEY!r} not found in {labels_path.name}")
    windows = data[LABEL_KEY]
    if not isinstance(windows, list) or len(windows) == 0:
        raise DataValidationError(f"no label windows listed for {LABEL_KEY!r}")
    parsed = []
    for window in windows:
        if not isinstance(window, (list, tuple)) or len(window) != 2:
            raise DataValidationError(f"malformed label window: {window!r}")
        parsed.append((str(window[0]), str(window[1])))
    return parsed


def encode_label_windows(windows: list[tuple[str, str]]) -> list[dict]:
    encoded = []
    for index, (start_src, end_src) in enumerate(windows):
        start_ms = encode_source_timestamp_to_ms(start_src, fmt=None)
        end_ms = encode_source_timestamp_to_ms(end_src, fmt=None)
        if end_ms < start_ms:
            raise DataValidationError(f"label window {index} ends before it starts")
        encoded.append(
            {
                "index": index,
                "start_source": start_src,
                "end_source": end_src,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "boundary_inclusive": True,
            }
        )
    return encoded


def build_splits(ts_ms: np.ndarray, boundaries=SPLIT_BOUNDARIES) -> dict:
    """Count points per split and label coverage.  Boundaries are half-open."""
    ts = np.asarray(ts_ms)
    splits: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for name, start_src, end_src in boundaries:
        start_ms = encode_source_timestamp_to_ms(start_src)
        end_ms = encode_source_timestamp_to_ms(end_src)
        mask = (ts >= start_ms) & (ts < end_ms)
        counts[name] = int(mask.sum())
        splits[name] = {
            "start_source": start_src,
            "end_source_exclusive": end_src,
            "start_ms": start_ms,
            "end_ms_exclusive": end_ms,
            "rows": int(mask.sum()),
        }
    if sum(counts.values()) != ts.size:
        raise DataValidationError(
            f"split boundaries leave {ts.size - sum(counts.values())} point(s) uncovered"
        )
    return {"boundaries": splits, "counts": counts, "total": int(ts.size)}


def label_coverage(ts_ms: np.ndarray, windows: list[dict], boundaries=SPLIT_BOUNDARIES) -> dict:
    """Inclusive coverage of each split by the label windows."""
    ts = np.asarray(ts_ms)
    coverage: dict[str, dict] = {}
    for name, start_src, end_src in boundaries:
        start_ms = encode_source_timestamp_to_ms(start_src)
        end_ms = encode_source_timestamp_to_ms(end_src)
        split_mask = (ts >= start_ms) & (ts < end_ms)
        covered = np.zeros(ts.size, dtype=bool)
        overlapping = 0
        for window in windows:
            if window["start_ms"] < end_ms and window["end_ms"] >= start_ms:
                overlapping += 1
                covered |= (ts >= window["start_ms"]) & (ts <= window["end_ms"])
        coverage[name] = {"label_points": int((split_mask & covered).sum()),
                          "label_windows_overlapping": overlapping}
    return coverage


def lag_pearson(values: np.ndarray, lag: int) -> float | None:
    """Pearson correlation of the series against its *lag*-step lag, or None."""
    vals = np.asarray(values, dtype="float64")
    if lag <= 0 or vals.size <= lag:
        return None
    a = vals[:-lag]
    b = vals[lag:]
    if a.size < 2:
        return None
    if np.std(a) == 0 or np.std(b) == 0:
        return None
    corr = float(np.corrcoef(a, b)[0, 1])
    if not np.isfinite(corr):
        return None
    return corr


def train_lag_correlations(ts_ms: np.ndarray, values: np.ndarray, boundaries=SPLIT_BOUNDARIES) -> dict:
    """Daily/weekly lag correlation computed only on the training segment."""
    ts = np.asarray(ts_ms)
    start_ms = encode_source_timestamp_to_ms(boundaries[0][1])
    end_ms = encode_source_timestamp_to_ms(boundaries[0][2])
    mask = (ts >= start_ms) & (ts < end_ms)
    train_values = np.asarray(values, dtype="float64")[mask]
    return {
        "segment": "train",
        "rows": int(mask.sum()),
        "lag_daily": LAG_DAILY,
        "lag_daily_pearson": lag_pearson(train_values, LAG_DAILY),
        "lag_weekly": LAG_WEEKLY,
        "lag_weekly_pearson": lag_pearson(train_values, LAG_WEEKLY),
    }


# ---------------------------------------------------------------------------
# Artefact writers
# ---------------------------------------------------------------------------


def write_processed_series(path: Path, source: pd.Series, ts_ms: np.ndarray, values: np.ndarray) -> None:
    frame = pd.DataFrame(
        {
            "timestamp_ms": np.asarray(ts_ms, dtype="int64"),
            "source_timestamp": source.astype(str).to_numpy(),
            "value": values,
        }
    )
    buffer: list[str] = [",".join(frame.columns)]
    for ts, src, val in zip(frame["timestamp_ms"], frame["source_timestamp"], frame["value"]):
        buffer.append(f"{int(ts)},{src},{val:.17g}")
    atomic_write_text(path, "\n".join(buffer) + "\n")


def write_overview(path: Path, ts_ms: np.ndarray, values: np.ndarray, windows: list[dict]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    times = pd.to_datetime(pd.Series(ts_ms), unit="ms", utc=True)
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(times, values, linewidth=0.7, color="#1f77b4", label="nyc_taxi value")
    for index, window in enumerate(windows):
        ax.axvspan(
            pd.Timestamp(window["start_ms"], unit="ms", tz="UTC"),
            pd.Timestamp(window["end_ms"], unit="ms", tz="UTC"),
            color="#d62728",
            alpha=0.2,
            label="label window" if index == 0 else None,
        )
    for name, start_src, _ in SPLIT_BOUNDARIES[1:]:
        ax.axvline(
            pd.Timestamp(encode_source_timestamp_to_ms(start_src), unit="ms", tz="UTC"),
            color="black",
            linestyle="--",
            linewidth=0.8,
            label="split boundary" if name == "validation" else None,
        )
    ax.set_title("NAB realKnownCause/nyc_taxi.csv (UTC-coordinate time axis)")
    ax.set_xlabel("source timestamp (encoded as UTC coordinates)")
    ax.set_ylabel("passenger count")
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".png")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        fig.savefig(tmp_path, dpi=120)
        os.replace(tmp_path, path)
    finally:
        plt.close(fig)
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build_manifest(
    *,
    download: bool,
    commit: str,
    commit_source: str,
    file_meta: dict[str, dict],
    summary: dict,
    license_meta: dict,
) -> dict:
    return {
        "dataset": "NAB realKnownCause/nyc_taxi.csv",
        "description": "NYC taxi passenger counts, 30-minute buckets; labels are NAB combined anomaly windows.",
        "generated_utc": utc_now_iso(),
        "download_performed": download,
        "nab": {
            "repository": NAB_REPO_URL,
            "commit": commit,
            "commit_source": commit_source,
            "reference_commit": NAB_REFERENCE_COMMIT,
        },
        "license": license_meta,
        "time_encoding": {
            "convention": "naive source time encoded as UTC-coordinate epoch milliseconds",
            "step_ms": STEP_MS,
            "origin_ms": ORIGIN_MS,
            "origin_source": "2014-07-01 00:00:00",
            "note": "technical encoding; not a claim that business time is UTC; local timezone never used",
        },
        "files": file_meta,
        "series": {
            "rows": summary["rows"],
            "first_timestamp_ms": summary["first_timestamp_ms"],
            "last_timestamp_ms": summary["last_timestamp_ms"],
            "first_source_timestamp": summary["first_source_timestamp"],
            "last_source_timestamp": summary["last_source_timestamp"],
            "step_ms": summary["step_ms"],
        },
        "label_windows": {
            "key": LABEL_KEY,
            "count": len(summary["label_windows"]),
            "windows": summary["label_windows"],
        },
        "checks": summary["checks"],
        "splits": summary["splits"],
        "correlations": summary["correlations"],
        "artefacts": {
            "processed_series": "data/processed/nyc_taxi.csv",
            "events": "data/processed/events.json",
            "splits": "data/processed/splits.json",
            "summary": "results/data-check/summary.json",
            "overview": "results/data-check/overview.png",
        },
    }


def prepare(paths: dict[str, Path], *, download: bool, run_git_ls_remote: bool = True) -> dict:
    raw_dir = paths["raw"]
    file_meta: dict[str, dict] = {}

    commit = NAB_REFERENCE_COMMIT
    commit_source = f"pinned acquisition commit {NAB_REFERENCE_COMMIT}"
    previous_manifest = {}
    if not download and paths["manifest"].is_file():
        previous_manifest = json.loads(paths["manifest"].read_text())
        previous_nab = previous_manifest.get("nab", {})
        commit = previous_nab.get("commit", commit)
        commit_source = previous_nab.get("commit_source", commit_source)

    if download:
        if run_git_ls_remote:
            resolved = resolve_master_commit()
            commit = resolved
            commit_source = f"resolved via `git ls-remote {NAB_REPO_URL} refs/heads/master`"
        downloaded = download_raw_files(raw_dir, commit)
        file_meta.update(downloaded)
    else:
        print("[verify] --download not set: hashing and converting existing raw files")

    # Always re-hash the on-disk bytes so the manifest reflects what was used.
    on_disk = describe_raw_files(raw_dir)
    for name, info in on_disk.items():
        if name in file_meta:
            file_meta[name]["sha256"] = info["sha256"]
            file_meta[name]["bytes"] = info["bytes"]
            file_meta[name]["sha256_match"] = info["sha256_match"]
        else:
            previous = previous_manifest.get("files", {}).get(name, {})
            if previous.get("sha256") == info["sha256"]:
                file_meta[name] = {**previous, **info}
            else:
                file_meta[name] = info

    check_expected_hashes(file_meta)

    raw_nyc = raw_dir / "nyc_taxi.csv"
    raw_labels = raw_dir / "combined_windows.json"
    source, ts_ms, values = load_nyc_series(raw_nyc)
    windows = encode_label_windows(load_label_windows(raw_labels))

    if len(windows) != EXPECTED_LABEL_WINDOWS:
        raise DataValidationError(
            f"expected {EXPECTED_LABEL_WINDOWS} label windows, found {len(windows)}"
        )
    validate_series(ts_ms, values, step_ms=STEP_MS, origin_ms=ORIGIN_MS)

    split_info = build_splits(ts_ms)
    coverage = label_coverage(ts_ms, windows)
    for name, info in split_info["boundaries"].items():
        info.update(coverage[name])
    correlations = train_lag_correlations(ts_ms, values)

    disk_checks = {
        "finite": bool(np.all(np.isfinite(values))),
        "no_empty_values": bool(not pd.isna(values).any()),
        "no_empty_source_timestamps": bool(not source.isna().any()),
        "no_duplicate_timestamps": bool(np.unique(ts_ms).size == ts_ms.size),
        "strictly_increasing": bool(np.all(np.diff(ts_ms) > 0)),
        "fixed_grid": bool(np.all(np.diff(ts_ms) == STEP_MS)),
        "row_count": int(ts_ms.size),
        "step_ms": STEP_MS,
    }

    summary = {
        "generated_utc": utc_now_iso(),
        "dataset": "NAB realKnownCause/nyc_taxi.csv",
        "rows": int(ts_ms.size),
        "step_ms": STEP_MS,
        "first_timestamp_ms": int(ts_ms[0]),
        "last_timestamp_ms": int(ts_ms[-1]),
        "first_source_timestamp": str(source.iloc[0]),
        "last_source_timestamp": str(source.iloc[-1]),
        "checks": disk_checks,
        "splits": split_info["boundaries"],
        "split_counts": split_info["counts"],
        "correlations": correlations,
        "label_windows": windows,
    }

    license_text = (raw_dir / "LICENSE.txt").read_text(encoding="utf-8", errors="replace")
    license_meta = {
        "name": "Numenta Anomaly Benchmark (NAB) license, MIT-style",
        "spdx_id": "MIT",
        "source_url": file_meta["LICENSE.txt"].get("url")
        or NAB_RAW_BASE.format(commit=commit, path="LICENSE.txt"),
        "copyright": "Copyright 2014-2024 Numenta Inc.",
        "sha256": file_meta["LICENSE.txt"]["sha256"],
        "retrieved_utc": file_meta["LICENSE.txt"].get("retrieved_utc"),
        "data_readme_url": file_meta["data_README.md"].get("url")
        or NAB_RAW_BASE.format(commit=commit, path="data/README.md"),
        "license_text_head": license_text.strip().splitlines()[0] if license_text.strip() else "",
    }

    manifest = build_manifest(
        download=download,
        commit=commit,
        commit_source=commit_source,
        file_meta=file_meta,
        summary=summary,
        license_meta=license_meta,
    )

    # All checks have passed: now materialise the artefacts.
    processed_dir = paths["processed"]
    results_dir = paths["results"]
    processed_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    write_processed_series(processed_dir / "nyc_taxi.csv", source, ts_ms, values)
    atomic_write_json(
        processed_dir / "events.json",
        {
            "source": "NAB labels/combined_windows.json",
            "key": LABEL_KEY,
            "time_encoding": "UTC-coordinate epoch milliseconds",
            "count": len(windows),
            "windows": windows,
        },
    )
    atomic_write_json(processed_dir / "splits.json", split_info)
    atomic_write_json(results_dir / "summary.json", summary)
    write_overview(results_dir / "overview.png", ts_ms, values, windows)
    atomic_write_json(paths["manifest"], manifest)

    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--download",
        action="store_true",
        help="download the NAB inputs at the current master commit before verifying/converting",
    )
    parser.add_argument(
        "--skip-ls-remote",
        action="store_true",
        help="with --download, use the pinned acquisition commit instead of calling git ls-remote",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="override the auto-detected repository root (mainly for tests)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.repo_root is not None:
        root = args.repo_root.resolve()
    else:
        root = find_repo_root(Path(__file__).resolve().parent)
    paths = repo_paths(root)
    print(f"[repo] root = {root}")
    try:
        summary = prepare(paths, download=args.download, run_git_ls_remote=not args.skip_ls_remote)
    except PrepError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    print(
        "[ok] rows={rows} splits={counts} label_windows={n}".format(
            rows=summary["rows"],
            counts=summary["split_counts"],
            n=len(summary["label_windows"]),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

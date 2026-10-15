#!/usr/bin/env python3
"""Mechanical preparation of the NAB supplement series.

This is a self-contained data-preparation helper.  It performs *format-only*
conversion and candidate screening; it implements no detector and produces no
detection scores.

Candidate set
-------------
Every ``*.csv`` under the configured NAB candidate directories at the pinned
commit, ASCII-ascending by relative path.  Two directories are screened:

    data/realAdExchange/   (6 csv)
    data/realTraffic/      (7 csv)

The realAdExchange screen is kept in the manifest as history even though all of
its series are rejected; it is never relaxed.  realTraffic is screened with the
identical rules and thresholds.

Pipeline
--------
1. List candidates via the GitHub API on ``--download`` (and reuse the manifest
   afterwards).
2. Download the raw bytes and record SHA-256 (bytes are never rewritten).
3. Per candidate derive the actual sampling interval as the modal positive
   timestamp delta, require it to divide 86,400,000 ms exactly, and take
   ``period_points = 86_400_000 // step_ms`` for the fixed *daily* period.
4. Screen *without using any labels*:
   - whole series: strictly increasing, no duplicate timestamps, fixed interval
     (every consecutive delta equals the derived step), finite values;
   - training prefix only (``train_end = floor(n * 0.4)``): at least 8 cycles and
     the lag-``P`` Pearson correlation >= 0.5.
5. Select at most the first 3 passers in ASCII order.  If fewer than 3 pass, all
   passers are used and the thresholds are *not* relaxed.

Time convention: the source timestamps carry no timezone.  They are encoded as
if they were UTC coordinates ("UTC-coordinate encoding"), so identical text
yields identical integers on any machine.  This is a technical encoding, not a
claim that the business time is UTC.  The local system timezone is never used.
No filling, smoothing, normalisation, resampling, sorting repair or
label-based cleaning is performed.

Splits (half-open, boundaries from the row count only -- labels never used):
    train      [0, floor(0.4 n))
    validation [floor(0.4 n), floor(0.6 n))
    test       [floor(0.6 n), n)

Outputs (relative to ``--root``, default = the script's parent directory):
    data/raw/supplement/<dir>/<name>.csv                    (original bytes)
    data/processed/supplement/<stem>.csv                    (timestamp_ms,source_timestamp,value)
    data/processed/supplement/<stem>.events.json            (list of label windows)
    data/processed/supplement/<stem>.splits.json            ({"boundaries": {...}})
    configs/supplement-manifest.json                        (provenance + screen)

Labels come from the read-only copy ``data/raw/combined_windows.json`` (key =
the candidate's path relative to ``data/``); this script never modifies it.

Usage:
    python scripts/prepare_supplement.py --download        # fetch + screen + convert
    python scripts/prepare_supplement.py                   # re-verify + convert locally
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
NAB_API_CONTENTS = (
    "https://api.github.com/repos/numenta/NAB/contents/{path}?ref={commit}"
)

# Commit pinned by the project (same as the primary nyc_taxi acquisition).
NAB_REFERENCE_COMMIT = "ea702d75cc2258d9d7dd35ca8e5e2539d71f3140"

# Candidate directories, screened in this order.  realAdExchange results are
# retained as history even though all of its series are rejected.
CANDIDATE_DIRS = ("data/realAdExchange", "data/realTraffic")

DAY_MS = 86_400_000  # 24 h expressed in ms (fixed daily period)

TRAIN_FRACTION = 0.4
VAL_END_FRACTION = 0.6
MIN_TRAIN_CYCLES = 8
MIN_LAG_PEARSON = 0.5
SELECT_COUNT = 3

SOURCE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

USER_AGENT = "dw-periodic-supplement-prep/1.0"

REASONS = {
    "ok": "passes all screening rules",
    "bad_columns": "expected exactly the columns [timestamp, value]",
    "empty": "series is empty",
    "not_strictly_increasing": "timestamps are not strictly increasing",
    "has_duplicate_timestamps": "timestamp column contains duplicate values",
    "no_step_detected": "no positive sampling interval could be derived",
    "not_fixed_interval": "consecutive deltas are not all equal to the derived sampling interval",
    "non_finite_values": "value column contains NaN/Inf or non-numeric entries",
    "step_does_not_divide_day": "sampling interval does not divide 86,400,000 ms exactly",
    "train_cycles_below_min": "training prefix covers fewer than 8 daily cycles",
    "train_lag_undefined": "training lag-P Pearson correlation is undefined (zero variance)",
    "train_lag_below_min": "training lag-P Pearson correlation is below 0.5",
    "not_in_first_three_passers": "passes screening but is not among the first 3 in ASCII order",
}


class PrepError(RuntimeError):
    """Raised when preparation cannot proceed or an integrity check fails."""


# ---------------------------------------------------------------------------
# Small helpers
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


def encode_source_timestamps_to_ms(values) -> np.ndarray:
    """Encode naive source timestamps as UTC-coordinate epoch milliseconds."""
    dt = pd.to_datetime(list(values), format=SOURCE_TIMESTAMP_FORMAT, utc=True, errors="raise")
    if isinstance(dt, pd.Series):
        dt = dt.dt.as_unit("ms")
    else:
        dt = dt.as_unit("ms")
    return dt.astype("int64").to_numpy()


def workspace_paths(root: Path) -> dict:
    return {
        "root": root,
        "raw_dir": root / "data" / "raw" / "supplement",
        "processed_dir": root / "data" / "processed" / "supplement",
        "manifest": root / "configs" / "supplement-manifest.json",
        "labels": root / "data" / "raw" / "combined_windows.json",
    }


def stem_for(relative_path: str) -> str:
    """Unique output stem for a candidate (keeps the directory namespace)."""
    return Path(relative_path).with_suffix("").as_posix().replace("/", "__")


# ---------------------------------------------------------------------------
# Candidate discovery / download
# ---------------------------------------------------------------------------


def http_get(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise PrepError(f"network request failed for {url}: {exc}") from exc


def list_candidates(commit: str) -> list[str]:
    """List candidate paths (relative to ``data/``) via the GitHub API."""
    found: list[str] = []
    for directory in CANDIDATE_DIRS:
        url = NAB_API_CONTENTS.format(path=directory, commit=commit)
        payload = http_get(url)
        try:
            entries = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise PrepError(f"GitHub API did not return JSON for {url}: {exc}") from exc
        if not isinstance(entries, list):
            raise PrepError(f"GitHub API listing for {directory} is not a list: {payload[:200]!r}")
        names = [
            entry["name"]
            for entry in entries
            if entry.get("type") == "file" and entry.get("name", "").endswith(".csv")
        ]
        if not names:
            raise PrepError(f"no *.csv candidates found in {directory} at {commit}")
        rel_dir = directory.split("/", 1)[1]
        found.extend(f"{rel_dir}/{name}" for name in names)
    return sorted(found)  # ASCII ascending by relative path


def download_candidates(raw_dir: Path, commit: str, rel_paths: list[str]) -> dict[str, dict]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    meta: dict[str, dict] = {}
    for rel_path in rel_paths:
        data_path = f"data/{rel_path}"
        url = NAB_RAW_BASE.format(commit=commit, path=data_path)
        data = http_get(url)
        dest = raw_dir / rel_path
        atomic_write_bytes(dest, data)
        meta[rel_path] = {
            "url": url,
            "commit": commit,
            "retrieved_utc": utc_now_iso(),
            "sha256": sha256_bytes(data),
            "bytes": len(data),
            "raw_path": str(Path("data/raw/supplement") / rel_path),
        }
    return meta


# ---------------------------------------------------------------------------
# Loading + structural screening
# ---------------------------------------------------------------------------


def load_candidate(path: Path):
    """Return (source_texts, ts_ms, value_texts, value_floats) or raise PrepError."""
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    columns = list(frame.columns)
    if columns != ["timestamp", "value"]:
        raise PrepError(f"bad_columns: {path.name} columns={columns!r}")
    if frame.shape[0] == 0:
        raise PrepError(f"empty: {path.name}")
    source = frame["timestamp"]
    value_texts = frame["value"].to_numpy()
    ts_ms = encode_source_timestamps_to_ms(source)
    values = pd.to_numeric(pd.Series(value_texts), errors="coerce").to_numpy(dtype="float64")
    return source.astype(str).to_numpy(), ts_ms, value_texts, values


def derive_step_ms(ts_ms: np.ndarray) -> int | None:
    """Modal positive delta = the actual sampling interval, or None."""
    diffs = np.diff(np.asarray(ts_ms, dtype="int64"))
    positive = diffs[diffs > 0]
    if positive.size == 0:
        return None
    values, counts = np.unique(positive, return_counts=True)
    return int(values[int(np.argmax(counts))])


def screen_candidate(relative_path: str, source, ts_ms, value_texts, values) -> dict:
    """Compute structural checks and the training lag correlation (no labels)."""
    ts = np.asarray(ts_ms, dtype="int64")
    vals = np.asarray(values, dtype="float64")
    n = int(ts.size)

    diffs = np.diff(ts)
    finite = bool(np.all(np.isfinite(vals))) and not bool(pd.isna(vals).any())
    strict = bool(np.all(diffs > 0)) if diffs.size else True
    no_dup = int(np.unique(ts).size) == n
    step_ms = derive_step_ms(ts)
    # "Fixed interval": every consecutive delta equals the derived step.  This is
    # the strict criterion; it is not relaxed and gaps are not tolerated here.
    fixed = step_ms is not None and bool(np.all(diffs == step_ms))
    divides = step_ms is not None and (DAY_MS % step_ms == 0)
    # Diagnostic only: are all deltas multiples of the base step?
    on_base_grid = (
        bool(np.all(diffs % step_ms == 0))
        if (step_ms is not None and diffs.size)
        else (step_ms is not None)
    )

    period_points = (DAY_MS // step_ms) if divides else None

    train_end = int(n * TRAIN_FRACTION)
    val_end = int(n * VAL_END_FRACTION)

    structural_ok = finite and strict and no_dup and fixed and divides

    train = {
        "rows": train_end,
        "end_ms_exclusive": int(ts[train_end]) if train_end < n else (int(ts[-1]) + (step_ms or 0)),
    }
    cycles = (train_end / period_points) if period_points else None
    lag_pearson = None
    lag_defined = False
    if period_points and train_end > period_points:
        a = vals[: train_end - period_points]
        b = vals[period_points : train_end]
        if a.size >= 2 and np.std(a) > 0 and np.std(b) > 0:
            corr = float(np.corrcoef(a, b)[0, 1])
            if np.isfinite(corr):
                lag_pearson = corr
                lag_defined = True

    reasons: list[str] = []
    if not finite:
        reasons.append("non_finite_values")
    if not strict:
        reasons.append("not_strictly_increasing")
    if not no_dup:
        reasons.append("has_duplicate_timestamps")
    if step_ms is None:
        reasons.append("no_step_detected")
    elif not fixed:
        reasons.append("not_fixed_interval")
    if step_ms is not None and not divides:
        reasons.append("step_does_not_divide_day")
    if structural_ok:
        if cycles is None or cycles < MIN_TRAIN_CYCLES:
            reasons.append("train_cycles_below_min")
        if not lag_defined:
            reasons.append("train_lag_undefined")
        elif lag_pearson < MIN_LAG_PEARSON:
            reasons.append("train_lag_below_min")

    passed = len(reasons) == 0

    diff_values, diff_counts = (
        np.unique(diffs, return_counts=True) if diffs.size else (np.array([]), np.array([]))
    )
    return {
        "name": Path(relative_path).name,
        "relative_path": relative_path,
        "directory": str(Path(relative_path).parent),
        "stem": stem_for(relative_path),
        "n": n,
        "value_column": {"raw_text_preserved": True, "parsed_finite": finite},
        "timestamps": {
            "first_ms": int(ts[0]),
            "last_ms": int(ts[-1]),
            "first_source": str(source[0]),
            "last_source": str(source[-1]),
            "strictly_increasing": strict,
            "duplicate_count": int(n - np.unique(ts).size),
            "observed_diffs_ms": {int(v): int(c) for v, c in zip(diff_values.tolist(), diff_counts.tolist())},
        },
        "step_ms": step_ms,
        "period_points": period_points,
        "period_hours": 24.0 if period_points else None,
        "checks": {
            "finite": finite,
            "strictly_increasing": strict,
            "no_duplicate_timestamps": no_dup,
            "fixed_interval": fixed,
            "on_base_grid_diagnostic": on_base_grid,
            "step_divides_day": divides,
        },
        "train": {
            **train,
            "cycles": cycles,
            "lag_points": period_points,
            "lag_pearson": lag_pearson,
            "lag_pearson_defined": lag_defined,
            "lag_pearson_definition": "Pearson(value[:train_end-P], value[P:train_end]) on the index grid",
        },
        "passed": passed,
        "reasons": reasons if reasons else ["ok"],
        "reason_text": [REASONS[r] for r in (reasons if reasons else ["ok"])],
    }


# ---------------------------------------------------------------------------
# Label windows
# ---------------------------------------------------------------------------


def load_label_windows(labels_path: Path, label_key: str) -> list[tuple[str, str]]:
    if not labels_path.is_file():
        raise PrepError(f"label file not found: {labels_path}")
    with labels_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if label_key not in data:
        raise PrepError(f"label key {label_key!r} not found in {labels_path.name}")
    windows = data[label_key]
    if not isinstance(windows, list) or not windows:
        raise PrepError(f"no label windows for {label_key!r}")
    parsed = []
    for window in windows:
        if not isinstance(window, (list, tuple)) or len(window) != 2:
            raise PrepError(f"malformed label window: {window!r}")
        parsed.append((str(window[0]), str(window[1])))
    return parsed


def encode_label_windows(windows: list[tuple[str, str]]) -> list[dict]:
    out = []
    for index, (start_src, end_src) in enumerate(windows):
        start_ms = int(pd.to_datetime([start_src], format=None, utc=True).as_unit("ms").astype("int64")[0])
        end_ms = int(pd.to_datetime([end_src], format=None, utc=True).as_unit("ms").astype("int64")[0])
        if end_ms < start_ms:
            raise PrepError(f"label window {index} ends before it starts")
        out.append(
            {
                "index": index,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "start_source": start_src,
                "end_source": end_src,
                "boundary_inclusive": True,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_processed_series(path: Path, ts_ms: np.ndarray, source, value_texts) -> None:
    lines = ["timestamp_ms,source_timestamp,value"]
    for ts, src, val in zip(ts_ms, source, value_texts):
        lines.append(f"{int(ts)},{src},{val}")
    atomic_write_text(path, "\n".join(lines) + "\n")


def build_splits(ts_ms: np.ndarray, step_ms: int) -> dict:
    ts = np.asarray(ts_ms, dtype="int64")
    n = int(ts.size)
    train_end = int(n * TRAIN_FRACTION)
    val_end = int(n * VAL_END_FRACTION)
    grid_end = int(ts[-1]) + step_ms

    def as_source(ms: int) -> str:
        return pd.Timestamp(ms, unit="ms", tz="UTC").strftime(SOURCE_TIMESTAMP_FORMAT)

    def boundary(name: str, lo: int, hi: int) -> dict:
        start_ms = int(ts[lo])
        end_ms_exclusive = int(ts[hi]) if hi < n else grid_end
        return {
            "name": name,
            "start_index": lo,
            "end_index_exclusive": hi,
            "start_source": as_source(start_ms),
            "end_source_exclusive": as_source(end_ms_exclusive),
            "start_ms": start_ms,
            "end_ms_exclusive": end_ms_exclusive,
            "rows": hi - lo,
        }

    boundaries = {
        "train": boundary("train", 0, train_end),
        "validation": boundary("validation", train_end, val_end),
        "test": boundary("test", val_end, n),
    }
    return {
        "boundaries": boundaries,
        "counts": {k: v["rows"] for k, v in boundaries.items()},
        "total": n,
        "train_fraction": TRAIN_FRACTION,
        "val_end_fraction": VAL_END_FRACTION,
        "grid_end_ms": grid_end,
    }


# ---------------------------------------------------------------------------
# Verification of written artefacts
# ---------------------------------------------------------------------------


def verify_outputs(paths: dict, selected: list[dict], raw_meta: dict[str, dict]) -> dict:
    report: dict = {"files": {}, "splits": {}, "ok": True}

    for item in selected:
        rel = item["relative_path"]
        stem = item["stem"]
        raw = paths["raw_dir"] / rel
        got = sha256_file(raw)
        exp = raw_meta[rel]["sha256"]
        if got != exp:
            raise PrepError(f"raw SHA mismatch for {rel}: {got} != {exp}")

        proc = paths["processed_dir"] / f"{stem}.csv"
        frame = pd.read_csv(proc, dtype={"source_timestamp": str, "value": str})
        ts = frame["timestamp_ms"].to_numpy(dtype="int64")
        vals = pd.to_numeric(frame["value"], errors="coerce").to_numpy(dtype="float64")
        rows_ok = len(frame) == item["rows"]
        grid_ok = bool(np.all(np.diff(ts) == item["step_ms"]))
        finite_ok = bool(np.all(np.isfinite(vals)))
        if not (rows_ok and grid_ok and finite_ok):
            raise PrepError(f"processed verification failed for {proc.name}")

        splits = json.loads((paths["processed_dir"] / f"{stem}.splits.json").read_text())
        b = splits["boundaries"]
        spans = sorted((v["start_index"], v["end_index_exclusive"], k) for k, v in b.items())
        covered = 0
        prev_end = 0
        for lo, hi, _ in spans:
            if lo != prev_end:
                raise PrepError(f"splits not contiguous for {stem}: gap/overlap at {lo}")
            covered += hi - lo
            prev_end = hi
        if covered != len(frame) or prev_end != len(frame):
            raise PrepError(f"splits do not cover all rows for {stem}")
        test = b["test"]
        n_test = int(((ts >= test["start_ms"]) & (ts < test["end_ms_exclusive"])).sum())
        if n_test != test["rows"]:
            raise PrepError(f"test split ms mismatch for {stem}: {n_test} != {test['rows']}")

        events = json.loads((paths["processed_dir"] / f"{stem}.events.json").read_text())
        if not isinstance(events, list) or not events:
            raise PrepError(f"events file empty for {stem}")
        for w in events:
            for key in ("index", "start_ms", "end_ms", "start_source", "end_source", "boundary_inclusive"):
                if key not in w:
                    raise PrepError(f"event window missing {key!r} for {stem}")

        report["files"][stem] = {
            "relative_path": rel,
            "raw_sha256": got,
            "raw_sha256_matches": True,
            "rows": len(frame),
            "rows_match_manifest": rows_ok,
            "grid_ok": grid_ok,
            "finite_ok": finite_ok,
            "events": len(events),
        }
        report["splits"][stem] = {
            "counts": splits["counts"],
            "total": splits["total"],
            "contiguous_full_cover": True,
            "test_rows_by_ms": n_test,
        }

    return report


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def prepare(root: Path, download: bool) -> dict:
    paths = workspace_paths(root)
    committed = NAB_REFERENCE_COMMIT

    previous = {}
    if paths["manifest"].is_file():
        previous = json.loads(paths["manifest"].read_text())
    prev_commit = previous.get("nab", {}).get("commit")
    if prev_commit and prev_commit != committed:
        raise PrepError(
            f"existing manifest commit {prev_commit} differs from pinned {committed}; "
            "refusing to silently switch commits"
        )

    raw_meta: dict[str, dict] = {}
    if download:
        rel_paths = list_candidates(committed)
        fetched = download_candidates(paths["raw_dir"], committed, rel_paths)
        for rel, info in fetched.items():
            raw_meta[rel] = {
                "url": info["url"],
                "commit": info["commit"],
                "retrieved_utc": info["retrieved_utc"],
                "sha256": info["sha256"],
                "expected_sha256": info["sha256"],
                "sha256_match": True,
                "bytes": info["bytes"],
                "raw_path": info["raw_path"],
            }
    else:
        prev_candidates = previous.get("candidates", [])
        if not prev_candidates:
            raise PrepError("no local candidate manifest; run once with --download to pin the candidate list")
        for cand in prev_candidates:
            rel = cand.get("relative_path") or cand["name"]
            path = paths["raw_dir"] / rel
            if not path.is_file():
                raise PrepError(f"missing raw candidate {path} (run with --download)")
            digest = sha256_file(path)
            exp = cand.get("raw_sha256")
            raw_meta[rel] = {
                "url": cand.get("url"),
                "commit": prev_commit or committed,
                "retrieved_utc": cand.get("retrieved_utc"),
                "sha256": digest,
                "expected_sha256": exp,
                "sha256_match": (digest == exp) if exp else None,
                "bytes": path.stat().st_size,
                "raw_path": cand.get("raw_path") or str(Path("data/raw/supplement") / rel),
            }
        for rel, info in raw_meta.items():
            if info["expected_sha256"] and info["sha256"] != info["expected_sha256"]:
                raise PrepError(f"raw SHA mismatch for {rel}: {info['sha256']} != {info['expected_sha256']}")

    rel_paths = sorted(raw_meta.keys())  # ASCII ascending by relative path

    screened: list[dict] = []
    for rel in rel_paths:
        raw_path = paths["raw_dir"] / rel
        try:
            source, ts_ms, value_texts, values = load_candidate(raw_path)
        except PrepError as exc:
            screened.append(
                {
                    "name": Path(rel).name,
                    "relative_path": rel,
                    "directory": str(Path(rel).parent),
                    "stem": stem_for(rel),
                    "passed": False,
                    "reasons": [str(exc).split(":", 1)[0]],
                    "reason_text": [str(exc)],
                    "n": None,
                }
            )
            continue
        rec = screen_candidate(rel, source, ts_ms, value_texts, values)
        rec["source_file"] = rel
        rec["raw_sha256"] = raw_meta[rel]["sha256"]
        rec["url"] = raw_meta[rel]["url"]
        screened.append(rec)

    passers = [rec for rec in screened if rec["passed"]]
    selected_recs = passers[:SELECT_COUNT]
    selected_stems = {rec["stem"] for rec in selected_recs}
    for rec in screened:
        if rec["passed"] and rec["stem"] not in selected_stems:
            rec["selected"] = False
            rec["reasons"] = ["not_in_first_three_passers"]
            rec["reason_text"] = [REASONS["not_in_first_three_passers"]]
        else:
            rec["selected"] = rec["stem"] in selected_stems

    paths["processed_dir"].mkdir(parents=True, exist_ok=True)
    selected_items: list[dict] = []
    for rec in selected_recs:
        stem = rec["stem"]
        rel = rec["source_file"]
        source, ts_ms, value_texts, values = load_candidate(paths["raw_dir"] / rel)
        step_ms = rec["step_ms"]

        proc_path = paths["processed_dir"] / f"{stem}.csv"
        write_processed_series(proc_path, ts_ms, source, value_texts)

        splits = build_splits(ts_ms, step_ms)
        splits_path = paths["processed_dir"] / f"{stem}.splits.json"
        atomic_write_json(splits_path, splits)

        label_key = rel  # key in combined_windows.json == path relative to data/
        windows = encode_label_windows(load_label_windows(paths["labels"], label_key))
        events_path = paths["processed_dir"] / f"{stem}.events.json"
        atomic_write_json(events_path, windows)

        selected_items.append(
            {
                "stem": stem,
                "source_file": rel,
                "relative_path": rel,
                "label_key": label_key,
                "period": rec["period_points"],
                "period_hours": rec["period_hours"],
                "step_ms": step_ms,
                "rows": rec["n"],
                "first_ms": int(ts_ms[0]),
                "last_ms": int(ts_ms[-1]),
                "first_source": str(source[0]),
                "last_source": str(source[-1]),
                "raw_sha256": raw_meta[rel]["sha256"],
                "url": raw_meta[rel]["url"],
                "processed_csv": str(proc_path.relative_to(root)),
                "splits_json": str(splits_path.relative_to(root)),
                "events_json": str(events_path.relative_to(root)),
                "splits": splits["counts"],
                "train": {
                    "rows": rec["train"]["rows"],
                    "cycles": rec["train"]["cycles"],
                    "lag_points": rec["train"]["lag_points"],
                    "lag_pearson": rec["train"]["lag_pearson"],
                },
                "label_windows": len(windows),
                "screening": {"checks": rec["checks"], "reasons": rec["reasons"]},
            }
        )

    verify = verify_outputs(paths, selected_items, raw_meta)

    manifest = {
        "dataset": "NAB supplement candidates (realAdExchange + realTraffic)",
        "description": (
            "Ad-exchange and traffic series screened for a fixed daily period on the "
            "first-40% training prefix; no labels used during screening."
        ),
        "generated_utc": utc_now_iso(),
        "download_performed": download,
        "nab": {
            "repository": NAB_REPO_URL,
            "commit": committed,
            "commit_source": "pinned project commit (same as primary nyc_taxi acquisition)",
            "reference_commit": NAB_REFERENCE_COMMIT,
        },
        "time_encoding": {
            "convention": "naive source time encoded as UTC-coordinate epoch milliseconds",
            "note": "technical encoding; not a claim that business time is UTC; local timezone never used",
        },
        "candidate_dirs": list(CANDIDATE_DIRS),
        "selection": {
            "order": "ASCII ascending by candidate path relative to data/",
            "candidate_set": "every *.csv under the candidate dirs at the pinned commit",
            "fixed_period": "daily 24 h, converted to period_points = 86,400,000 // step_ms",
            "step_conversion": "actual (modal) sampling interval must divide 86,400,000 ms exactly",
            "label_usage_in_screening": "none",
            "train_segment": "index prefix [0, floor(n * 0.4)), boundaries from row count only",
            "min_train_cycles": MIN_TRAIN_CYCLES,
            "min_lag_pearson": MIN_LAG_PEARSON,
            "select_count": SELECT_COUNT,
            "thresholds_relaxed": False,
            "split_fractions": {"train": TRAIN_FRACTION, "val_end": VAL_END_FRACTION},
        },
        "labels": {
            "source": "data/raw/combined_windows.json (read-only copy)",
            "key_rule": "candidate path relative to data/",
            "boundary_inclusive": True,
        },
        "candidates": screened,
        "selected": selected_items,
        "passthrough_checks": verify,
    }

    atomic_write_json(paths["manifest"], manifest)
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--root", type=Path, default=default_root,
                        help="workspace root (default: the script's parent directory)")
    parser.add_argument("--download", action="store_true",
                        help="list candidates via the GitHub API and fetch raw bytes at the pinned commit")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    try:
        manifest = prepare(root, download=args.download)
    except PrepError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    selected = [item["stem"] for item in manifest["selected"]]
    passed = [c["relative_path"] for c in manifest["candidates"] if c.get("passed")]
    rejected = [c["relative_path"] for c in manifest["candidates"] if not c.get("passed")]
    print(f"root       : {root}")
    print(f"commit     : {manifest['nab']['commit']}")
    print(f"candidates : {len(manifest['candidates'])}  passed={len(passed)}  selected={len(selected)}")
    print(f"selected   : {', '.join(selected) if selected else '(none)'}")
    print(f"rejected   : {', '.join(rejected) if rejected else '(none)'}")
    print(f"manifest   : {workspace_paths(root)['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

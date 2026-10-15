"""Causal event evaluation; labels never participate in detector updates."""
from __future__ import annotations

import numpy as np
import pandas as pd

DAY_MS = 86_400_000


def merge_alerts(timestamps, flags, gap_ms=3_600_000):
    """Merge on the complete scored prefix, before applying evaluation bounds."""
    ts = np.asarray(timestamps, dtype=np.int64)
    mask = np.asarray(flags, dtype=bool)
    if ts.ndim != 1 or ts.shape != mask.shape or np.any(np.diff(ts) <= 0):
        raise ValueError("timestamps must be unique, ascending, and match flags")
    if gap_ms < 0:
        raise ValueError("negative merge gap")
    events = []
    for t in ts[mask]:
        t = int(t)
        if events and t - events[-1]["end_ms"] <= gap_ms:
            events[-1]["end_ms"] = t
            events[-1]["points"] += 1
        else:
            events.append({"start_ms": t, "end_ms": t, "points": 1})
    return events


def evaluate(timestamps, flags, windows, start_ms, end_ms, step_ms, gap_ms=3_600_000):
    """One TP per closed label window; duplicate and outside events are FP.

    Exposure is counted in fixed-grid sample cells. A timestamp in a closed
    label window contributes one step to labelled exposure, including its last
    point. This is not the official NAB score and does not use point adjustment.
    """
    if step_ms <= 0 or end_ms <= start_ms:
        raise ValueError("invalid evaluation interval")
    ts = np.asarray(timestamps, dtype=np.int64)
    flags = np.asarray(flags, dtype=bool)
    relevant = sorted([dict(w) for w in windows
                       if w["start_ms"] < end_ms and w["end_ms"] >= start_ms],
                      key=lambda w: w["start_ms"])
    for i, w in enumerate(relevant):
        if w["end_ms"] < w["start_ms"]:
            raise ValueError("reversed window")
        if i and w["start_ms"] <= relevant[i - 1]["end_ms"]:
            raise ValueError("overlapping closed windows")
    full_events = merge_alerts(ts, flags, gap_ms)
    events = [dict(e) for e in full_events if start_ms <= e["start_ms"] < end_ms]
    matches = [None] * len(relevant)
    duplicates = outside = 0
    for event in events:
        index = next((i for i, w in enumerate(relevant)
                      if w["start_ms"] <= event["start_ms"] <= w["end_ms"]), None)
        event["window_index"] = index
        if index is None:
            outside += 1
            event["classification"] = "outside_fp"
        elif matches[index] is None:
            matches[index] = event["start_ms"]
            event["classification"] = "tp"
        else:
            duplicates += 1
            event["classification"] = "duplicate_fp"
    tp = sum(t is not None for t in matches)
    fp, fn = outside + duplicates, len(matches) - tp
    grid = np.arange(start_ms, end_ms, step_ms, dtype=np.int64)
    member = np.zeros(len(grid), dtype=bool)
    for w in relevant:
        member |= (grid >= w["start_ms"]) & (grid <= w["end_ms"])
    outside_days = float(np.count_nonzero(~member) * step_ms / DAY_MS)
    selected = (ts >= start_ms) & (ts < end_ms)
    truth = np.zeros(len(ts), dtype=bool)
    for w in relevant:
        truth |= (ts >= w["start_ms"]) & (ts <= w["end_ms"])
    point_tp = int(np.count_nonzero(selected & flags & truth))
    point_fp = int(np.count_nonzero(selected & flags & ~truth))
    point_fn = int(np.count_nonzero(selected & ~flags & truth))
    div = lambda a, b: float(a / b) if b else 0.0
    detail = [{**w, "detected": t is not None, "first_alert_ms": t,
               "window_position_hours": None if t is None else (t-w["start_ms"])/3_600_000,
               "crosses_evaluation_boundary": w["start_ms"] < start_ms or w["end_ms"] >= end_ms}
              for w, t in zip(relevant, matches)]
    metrics = {
        "tp": tp, "fp": fp, "fn": fn, "duplicate_fp": duplicates, "outside_fp": outside,
        "precision": div(tp, tp+fp), "recall": div(tp, tp+fn), "f1": div(2*tp, 2*tp+fp+fn),
        "outside_days": outside_days, "outside_events_per_day": div(outside, outside_days),
        "all_events_per_day": div(len(events), (end_ms-start_ms)/DAY_MS),
        "events": len(events), "scored_points": int(selected.sum()),
        "point_window_proxy": {"tp": point_tp, "fp": point_fp, "fn": point_fn,
            "precision": div(point_tp,point_tp+point_fp), "recall": div(point_tp,point_tp+point_fn),
            "f1": div(2*point_tp,2*point_tp+point_fp+point_fn)},
        "windows": detail,
        "definition": "custom event score, not official NAB; no point adjustment; exposure in grid cells"
    }
    return metrics, events


def read_scores(path):
    df = pd.read_csv(path)
    numeric = ["baseline", "residual", "residual_center", "scale", "score"]
    if not np.isfinite(df[numeric].to_numpy(dtype=float)).all() or not np.isfinite(df["update"].dropna()).all():
        raise ValueError(f"nonfinite score output: {path}")
    if not (df["scale"] > 0).all() or not (df["score"] >= 0).all():
        raise ValueError(f"invalid scale/score: {path}")
    if not df.timestamp_ms.is_monotonic_increasing or df.timestamp_ms.duplicated().any():
        raise ValueError(f"unordered score output: {path}")
    return df


def reference_periodic(timestamps, values, config):
    """Independent NumPy oracle, intentionally not a port of Java classes."""
    p, k, w = (int(config[x]) for x in ("period", "k", "window"))
    step, origin = int(config["step_ms"]), int(config["origin_ms"])
    phases = [[] for _ in range(p)]
    residuals = []
    phase_ready = False
    epsilon = None if str(config.get("epsilon", "auto")) == "auto" else float(config["epsilon"])
    tau, clip = float(config["tau"]), float(config["clip"])
    strategy = config["strategy"]
    result = []
    previous = None
    for time, x in zip(timestamps, values):
        time = int(time)
        if (time-origin) % step or (previous is not None and time <= previous):
            raise ValueError("unordered or off-grid time")
        previous = time
        if not np.isfinite(x):
            continue
        phase = ((time-origin)//step) % p
        history = phases[phase]
        if not phase_ready:
            history.append(float(x))
            del history[:-k]
            phase_ready = all(len(h) == k for h in phases)
            continue
        baseline = float(np.median(history))
        residual = float(x-baseline)
        if len(residuals) < w:
            residuals.append(residual)
            history.append(float(x))
            del history[:-k]
            if len(residuals) == w and epsilon is None:
                r = np.array(residuals)
                epsilon = max(1e-9, 1e-3*1.4826*float(np.median(np.abs(r-np.median(r)))))
            continue
        r = np.array(residuals)
        center = float(np.median(r))
        scale = max(float(epsilon),1.4826*float(np.median(np.abs(r-center))))
        score = abs(residual-center)/scale
        flag = score > tau
        update = baseline+center+float(np.clip(residual-center,-clip*scale,clip*scale)) if strategy == "bounded" else float(x)
        # For freeze, update is the candidate raw value; no insertion when flagged.
        result.append((time,float(x),baseline,residual,center,scale,score,flag,update,phase))
        if strategy != "freeze" or not flag:
            history.append(update)
            del history[:-k]
        residuals.append(residual)
        del residuals[:-w]
    return pd.DataFrame(result, columns=["timestamp_ms","value","baseline","residual","residual_center","scale","score","flag","update","phase"])

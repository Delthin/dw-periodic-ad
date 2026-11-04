#!/usr/bin/env python3
"""NAB-only final fusion. Components are scored by Java; fusion is Python.

The two modes and validation rules reproduce the completed development
experiment. These already-exposed results are not a new blind evaluation.
"""
from __future__ import annotations
import numpy as np
from evaluation import evaluate
from benchmark_iteration2 import selection_key

COMPONENTS = ['periodic_point', 'adaptive_evidence', 'periodic_robust_ewma']
MODES = ['adaptive_slow_mean', 'point_adaptive_mean']
SUITES = ['background', 'short', 'long', 'flatline']

def combine(normalized, mode):
    p, a, s = normalized
    if mode == 'adaptive_slow_mean': return (a + s) / 2
    if mode == 'point_adaptive_mean': return (p + a) / 2
    raise ValueError(mode)

def background_threshold(ts, score, meta, c):
    mask = (ts >= meta['validation_start_ms']) & (ts < meta['test_start_ms'])
    bg = score[mask]
    if not np.isfinite(bg).all(): raise ValueError('Nonfinite calibration scores')
    budget = max(c['validation_event_alarm_budget'], (meta['test_start_ms'] - meta['validation_start_ms']) / 86400000 * c.get('validation_events_per_day', 0))
    candidates = sorted({1., *(max(1., float(v)) for v in np.quantile(bg, [.995, .999, 1.], method='higher'))})
    for threshold in candidates:
        m, _ = evaluate(ts, score > threshold, [], meta['validation_start_ms'], meta['test_start_ms'], meta['step_ms'], gap_ms=c['event_merge_gap_ms'])
        fraction = float(np.mean(bg > threshold))
        row = dict(threshold=threshold, background_fraction=fraction, background_events=m['fp'], event_budget=budget, accepted=fraction <= c['validation_point_alarm_budget'] and m['fp'] <= budget)
        if row['accepted']: return row
    raise AssertionError('Maximum background score must yield zero alarms')

def choose(ts, raw, component_thresholds, injections, meta, c):
    normalized = raw / np.asarray(component_thresholds)[None, :, None]
    trials = {}
    mask = (ts >= meta['validation_start_ms']) & (ts < meta['test_start_ms'])
    for mode in MODES:
        scores = {name: combine(normalized[i], mode) for i, name in enumerate(SUITES)}
        selected = background_threshold(ts, scores['background'], meta, c)
        tau = selected['threshold']; details = {}
        for name in SUITES[1:]:
            metric, _ = evaluate(ts, scores[name] > tau, injections[name], meta['validation_start_ms'], meta['test_start_ms'], meta['step_ms'], gap_ms=c['event_merge_gap_ms'])
            truth = np.zeros(len(ts), dtype=bool)
            for w in injections[name]: truth |= (ts >= w['start_ms']) & (ts <= w['end_ms'])
            negative = mask & ~truth
            den = 5 * metric['tp'] + 4 * metric['fn'] + metric['fp']
            details[name] = dict(tp=metric['tp'], fp=metric['fp'], fn=metric['fn'], recall=metric['recall'], f2=5 * metric['tp'] / den if den else 0., outside_point_fraction=float(np.mean(scores[name][negative] > tau)))
        tail = max(r['outside_point_fraction'] for r in details.values())
        selected.update(mean_f2=float(np.mean([r['f2'] for r in details.values()])), mean_recall=float(np.mean([r['recall'] for r in details.values()])), max_outside_fraction=tail, tail_excess=max(0., tail - c['injection_outside_point_budget']), suites=details)
        trials[mode] = dict(selected=selected, tail_fallback=tail > c['injection_outside_point_budget'])
    winner = max(MODES, key=lambda v: selection_key(trials[v])[:-1])
    return dict(variant=winner, threshold=trials[winner]['selected']['threshold'], validation=trials[winner]['selected'], candidates=trials, component_thresholds=dict(zip(COMPONENTS, map(float, component_thresholds))))

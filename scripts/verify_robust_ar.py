#!/usr/bin/env python3
"""Read-only frozen-v8 reference: no fitting, selection, calibration, or labels.

Run with the existing reference checkout's Python environment. Writes only the
explicit --output directory; replays require an explicit locally built --jar.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def properties(path, config):
    path.write_text(''.join(f'{k}={v}\n' for k, v in config.items()))


def fixture(output, predictive, ref, contract, np, pd):
    output.mkdir(parents=True, exist_ok=True)
    model = dict(lags=[1, 2, 3, 6, 12], coefficients=[.7, -.2, .1, -.05, .125],
                 intercept=.125, center=10., scale=2., lag_clip=6.)
    x = np.array([10 + ((i * 7) % 11 - 5) * .25 + i * .03 for i in range(100)], dtype=float)
    x[27:31] += 20; x[45:50] -= 10; x[66] = 10000
    frame = pd.DataFrame(dict(timestamp_ms=np.arange(len(x)), value=x))
    trace = predictive.prediction_trace(frame, model, 6)
    z = ref.z_from_trace(trace, frame.timestamp_ms.to_numpy(), 1.25)
    # Level epsilon is frozen from the first P raw values, independently of AR warmup.
    first = x[:6]; epsilon = max(1e-9, .001 * 1.4826 * float(np.median(np.abs(first - np.median(first)))))
    level = np.full(len(x), np.nan)
    for i in range(6, len(x)):
        old = x[i-6:i]; center = np.median(old)
        scale = max(1.4826 * np.median(np.abs(old-center)), epsilon)
        level[i] = (x[i]-center)/max(scale, 2.)
    base = dict(step_ms=1, period=6, k=1, window=6, tau=4, epsilon='auto', method='robust_ar',
                score_mode='ewma', evidence_scale_floor=1.25, evidence_normalizer=2., evidence_tau=.4,
                ar_lags=','.join(map(str, model['lags'])), ar_coefficients=','.join(map(str, model['coefficients'])),
                ar_intercept=model['intercept'], ar_center=model['center'], ar_scale=model['scale'],
                ar_lag_clip=model['lag_clip'], ar_level_scale_floor=2.)
    for context in [False, True]:
        u = np.sign(z)*np.sqrt(np.abs(z)*np.abs(level)) if context else z
        for mode in ['point', 'ewma6', 'ewma12']:
            config = dict(base, ar_level_context=str(context).lower(),
                          evidence_span={'point': 1, 'ewma6': 6, 'ewma12': 12}[mode],
                          evidence_clip={'point': 'none', 'ewma6': 6, 'ewma12': 4}[mode])
            name = f'{"context" if context else "ar"}-{mode}'
            scores = ref.evidence_channels(u, mode, contract)[:, 0]/2.
            expected = trace.copy()
            ix = expected.timestamp_ms.to_numpy(dtype=int)
            expected['score'] = scores[ix]
            expected['flag'] = scores[ix] > .4
            expected['raw_score'] = np.abs((expected.residual-expected.residual_center)/expected.scale)
            expected['raw_flag'] = expected.raw_score > 4
            expected.to_csv(output/f'{name}.csv', index=False, float_format='%.17g')
            properties(output/f'{name}.properties', config)
    frame.insert(1, 'source_timestamp', 'synthetic')
    frame.to_csv(output/'input.csv', index=False, float_format='%.17g')
    return dict(fixture_cases=6, scored_rows_each=len(trace), input_rows=len(frame), epsilon=epsilon)


def replay(output, root, worktree, jar, predictive, ref, contract, np, pd):
    if not jar.is_file() or not jar.is_relative_to(worktree):
        raise ValueError('--jar must be an existing worktree-owned JAR')
    # Explicitly redirect all reference caches/Java outputs away from the reference checkout.
    ref.ROOT = worktree; ref.JAR = jar
    selections = json.loads((root/'results/iteration2/development-v8/selection.json').read_text())['selections']
    candidates = json.loads((root/'configs/expanded-manifest.json').read_text())['candidates']
    ids = ['realAWSCloudwatch__ec2_cpu_utilization_53ea38',
           'realAWSCloudwatch__ec2_cpu_utilization_24ae8d',
           'realAWSCloudwatch__ec2_cpu_utilization_5f5533']
    results = []
    for name in ids:
        # Use only frozen model/threshold and sampling metadata; never open label files.
        meta = next(m for m in candidates if m['id'] == name)
        selected = selections[name]['adaptive_evidence']; p = selected['parameters']
        folder = output/name; folder.mkdir(parents=True, exist_ok=True)
        input_path = root/meta['input']; meta = dict(meta, input=str(input_path))
        frame = ref.frame_read(input_path)
        ts = frame.timestamp_ms.to_numpy(dtype=np.int64)
        expected_scores = ref.adaptive_reference_scores(meta, frame, p, folder/'reference', contract)
        span = {'point': 1, 'ewma6': 6, 'ewma12': 12}[p['mode']]
        config = dict(step_ms=meta['step_ms'], origin_ms=meta['origin_ms'], period=meta['period'], k=3,
                      window=meta['period'], tau=4, clip=2, epsilon='auto', strategy='bounded', method=p['base'],
                      score_mode='ewma', evidence_span=span, evidence_clip={1: 'none', 6: 6, 12: 4}[span],
                      evidence_scale_floor=p['scale_floor'], evidence_normalizer=p['channel_scales'][0],
                      evidence_tau=selected['selected']['threshold'])
        if p['base'] == 'robust_ar':
            model = p['autoregressive_model']
            for key in ['intercept', 'center', 'scale', 'lag_clip']:
                config['ar_'+key] = model[key]
            config['ar_lags'] = ','.join(map(str, model['lags']))
            config['ar_coefficients'] = ','.join(map(str, model['coefficients']))
            config['ar_level_context'] = str('level_scale_floor' in p).lower()
            config['ar_level_scale_floor'] = p.get('level_scale_floor', 0)
            trace = predictive.prediction_trace(frame, model, meta['period'])
        else:
            trace = ref.frame_read(folder/'reference/test-periodic.csv')
        properties(folder/'frozen.properties', config)
        (folder/'frozen-model.json').write_text(json.dumps(dict(parameters=p, threshold=config['evidence_tau']), indent=2)+'\n')
        with (folder/'java.log').open('w') as log:
            subprocess.run(['java', '-jar', str(jar), 'score-diagnostics', str(input_path),
                            str(folder/'java.csv'), str(folder/'frozen.properties')],
                           cwd=worktree, stdout=log, stderr=subprocess.STDOUT, check=True)
        actual = ref.frame_read(folder/'java.csv')
        np.testing.assert_array_equal(trace.timestamp_ms, actual.timestamp_ms)
        valid = np.isfinite(expected_scores)
        np.testing.assert_array_equal(ts[valid], actual.timestamp_ms)
        errors = {}
        for col in ['baseline', 'residual', 'residual_center', 'scale']:
            a = actual[col].to_numpy(); b = trace[col].to_numpy()
            np.testing.assert_allclose(a, b, rtol=2e-10, atol=2e-10)
            errors[col] = float(np.max(np.abs(a-b)))
        expected_raw = np.abs((trace.residual-trace.residual_center)/trace.scale).to_numpy()
        np.testing.assert_allclose(actual.raw_score, expected_raw, rtol=2e-9, atol=2e-9)
        np.testing.assert_array_equal(actual.raw_flag, expected_raw > config['tau'])
        scores = expected_scores[valid]
        np.testing.assert_allclose(actual.score, scores, rtol=2e-9, atol=2e-9)
        np.testing.assert_allclose(actual.evidence_score, scores, rtol=2e-9, atol=2e-9)
        np.testing.assert_array_equal(actual.flag, scores > config['evidence_tau'])
        errors['score'] = float(np.max(np.abs(actual.score.to_numpy()-scores)))
        expected = trace.copy(); expected['score'] = scores; expected['flag'] = scores > config['evidence_tau']
        expected.to_csv(folder/'python.csv', index=False, float_format='%.17g')
        results.append(dict(id=name, variant=selected['variant'], input_sha256=digest(input_path),
                            input_rows=len(frame), scored_rows=len(actual), flags=int(actual.flag.sum()),
                            flag_mismatches=0, raw_flag_mismatches=0, max_absolute_errors=errors))
    return dict(jar_sha256=digest(jar), replays=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['fixtures', 'replay'])
    parser.add_argument('--reference-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--jar', type=Path)
    args = parser.parse_args()
    root = args.reference_root.resolve(); output = args.output.resolve()
    worktree = Path(__file__).resolve().parents[1]
    if not output.is_relative_to(worktree):
        raise ValueError('--output must be inside this worktree')
    sys.dont_write_bytecode = True  # no __pycache__ writes in the read-only reference tree
    sys.path.insert(0, str(root/'scripts'))
    import numpy as np
    import pandas as pd
    import predictive_baseline as predictive
    import benchmark_iteration2 as ref
    contract = json.loads((root/'configs/iteration2.json').read_text())
    output.mkdir(parents=True, exist_ok=True)
    result = fixture(output, predictive, ref, contract, np, pd) if args.mode == 'fixtures' else replay(
        output, root, worktree, args.jar.resolve(), predictive, ref, contract, np, pd)
    result['reference_sha256'] = {str(p.relative_to(root)): digest(p) for p in [
        root/'scripts/predictive_baseline.py', root/'scripts/benchmark_iteration2.py',
        root/'configs/iteration2.json', root/'results/iteration2/development-v8/selection.json']}
    (output/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

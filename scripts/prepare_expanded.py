#!/usr/bin/env python3
"""Download the pinned NAB corpus and admit every structurally compatible real series.

Labels are downloaded as opaque bytes; this module never parses them. No original
observation is interpolated, deduplicated, sorted, normalized, or removed.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / 'configs/benchmark-expanded.json'
RAW = ROOT / 'data/raw/post-feedback-nab'
OUT = ROOT / 'data/processed/expanded'
# Tree linked by GitHub's signed commit ea702d75...; verify recursive cache contents,
# not merely the cache's self-reported sha field.
NAB_TREE_SHA = '7748725097344fe37386eb356e69d91c6b14b9f0'


def dump(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_object_sha(kind, content):
    return hashlib.sha1(f'{kind} {len(content)}\0'.encode()+content).hexdigest()


def verify_tree(obj):
    if obj.get('truncated'):
        raise ValueError('Truncated NAB tree cannot be verified')
    # GitHub echoes a commit-ish request in obj['sha']; the recomputed root
    # tree below, not that transport field, proves the pinned contents.
    entries = obj['tree']; directories = {'': NAB_TREE_SHA}
    directories.update({r['path']: r['sha'] for r in entries if r['type']=='tree'})
    for parent, expected in directories.items():
        children = [r for r in entries if r['path'].rpartition('/')[0] == parent]
        children.sort(key=lambda r:(r['path'].rsplit('/',1)[-1]+('/' if r['type']=='tree' else '')).encode())
        content = b''.join(r['mode'].lstrip('0').encode()+b' '+r['path'].rsplit('/',1)[-1].encode()+b'\0'+bytes.fromhex(r['sha']) for r in children)
        if git_object_sha('tree',content) != expected:
            raise ValueError('Invalid cached Git tree contents: '+parent)
    return {r['path']:r['sha'] for r in entries if r['type']=='blob'}


def verify_blob(path, expected):
    if git_object_sha('blob',Path(path).read_bytes()) != expected:
        raise ValueError('Cached file is not the pinned NAB blob: '+str(path))


def fetch(url, path):
    path = Path(path)
    if path.exists():
        return
    req = Request(url, headers={'User-Agent': 'dw-periodic-ad-course'})
    with urlopen(req, timeout=90) as r:
        content = r.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.part'); tmp.write_bytes(content); tmp.replace(path)


def download(contract):
    commit = contract['nab_commit']
    tree = RAW/'tree.json'
    fetch(f'https://api.github.com/repos/numenta/NAB/git/trees/{commit}?recursive=1', tree)
    obj = json.loads(tree.read_text())
    expected_blobs = verify_tree(obj)
    paths = sorted(r['path'] for r in obj['tree'] if r['path'].startswith('data/') and r['path'].endswith('.csv'))
    jobs = [(f'https://raw.githubusercontent.com/numenta/NAB/{commit}/{p}', RAW/p[5:]) for p in paths]
    jobs += [(f'https://raw.githubusercontent.com/numenta/NAB/{commit}/{src}', RAW/dst) for src,dst in
             [('LICENSE.txt','LICENSE.txt'),('data/README.md','README.md'),('labels/combined_windows.json','combined_windows.json')]]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda x: fetch(*x), jobs))
    for p in paths:
        verify_blob(RAW/p[5:], expected_blobs[p])
    for src,dst in [('LICENSE.txt','LICENSE.txt'),('data/README.md','README.md'),('labels/combined_windows.json','combined_windows.json')]:
        verify_blob(RAW/dst, expected_blobs[src])
    return paths


def lag_correlation(values, lag):
    a,b = values[:-lag],values[lag:]
    if len(a)<2 or np.std(a)==0 or np.std(b)==0:
        return None
    corr = float(np.corrcoef(a,b)[0,1])
    return corr if np.isfinite(corr) else None


def inspect(path, contract):
    rel = path.relative_to(RAW).as_posix()
    frame = pd.read_csv(path, float_precision='round_trip')
    x = pd.to_numeric(frame['value'], errors='coerce').to_numpy(dtype=float)
    dt = pd.to_datetime(frame['timestamp'], utc=True, format='mixed', errors='raise')
    ts = dt.to_numpy(dtype='datetime64[ms]').astype(np.int64)
    n = len(frame); train = int(n*contract['split_train_end']); val = int(n*contract['split_validation_end'])
    diffs = np.diff(ts)
    step = int(pd.Series(diffs).mode().iloc[0]) if len(diffs) else 0
    regular = step>0 and bool(np.all(diffs==step))
    reasons = []
    if not np.isfinite(x).all(): reasons.append('nonfinite_values')
    if not regular: reasons.append('not_completely_regular')
    if step<=0 or 86400000%step: reasons.append('step_does_not_divide_day')
    daily = 86400000//step if step>0 and 86400000%step==0 else None
    if daily is None or daily<2: reasons.append('unsupported_daily_period')
    if daily and train<=4*daily: reasons.append('training_shorter_than_four_daily_cycles_plus_one')
    row = {'source':rel,'family':rel.split('/')[0], 'raw_sha256':sha(path),
           'source_url':f"https://raw.githubusercontent.com/numenta/NAB/{contract['nab_commit']}/data/{rel}",
           'rows':n,'step_ms':step,'regular':regular,'daily_period':daily,'admitted':not reasons,'reasons':reasons,
           'previously_evaluated':rel in contract['previously_evaluated']}
    if reasons: return row
    candidates = []
    for period in [daily, 7*daily]:
        if train>4*period:
            candidates.append({'period':period,'train_lag_correlation':lag_correlation(x[:train],period)})
    chosen = max(candidates, key=lambda r:(r['train_lag_correlation'] if r['train_lag_correlation'] is not None else -1,-r['period']))
    name = rel[:-4].replace('/','__')
    target = OUT/f'{name}.csv'
    pd.DataFrame({'timestamp_ms':ts,'source_timestamp':frame.timestamp,'value':frame.value}).to_csv(target,index=False)
    # Numeric round-trip, not just row count, is required for the processed input.
    readback = pd.read_csv(target, float_precision='round_trip')
    np.testing.assert_array_equal(readback.timestamp_ms.to_numpy(), ts)
    np.testing.assert_allclose(readback.value.to_numpy(dtype=float),x,rtol=1e-14,atol=1e-14)
    row.update({'id':name,**chosen,'period_candidates':candidates,'origin_ms':int(ts[0]),
                'train_end_index':train,'validation_end_index':val,
                'validation_start_ms':int(ts[train]),'test_start_ms':int(ts[val]),
                'end_ms_exclusive':int(ts[-1]+step), 'input':str(target.relative_to(ROOT)),
                'processed_sha256':sha(target), 'strong_periodicity':chosen['train_lag_correlation'] is not None and chosen['train_lag_correlation']>=0.5})
    return row


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--download',action='store_true');args=ap.parse_args()
    contract=json.loads(CONTRACT_PATH.read_text());OUT.mkdir(parents=True,exist_ok=True)
    if args.download: download(contract)
    # Fetch tree even when existing audited CSVs are reused: missing files must not silently disappear.
    tree=RAW/'tree.json'
    if not tree.exists():
        fetch(f"https://api.github.com/repos/numenta/NAB/git/trees/{contract['nab_commit']}?recursive=1",tree)
    tree_obj=json.loads(tree.read_text())
    expected_blobs=verify_tree(tree_obj)
    expected=sorted(r['path'][5:] for r in tree_obj['tree'] if r['path'].startswith('data/') and r['path'].endswith('.csv') and r['path'].split('/')[1] in contract['candidate_families'])
    missing=[p for p in expected if not (RAW/p).exists()]
    if missing:raise FileNotFoundError(f'Missing {len(missing)} candidate files; run --download: {missing}')
    for p in expected:verify_blob(RAW/p,expected_blobs['data/'+p])
    labels=RAW/'combined_windows.json'
    if not labels.exists():labels=RAW/'labels/combined_windows.json'
    verify_blob(labels,expected_blobs['labels/combined_windows.json'])  # opaque bytes only
    rows=[inspect(RAW/p,contract) for p in expected]
    manifest={'contract_sha256':sha(CONTRACT_PATH),'nab_commit':contract['nab_commit'],'candidates':rows,
              'candidate_count':len(rows),'admitted_count':sum(r['admitted'] for r in rows),
              'label_sha256':sha(labels),'pinned_git_tree_sha':NAB_TREE_SHA,
              'label_access':'raw-byte blob verification only; labels not parsed during admission or period selection'}
    dump(ROOT/'configs/expanded-manifest.json',manifest)
    print(json.dumps({'candidates':len(rows),'admitted':manifest['admitted_count'],
          'families':{f:sum(r['admitted'] and r['family']==f for r in rows) for f in contract['candidate_families']},
          'excluded':[{'source':r['source'],'reasons':r['reasons']} for r in rows if not r['admitted']]},ensure_ascii=False,indent=2))


if __name__=='__main__':main()

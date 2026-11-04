#!/usr/bin/env python3
"""Export lossless NAB-only calibration and full-history component scores."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd
from prepare_expanded import ROOT,RAW,dump,sha
from benchmark_expanded import windows_for
from nab_fusion import COMPONENTS,SUITES

def aligned(path,ts,jar_hash):
    cache=json.loads(path.with_suffix('.cache.json').read_text())
    if sha(path)!=cache['sha256'] or cache['key']['jar']!=jar_hash:raise ValueError('Score cache fingerprint changed: '+str(path))
    f=pd.read_csv(path,usecols=['timestamp_ms','score'],float_precision='round_trip')
    ix=np.searchsorted(ts,f.timestamp_ms.to_numpy(dtype=np.int64));np.testing.assert_array_equal(ts[ix],f.timestamp_ms)
    a=np.full(len(ts),np.nan);a[ix]=f.score.to_numpy();return a

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--components',type=Path,default=ROOT/'results/iteration2/final-nab');p.add_argument('--output',type=Path,default=ROOT/'results/submission/nab-replay');a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=True);previous=a.components.resolve()
    manifest=json.loads((ROOT/'configs/expanded-manifest.json').read_text());frozen=json.loads((previous/'selection.json').read_text())
    labelpath=RAW/'labels/combined_windows.json'
    if not labelpath.exists():labelpath=RAW/'combined_windows.json'
    labels=json.loads(labelpath.read_text());rows=[]
    for meta in manifest['candidates']:
        if not meta['admitted']:continue
        ident=meta['id'];folder=previous/ident;choice=frozen['selections'][ident]
        ts=pd.read_csv(ROOT/meta['input'],usecols=['timestamp_ms']).timestamp_ms.to_numpy(dtype=np.int64);cal_ts=ts[:meta['validation_end_index']]
        cal=[];hashes={}
        for suite in SUITES:
            channels=[]
            for method in COMPONENTS:
                tag=choice[method]['variant'] if method=='adaptive_evidence' else method
                path=folder/'java-production'/f'{suite}-{tag}.csv';hashes[str(path.relative_to(ROOT))]=sha(path)
                channels.append(aligned(path,cal_ts,frozen['runner_sha256']))
            cal.append(channels)
        keep=np.arange(len(ts))>=4*meta['period'];test=[]
        for method in COMPONENTS:
            path=folder/'test/production'/f'test-{method}.csv';hashes[str(path.relative_to(ROOT))]=sha(path)
            test.append(aligned(path,ts,frozen['runner_sha256'])[keep])
        windows=windows_for(labels[meta['source']]);t=ts[keep];truth=np.zeros(len(t),dtype=bool)
        for w in windows:truth|=(t>=w['start_ms'])&(t<=w['end_ms'])
        path=out/(ident+'.npz');np.savez_compressed(path,cal_t=cal_ts,cal_raw=np.asarray(cal),t=t,raw=np.asarray(test),truth=truth)
        fields=['id','source','family','step_ms','period','validation_start_ms','test_start_ms','end_ms_exclusive','strong_periodicity']
        rows.append({**{k:meta[k] for k in fields},'file':path.name,'sha256':sha(path),'windows':windows,'injections':json.loads((folder/'injections.json').read_text()),'component_thresholds':{m:choice[m]['selected']['threshold'] for m in COMPONENTS},'source_scores_sha256':hashes})
    dump(out/'manifest.json',{'scope':'NAB-only; lossless Java scores; calibration-only mode selection; already-exposed development data','series':rows,'component_selection_sha256':sha(previous/'selection.json'),'manifest_sha256':sha(ROOT/'configs/expanded-manifest.json'),'labels_sha256':sha(labelpath),'components':COMPONENTS,'suites':SUITES,'contract':json.loads((ROOT/'configs/iteration2.json').read_text()),'script_sha256':sha(Path(__file__))})
    historical=ROOT/'results/experiment-focus/two-mode-fusion'
    if (historical/'metrics.json').exists():
        expected=[r for r in json.loads((historical/'metrics.json').read_text()) if r['dataset']=='nab']
        choices=json.loads((historical/'selection.json').read_text())['selections']['nab']
        dump(out/'expected.json',{'metrics':expected,'selections':choices,'source_metrics_sha256':sha(historical/'metrics.json'),'source_selection_sha256':sha(historical/'selection.json')})
    print(json.dumps({'series':len(rows),'files':len(rows)+1,'output':str(out)}))
if __name__=='__main__':main()

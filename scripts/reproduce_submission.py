#!/usr/bin/env python3
"""Re-select NAB fusion from validation scores, then re-evaluate 21 histories."""
from __future__ import annotations
import argparse,json,math
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score
from prepare_expanded import ROOT,dump,sha
from benchmark_expanded import aggregate
from evaluation import evaluate
from nab_fusion import COMPONENTS,MODES,choose,combine

def packages():
    checked=0
    for path in ROOT.glob('package-manifest-*.json'):
        obj=json.loads(path.read_text());rows=obj['files']
        for row in rows:
            file=ROOT/row['path']
            if not file.is_file() or file.stat().st_size!=row['bytes'] or sha(file)!=row['sha256']:raise ValueError('Package file differs: '+row['path'])
            checked+=1
    return checked

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,default=ROOT/'results/submission/nab-replay');p.add_argument('--output',type=Path,default=ROOT/'results/reproduced-submission');p.add_argument('--check-hashes',action='store_true',help='Opt in to legacy file-hash checks; disabled by default.');p.add_argument('--no-package-check',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    check_hashes=a.check_hashes and not a.no_package_check
    checked=packages() if check_hashes else 0;out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    data=json.loads((a.input/'manifest.json').read_text());c=data['contract'];selected={}
    for meta in data['series']:
        path=a.input/meta['file']
        if check_hashes and sha(path)!=meta['sha256']:raise ValueError('Replay scores changed: '+meta['id'])
        with np.load(path) as z:
            selected[meta['id']]=choose(z['cal_t'],z['cal_raw'],[meta['component_thresholds'][m] for m in COMPONENTS],meta['injections'],meta,c)
    # Persist all choices before evaluating true windows. No test score selects a mode.
    dump(out/'selection.json',{'selections':selected,'allowed_modes':MODES,'scope':'NAB exposed-data development replay; validation-only selection','input_manifest':str(a.input/'manifest.json')})
    rows=[];window_rows=[]
    for meta in data['series']:
        choice=selected[meta['id']]
        with np.load(a.input/meta['file']) as z:
            t=z['t'];truth=z['truth'];normalized=z['raw']/np.asarray([choice['component_thresholds'][m] for m in COMPONENTS])[:,None]
            mask=(t>=meta['test_start_ms'])&(t<meta['end_ms_exclusive']);negative=mask&~truth
            for mode in MODES+['adaptive_complementary_fusion']:
                actual=choice['variant'] if mode=='adaptive_complementary_fusion' else mode
                tau=choice['candidates'][actual]['selected']['threshold'];score=combine(normalized,actual)
                if not np.isfinite(score[mask]).all():raise ValueError('Nonfinite test scores')
                flags=score>tau;metric,_=evaluate(t,flags,meta['windows'],meta['test_start_ms'],meta['end_ms_exclusive'],meta['step_ms'],gap_ms=c['event_merge_gap_ms'])
                ptp=int(np.count_nonzero(mask&truth&flags));pfp=int(np.count_nonzero(negative&flags));pfn=int(np.count_nonzero(mask&truth&~flags));den=2*ptp+pfp+pfn
                rows.append({'dataset':'nab',**{k:meta[k] for k in ('id','source','family','strong_periodicity')},'method':mode,'previously_evaluated':True,**{k:v for k,v in metric.items() if k not in ('windows','point_window_proxy','definition')},'point_tp':ptp,'point_fp':pfp,'point_fn':pfn,'point_f1':2*ptp/den if den else 0.,'negative_points':int(negative.sum()),'outside_point_fpr':pfp/int(negative.sum()) if negative.any() else None,'window_proxy_average_precision':float(average_precision_score(truth[mask],score[mask])) if truth[mask].any() else None,'threshold':tau,'selected_variant':actual})
                for w in metric['windows']:
                    inside=mask&(t>=w['start_ms'])&(t<=w['end_ms']);v=score[inside]
                    window_rows.append({'id':meta['id'],'method':mode,**w,'threshold':tau,'max_score':float(v.max()) if len(v) else None,'max_score_to_threshold':float(v.max()/tau) if len(v) else None,'positive_points':int(np.count_nonzero(v>tau))})
    final=[r for r in rows if r['method']=='adaptive_complementary_fusion'];fields=['tp','fp','fn','outside_fp','duplicate_fp','point_tp','point_fp','point_fn','f1','point_f1','outside_point_fpr']
    expected_path=a.input/'expected.json';status='recomputed';checks=0
    if expected_path.exists():
        expected=json.loads(expected_path.read_text());byid={r['id']:r for r in expected['metrics']}
        if set(byid)!=set(selected):raise AssertionError('Expected series differ')
        for r in final:
            previous=expected['selections'][r['id']]
            if selected[r['id']]['variant']!=previous['variant'] or selected[r['id']]['threshold']!=previous['threshold']:raise AssertionError('Validation selection differs: '+r['id'])
            for k in fields:
                x,y=r[k],byid[r['id']][k]
                if x is None or y is None:ok=x is y
                else:ok=math.isclose(x,y,rel_tol=0,abs_tol=1e-12)
                if not ok:raise AssertionError(f'{r["id"]} {k}: {x} != {y}')
                checks+=1
        status='matched'
    for name,obj in [('metrics',rows),('windows',window_rows),('summary',aggregate(rows))]:dump(out/(name+'.json'),obj);pd.DataFrame(obj).to_csv(out/(name+'.csv'),index=False)
    result={'status':status,'series_checked':len(final),'metric_fields_checked':checks,'package_files_checked':checked,'mode_counts':dict(Counter(r['variant'] for r in selected.values())),'totals':{k:sum(r[k] for r in final) for k in ('tp','fp','fn','outside_fp','duplicate_fp')},'scope':'Recomputed validation fusion selection and event/point metrics from lossless Java scores; not model refitting or a new blind test'}
    dump(out/'verification.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()

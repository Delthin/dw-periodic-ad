#!/usr/bin/env python3
"""Leakage-aware multi-series NAB benchmark with independent sklearn baselines.

Run after prepare_expanded.py and a Java package build. Selection is a separate
stage, persists every candidate and hash, and never opens the label file.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path
import subprocess
import time
import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.metrics import average_precision_score
from sklearn.preprocessing import StandardScaler
from evaluation import evaluate
from prepare_expanded import ROOT, RAW, CONTRACT_PATH, dump, sha

OUT = ROOT/'results/post-feedback/expanded'
JAR = ROOT/'runner/target/periodic-ad-runner.jar'
JAVA_METHODS = ['rolling_mad','periodic_point','periodic_ewma','periodic_robust_ewma']


def write_config(path, meta, method, threshold=None):
    c = json.loads(CONTRACT_PATH.read_text())
    config = {'step_ms':meta['step_ms'],'origin_ms':meta['origin_ms'],'period':meta['period'],
              'k':c['history_k'],'window':meta['period'],'tau':c['raw_update_tau'],
              'clip':c['baseline_clip'],'epsilon':'auto','strategy':'bounded',
              'method':'rolling_mad' if method=='rolling_mad' else 'periodic',
              'score_mode':'ewma' if method in ['periodic_ewma','periodic_robust_ewma'] else 'point',
              'evidence_span':c['evidence_span'],'evidence_tau':3.0 if threshold is None else threshold}
    # BOUNDED history writes are independent of tau, so replaying a selected point
    # threshold changes only flags, not scores/history. EWMA retains raw tau=4.
    if threshold is not None and method in ['periodic_point','rolling_mad']:config['tau']=threshold
    if method=='periodic_robust_ewma': config['evidence_clip']=c['evidence_clip']
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(f'{k}={v}\n' for k,v in config.items()))
    return config


def java_scores(meta, inputs, folder, thresholds=None):
    """Each input is a named processed CSV. All four scores come from production Java."""
    folder.mkdir(parents=True,exist_ok=True);jobs=[]
    for name,input_path in inputs.items():
        for method in JAVA_METHODS:
            cfg=folder/f'{name}-{method}.properties';write_config(cfg,meta,method,None if thresholds is None else thresholds[method])
            output=folder/f'{name}-{method}.csv'
            jobs.append((str(input_path.resolve()),str(output.resolve()),str(cfg.resolve())))
    batch=folder/'jobs.tsv';batch.write_text(''.join('\t'.join(row)+'\n' for row in jobs))
    with (folder/'java.log').open('w') as log:
        subprocess.run(['java','-jar',str(JAR),'batch',str(batch)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    result={}
    for name,input_path in inputs.items():
        frame=pd.read_csv(input_path,float_precision='round_trip'); ts=frame.timestamp_ms.to_numpy(dtype=np.int64)
        result[name]={}
        for method in JAVA_METHODS:
            trace=pd.read_csv(folder/f'{name}-{method}.csv',float_precision='round_trip')
            scores=np.full(len(ts),np.nan);positions=np.searchsorted(ts,trace.timestamp_ms.to_numpy())
            np.testing.assert_array_equal(ts[positions],trace.timestamp_ms.to_numpy())
            scores[positions]=trace.score.to_numpy(dtype=float)
            if thresholds is not None:
                np.testing.assert_array_equal(trace.flag.to_numpy(dtype=bool),scores[positions]>thresholds[method])
            result[name][method]=scores
    return result


def vectors(x,width):
    return np.lib.stride_tricks.sliding_window_view(np.asarray(x,dtype=float),width)


def fit_models(x,meta,contract):
    width=min(24,meta['daily_period']);train=meta['train_end_index']
    training=vectors(x[:train],width)
    scaler=StandardScaler().fit(training)
    selected=np.linspace(0,len(training)-1,min(5000,len(training)),dtype=int)
    samples=scaler.transform(training[selected])
    forest=IsolationForest(n_estimators=200,max_samples=min(256,len(samples)),random_state=contract['seed'],n_jobs=1).fit(samples)
    lof=LocalOutlierFactor(n_neighbors=min(20,len(samples)-1),novelty=True,n_jobs=1).fit(samples)
    return {'width':width,'scaler':scaler,'isolation_forest':forest,'local_outlier_factor':lof,
            'training_vectors':len(training),'fitted_vectors':len(samples)}


def independent_scores(x,meta,models):
    p=meta['period'];width=models['width'];result={}
    seasonal=np.full(len(x),np.nan);seasonal[p:]=np.abs(x[p:]-x[:-p]);result['seasonal_naive']=seasonal
    transformed=models['scaler'].transform(vectors(x,width))
    for method in ['isolation_forest','local_outlier_factor']:
        score=np.full(len(x),np.nan)
        score[width-1:]=-models[method].score_samples(transformed)
        result[method]=score
    return result


def injected_prefix(frame,meta,contract):
    train,end=meta['train_end_index'],meta['validation_end_index']
    out=frame.iloc[:end].copy();x=out.value.to_numpy(dtype=float).copy();p=meta['period']
    residual=x[p:train]-x[:train-p];med=np.median(residual)
    sigma=1.4826*np.median(np.abs(residual-med))
    spec=contract['injection']
    amplitude=max(spec['amplitude_training_seasonal_mad_multiple']*sigma,
                  spec['amplitude_training_iqr_floor_fraction']*float(np.subtract(*np.percentile(x[:train],[75,25]))),1e-6)
    starts=[train+round((end-train)*f) for f in spec['fractions']]
    windows=[];ts=out.timestamp_ms.to_numpy(dtype=np.int64)
    for i,(a,fraction,sign) in enumerate(zip(starts,spec['duration_daily_fractions'],spec['signs'])):
        max_end=starts[i+1]-2 if i+1<len(starts) else end
        b=min(a+max(1,round(meta['daily_period']*fraction)),max_end)
        if b<=a:raise ValueError('Validation prefix too short for nonoverlapping injections')
        x[a:b]+=sign*amplitude
        windows.append({'start_ms':int(ts[a]),'end_ms':int(ts[b-1]),'points':b-a,'amplitude':float(sign*amplitude)})
    out['value']=x
    return out,windows


def common(meta):return 4*meta['period']


def choose(ts,bg,inj,windows,meta,method,contract):
    lo=meta['validation_start_ms'];hi=meta['test_start_ms'];valid=(ts>=lo)&(ts<hi)
    if not np.isfinite(bg[valid]).all() or not np.isfinite(inj[valid]).all():raise ValueError('nonfinite calibration scores')
    candidates=[]
    for threshold in np.unique(np.quantile(bg[valid],contract['threshold_quantiles'])):
        threshold=max(float(threshold),1e-12)
        if method=='periodic_robust_ewma':threshold=min(threshold,float(np.nextafter(contract['evidence_clip'],0)))
        background_fraction=float(np.mean(bg[valid]>threshold))
        background_events,_=evaluate(ts,bg>threshold,[],lo,hi,meta['step_ms'])
        metric,_=evaluate(ts,inj>threshold,windows,lo,hi,meta['step_ms'])
        candidates.append({'threshold':threshold,'background_point_alarm_fraction':background_fraction,
            'background_events':background_events['events'],'injected_tp':metric['tp'],'injected_fp':metric['fp'],
            'injected_fn':metric['fn'],'injected_f1':metric['f1'],
            'within_budget':background_fraction<=contract['validation_point_alarm_budget']})
    eligible=[r for r in candidates if r['within_budget']]
    if eligible:best=max(eligible,key=lambda r:(r['injected_f1'],-r['background_point_alarm_fraction'],r['threshold']))
    else:best=max(candidates,key=lambda r:(-r['background_point_alarm_fraction'],r['injected_f1'],r['threshold']))
    return {'selected':best,'candidates':candidates,'fallback':not bool(eligible)}


def versions():
    return {'python':platform.python_version(),**{name:version(name) for name in ['numpy','pandas','scikit-learn','scipy','joblib','threadpoolctl']}}


def script_hashes():
    return {name:sha(ROOT/'scripts'/name) for name in ['benchmark_expanded.py','prepare_expanded.py','evaluation.py']}


def verify_frozen(manifest,frozen):
    if frozen['contract_sha256']!=sha(CONTRACT_PATH) or frozen['manifest_sha256']!=sha(ROOT/'configs/expanded-manifest.json') or frozen['runner_sha256']!=sha(JAR):
        raise ValueError('Frozen inputs or Java implementation changed after calibration')
    if frozen['versions']!=versions() or frozen['script_sha256']!=script_hashes():
        raise ValueError('Python implementation or dependencies changed after calibration')
    for meta in manifest['candidates']:
        if meta['admitted'] and sha(OUT/meta['id']/'models.joblib')!=frozen['model_sha256'][meta['id']]:
            raise ValueError('Frozen sklearn model changed: '+meta['id'])


def calibrate(manifest,contract):
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'selection.json').exists():raise FileExistsError('Frozen selection already exists; use a new --output directory for a new protocol')
    selections={};started=time.time()
    for meta in manifest['candidates']:
        if not meta['admitted']:continue
        folder=OUT/meta['id'];folder.mkdir(parents=True,exist_ok=True)
        frame=pd.read_csv(ROOT/meta['input'],float_precision='round_trip');prefix=frame.iloc[:meta['validation_end_index']]
        background=folder/'validation-background.csv';prefix.to_csv(background,index=False)
        injected,windows=injected_prefix(frame,meta,contract);injected_path=folder/'validation-injected.csv';injected.to_csv(injected_path,index=False)
        dump(folder/'injections.json',windows)
        models=fit_models(frame.value.to_numpy(dtype=float),meta,contract);joblib.dump(models,folder/'models.joblib',compress=3)
        scores=java_scores(meta,{'background':background,'injected':injected_path},folder/'calibration')
        for name,input_frame in [('background',prefix),('injected',injected)]:
            scores[name].update(independent_scores(input_frame.value.to_numpy(dtype=float),meta,models))
        ix=common(meta);ts=prefix.timestamp_ms.to_numpy(dtype=np.int64)[ix:]
        selections[meta['id']]={method:choose(ts,scores['background'][method][ix:],scores['injected'][method][ix:],windows,meta,method,contract) for method in contract['methods']}
        dump(folder/'selection.json',selections[meta['id']]);print('calibrated',meta['id'],flush=True)
    frozen={'contract_sha256':sha(CONTRACT_PATH),'manifest_sha256':sha(ROOT/'configs/expanded-manifest.json'),
            'runner_sha256':sha(JAR),'selections':selections,'elapsed_seconds':time.time()-started,
            'label_access':'none; no test labels parsed before this file was written',
            'versions':versions(),'script_sha256':script_hashes(),
            'model_sha256':{meta['id']:sha(OUT/meta['id']/'models.joblib') for meta in manifest['candidates'] if meta['admitted']}}
    dump(OUT/'selection.json',frozen)
    return frozen


def windows_for(label_pairs):
    return [{'start_ms':int(pd.Timestamp(a,tz='UTC').timestamp()*1000),'end_ms':int(pd.Timestamp(b,tz='UTC').timestamp()*1000)} for a,b in label_pairs]


def score_metric(ts,scores,threshold,windows,meta):
    start,end=meta['test_start_ms'],meta['end_ms_exclusive'];mask=(ts>=start)&(ts<end)
    if not np.isfinite(scores[mask]).all():raise ValueError('nonfinite test scores')
    metric,events=evaluate(ts,scores>threshold,windows,start,end,meta['step_ms'])
    truth=np.zeros(len(ts),dtype=bool)
    for w in windows:truth|=(ts>=w['start_ms'])&(ts<=w['end_ms'])
    negatives=int(np.count_nonzero(mask&~truth));positives=int(np.count_nonzero(mask&truth))
    metric.update({'negative_points':negatives,'positive_points':positives,
        'outside_point_fpr':metric['point_window_proxy']['fp']/negatives if negatives else None,
        'window_proxy_average_precision':float(average_precision_score(truth[mask],scores[mask])) if positives and negatives else None,
        'threshold':threshold,'event_recall_defined':bool(metric['tp']+metric['fn'])})
    return metric,events


def aggregate(records):
    frame=pd.DataFrame(records);rows=[]
    subsets={'all':frame,'newly_evaluated':frame[~frame.previously_evaluated],
             'previously_evaluated':frame[frame.previously_evaluated],
             'strong_periodicity':frame[frame.strong_periodicity],'weak_or_undefined_periodicity':frame[~frame.strong_periodicity]}
    subsets.update({f'family:{family}':g for family,g in frame.groupby('family')})
    for subset,f in subsets.items():
        for method,g in f.groupby('method',sort=False):
            tp,fp,fn=(int(g[k].sum()) for k in ['tp','fp','fn']);den=2*tp+fp+fn
            labelled=g[g.tp+g.fn>0]
            rows.append({'subset':subset,'method':method,'series':len(g),'series_with_test_windows':len(labelled),
                'windows':tp+fn,'tp':tp,'fp':fp,'fn':fn,'outside_fp':int(g.outside_fp.sum()),'duplicate_fp':int(g.duplicate_fp.sum()),
                'micro_precision':tp/(tp+fp) if tp+fp else 0.,'micro_recall':tp/(tp+fn) if tp+fn else None,
                'micro_f1':2*tp/den if den else None,'macro_f1_labelled_series':float(labelled.f1.mean()) if len(labelled) else None,
                'outside_events_per_day':float(g.outside_fp.sum()/g.outside_days.sum()) if g.outside_days.sum() else None,
                'outside_point_fpr':float(g.point_fp.sum()/g.negative_points.sum()) if g.negative_points.sum() else None,
                'macro_window_proxy_ap':float(g.window_proxy_average_precision.dropna().mean()) if g.window_proxy_average_precision.notna().any() else None})
    return rows


def test(manifest,contract,frozen):
    verify_frozen(manifest,frozen)
    label_path=RAW/'combined_windows.json'
    if not label_path.exists():label_path=RAW/'labels/combined_windows.json'
    if sha(label_path)!=manifest['label_sha256']:raise ValueError('Pinned labels changed after preparation')
    labels=json.loads(label_path.read_text())  # First and only label parsing, after global freeze.
    records=[];all_windows=[]
    for meta in manifest['candidates']:
        if not meta['admitted']:continue
        folder=OUT/meta['id'];frame=pd.read_csv(ROOT/meta['input'],float_precision='round_trip');models=joblib.load(folder/'models.joblib')
        selected_thresholds={method:frozen['selections'][meta['id']][method]['selected']['threshold'] for method in contract['methods']}
        scores=java_scores(meta,{'test':ROOT/meta['input']},folder/'test',selected_thresholds)['test']
        scores.update(independent_scores(frame.value.to_numpy(dtype=float),meta,models))
        ix=common(meta);ts=frame.timestamp_ms.to_numpy(dtype=np.int64)[ix:]
        windows=windows_for(labels[meta['source']]);series_metrics={}
        table=pd.DataFrame({'timestamp_ms':ts,'value':frame.value.to_numpy(dtype=float)[ix:]})
        for method in contract['methods']:
            threshold=frozen['selections'][meta['id']][method]['selected']['threshold'];score=scores[method][ix:]
            metric,events=score_metric(ts,score,threshold,windows,meta);series_metrics[method]=metric
            table[method]=score;table[method+'_flag']=score>threshold
            dump(folder/'test'/f'{method}-events.json',events)
            row={'id':meta['id'],'source':meta['source'],'family':meta['family'],'method':method,
                 'previously_evaluated':meta['previously_evaluated'],'strong_periodicity':meta['strong_periodicity'],
                 **{k:v for k,v in metric.items() if k not in ['windows','point_window_proxy','definition']},
                 'point_fp':metric['point_window_proxy']['fp'],'point_tp':metric['point_window_proxy']['tp'],'point_fn':metric['point_window_proxy']['fn']}
            records.append(row)
            all_windows.extend([{'id':meta['id'],'method':method,**w} for w in metric['windows']])
        dump(folder/'test/metrics.json',series_metrics);table.to_csv(folder/'test/all-scores.csv',index=False)
        print('evaluated',meta['id'],flush=True)
    summaries=aggregate(records);dump(OUT/'metrics.json',records);dump(OUT/'summary.json',summaries);dump(OUT/'windows.json',all_windows)
    pd.DataFrame(records).to_csv(OUT/'metrics.csv',index=False);pd.DataFrame(summaries).to_csv(OUT/'summary.csv',index=False)
    dump(OUT/'provenance.json',{'label_sha256':sha(label_path),'selection_sha256':sha(OUT/'selection.json'),
         'contract_sha256':sha(CONTRACT_PATH),'manifest_sha256':sha(ROOT/'configs/expanded-manifest.json'),
         'test_label_access':'after selection.json freeze','evaluation':'custom events, not official NAB; no point adjustment'})
    print(pd.DataFrame(summaries).query("subset == 'all'").to_string(index=False))


def main():
    global OUT
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--stage',choices=['calibrate','test','all'],default='all');ap.add_argument('--output',type=Path,default=OUT);args=ap.parse_args();OUT=args.output.resolve()
    manifest=json.loads((ROOT/'configs/expanded-manifest.json').read_text());contract=json.loads(CONTRACT_PATH.read_text())
    if manifest['contract_sha256']!=sha(CONTRACT_PATH):raise ValueError('Rerun prepare_expanded.py after changing the benchmark contract')
    for meta in manifest['candidates']:
        if meta['admitted'] and sha(ROOT/meta['input'])!=meta['processed_sha256']:raise ValueError('Processed data changed: '+meta['input'])
    frozen=calibrate(manifest,contract) if args.stage in ['calibrate','all'] else json.loads((OUT/'selection.json').read_text())
    if args.stage in ['test','all']:test(manifest,contract,frozen)


if __name__=='__main__':main()

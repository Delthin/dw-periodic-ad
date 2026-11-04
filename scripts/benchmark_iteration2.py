#!/usr/bin/env python3
"""Second development round: causal multi-scale evidence and literature baselines.

NAB test results were already exposed. They are development evidence, not a new
blind test. Thresholds and per-series configurations still use only the original
training/validation prefixes; original labels are opened only after selection.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler
from evaluation import evaluate
from benchmark_expanded import aggregate, score_metric, versions, windows_for
from prepare_expanded import ROOT, RAW, dump, sha

CONTRACT=ROOT/'configs/iteration2.json'
JAR=ROOT/'runner/target/periodic-ad-runner.jar'
BASES=('periodic','rolling_mad')


@contextmanager
def fixed_runner(out,expected):
    """Use a private byte-identical JAR, unaffected by concurrent target rebuilds."""
    global JAR
    original=JAR;copy=out/'private-runner.jar'
    if sha(original)!=expected:raise ValueError('Runner changed before snapshot')
    if copy.exists():
        if sha(copy)!=expected:raise ValueError('Existing private runner has a different version')
    else:
        shutil.copyfile(original,copy)
        if sha(copy)!=expected:raise ValueError('Runner changed while copying')
        copy.chmod(0o444)
    JAR=copy
    try:yield copy
    finally:JAR=original


def frame_read(path):return pd.read_csv(path,float_precision='round_trip')
def hash_array(a):return hashlib.sha256(np.ascontiguousarray(a,dtype=np.float64).tobytes()).hexdigest()
def cached(path, key, fn):
    meta=path.with_suffix('.cache.json')
    if path.exists() and meta.exists():
        record=json.loads(meta.read_text())
        if record.get('key')==key and record.get('sha256')==sha(path):
            return np.load(path)
    result=fn();path.parent.mkdir(parents=True,exist_ok=True);np.save(path,result)
    dump(meta,{'key':key,'sha256':sha(path)})
    return result


def injection_suites(frame,meta,c):
    train,end=meta['train_end_index'],meta['validation_end_index'];p=meta['period']
    prefix=frame.iloc[:end].copy();x=prefix.value.to_numpy(dtype=float);spec=c['injection']
    r=x[p:train]-x[:train-p];med=np.median(r)
    unit=max(1.4826*np.median(np.abs(r-med)),.02*np.subtract(*np.percentile(x[:train],[75,25])),1e-6)
    starts=[train+round((end-train)*f) for f in spec['fractions']]
    output={'background':(prefix,[])};ts=prefix.timestamp_ms.to_numpy(dtype=np.int64)
    for suite in ('short','long','flatline'):
        y=x.copy();windows=[]
        for j,a in enumerate(starts):
            desired=spec['short_durations'][j] if suite=='short' else max(1,round(p*spec['long_period_fractions'][j]))
            following=starts[j+1] if j+1<len(starts) else end
            b=a+min(desired,max(1,(following-a)//2))
            amplitude=spec['signs'][j]*spec['amplitudes'][j]*unit
            if suite=='flatline':y[a:b]=np.median(x[:train])+spec['signs'][j]*3*unit
            else:y[a:b]+=amplitude
            windows.append({'start_ms':int(ts[a]),'end_ms':int(ts[b-1]),'points':b-a,
                            'noise_unit':float(unit),'amplitude':float(amplitude),'suite':suite})
        f=prefix.copy();f['value']=y;output[suite]=(f,windows)
    return output


def base_traces(meta,inputs,folder):
    """Unmodified production Java, raw residual diagnostics, cache bound to inputs/JAR."""
    folder.mkdir(parents=True,exist_ok=True);jobs=[]
    for name,path in inputs.items():
        for base in BASES:
            cfg=folder/f'{name}-{base}.properties'
            config={'step_ms':meta['step_ms'],'origin_ms':meta['origin_ms'],'period':meta['period'],
                    'k':3,'window':meta['period'],'tau':4,'clip':2,'epsilon':'auto',
                    'strategy':'bounded','method':base,'score_mode':'point'}
            text=''.join(f'{k}={v}\n' for k,v in config.items());cfg.write_text(text)
            out=folder/f'{name}-{base}.csv';stamp=out.with_suffix('.cache.json')
            key={'input':sha(path),'jar':sha(JAR),'config':hashlib.sha256(text.encode()).hexdigest()}
            record=json.loads(stamp.read_text()) if stamp.exists() else {}
            valid=out.exists() and record.get('key')==key and record.get('sha256')==sha(out)
            if not valid:jobs.append((path.resolve(),out.resolve(),cfg.resolve(),stamp,key))
    if jobs:
        jobfile=folder/'jobs.tsv';jobfile.write_text(''.join('\t'.join(map(str,row[:3]))+'\n' for row in jobs))
        with (folder/'java.log').open('w') as log:
            subprocess.run(['java','-jar',str(JAR),'batch',str(jobfile)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
        for _,out,_,stamp,key in jobs:dump(stamp,{'key':key,'sha256':sha(out)})
    return {name:{base:frame_read(folder/f'{name}-{base}.csv') for base in BASES} for name in inputs}


def z_from_trace(trace,ts,floor):
    z=np.full(len(ts),np.nan);ix=np.searchsorted(ts,trace.timestamp_ms.to_numpy(dtype=np.int64))
    np.testing.assert_array_equal(ts[ix],trace.timestamp_ms.to_numpy(dtype=np.int64))
    z[ix]=(trace.residual.to_numpy()-trace.residual_center.to_numpy())/np.maximum(trace.scale.to_numpy(),floor)
    return z


def evidence_channels(z,mode,c):
    spec=c['candidate_evidence'];finite=np.isfinite(z);a=np.full((len(z),1),np.nan)
    if mode=='point':a[finite,0]=np.abs(z[finite]);return a
    if mode not in ('ewma6','ewma12'):raise ValueError('Unsupported frozen evidence mode: '+mode)
    span,clip=(6,spec['winsor_clip']) if mode=='ewma6' else (12,spec['ewma12_clip'])
    alpha=2./(span+1)
    starts=np.flatnonzero(finite&~np.r_[False,finite[:-1]])
    ends=np.flatnonzero(finite&~np.r_[finite[1:],False])+1
    for first,last in zip(starts,ends):
        state=0.
        for i in range(first,last):
            value=max(-clip,min(clip,float(z[i])))
            state=(1-alpha)*state+alpha*value
            a[i,0]=abs(state)
    return a


def choose(ts,scores,windows,meta,c):
    mask=(ts>=meta['validation_start_ms'])&(ts<meta['test_start_ms'])
    bg=scores['background'];candidates=[]
    if not all(np.isfinite(v[mask]).all() for v in scores.values()):raise ValueError('Nonfinite calibration score')
    event_budget=max(c['validation_event_alarm_budget'],(meta['test_start_ms']-meta['validation_start_ms'])/86400000*c.get('validation_events_per_day',0.))
    for threshold in np.unique(np.quantile(bg[mask],c['threshold_quantiles'],method=c.get('threshold_quantile_method','linear'))):
        threshold=max(float(threshold),1e-12);alarm=float(np.mean(bg[mask]>threshold));detail={}
        bg_metric,_=evaluate(ts,bg>threshold,[],meta['validation_start_ms'],meta['test_start_ms'],meta['step_ms'],gap_ms=c['event_merge_gap_ms'])
        for suite in ('short','long','flatline'):
            metric,_=evaluate(ts,scores[suite]>threshold,windows[suite],meta['validation_start_ms'],meta['test_start_ms'],meta['step_ms'],gap_ms=c['event_merge_gap_ms'])
            truth=np.zeros(len(ts),dtype=bool)
            for w in windows[suite]:truth|=(ts>=w['start_ms'])&(ts<=w['end_ms'])
            neg=mask&~truth
            metric['outside_point_fraction']=float(np.mean(scores[suite][neg]>threshold)) if neg.any() else 0.
            den=5*metric['tp']+4*metric['fn']+metric['fp']
            metric['f2']=5*metric['tp']/den if den else 0.
            detail[suite]={k:metric[k] for k in ('tp','fp','fn','recall','f1','f2','outside_point_fraction')}
        tail=max(v['outside_point_fraction'] for v in detail.values())
        candidates.append({'threshold':threshold,'background_fraction':alarm,'background_events':bg_metric['fp'],'max_outside_fraction':tail,
                           'tail_excess':max(0.,tail-c['injection_outside_point_budget']),
                           'mean_f2':float(np.mean([v['f2'] for v in detail.values()])),
                           'mean_recall':float(np.mean([v['recall'] for v in detail.values()])),
                           'within_background_budget':alarm<=c['validation_point_alarm_budget'] and bg_metric['fp']<=event_budget,
                           'background_event_budget':event_budget,
                           'within_tail_budget':tail<=c['injection_outside_point_budget'],'suites':detail})
    bgok=[r for r in candidates if r['within_background_budget']]
    ok=[r for r in bgok if r['within_tail_budget']]
    key=lambda r:(r['mean_f2'],r['mean_recall'],-r['max_outside_fraction'],-r['background_fraction'],r['threshold'])
    if ok:best=max(ok,key=key)
    else:best=max(bgok,key=lambda r:(-r['max_outside_fraction'],*key(r)))
    return {'selected':best,'candidates':candidates,'tail_fallback':not bool(ok)}


def selection_key(result):
    r=result['selected']
    return (not result['tail_fallback'], -r['tail_excess'],r['mean_f2'],r['mean_recall'],-r['max_outside_fraction'],-r['background_fraction'],r['threshold'])


def adaptive_calibrate(meta,suites,folder,c):
    inputs={name:folder/f'{name}.csv' for name in suites}
    for name,(f,_) in suites.items():f.to_csv(inputs[name],index=False)
    traces=base_traces(meta,inputs,folder/'java')
    prefix=suites['background'][0];ts=prefix.timestamp_ms.to_numpy(dtype=np.int64)
    valid=(ts>=meta['validation_start_ms'])&(ts<meta['test_start_ms']);windows={k:v[1] for k,v in suites.items()}
    candidates={};arrays={};parameters={}
    bases=c['candidate_evidence']['base']
    if c['candidate_evidence'].get('base_selection')=='training_periodicity':
        bases=['periodic'] if meta['strong_periodicity'] else ['robust_ar']
    for base in bases:
        model=None
        if base=='robust_ar':
            from predictive_baseline import fit_predictor,prediction_trace
            model=fit_predictor(prefix.value.to_numpy(dtype=float),meta['train_end_index'],meta['period'],c['candidate_evidence']['autoregressive'])
            dump(folder/'autoregressive-model.json',model)
            from production_scoring import java_batch
            raw=java_batch(inputs,meta,{'base':'robust_ar','autoregressive_model':model},c,folder/'java-production',JAR,'ar-raw',raw_trace=True)
            checks={}
            for name,(f,_) in suites.items():
                reference=prediction_trace(f,model,meta['period']);actual=raw[name]
                np.testing.assert_array_equal(actual.timestamp_ms.to_numpy(),reference.timestamp_ms.to_numpy())
                cols=['baseline','residual','residual_center','scale']
                np.testing.assert_allclose(actual[cols].to_numpy(),reference[cols].to_numpy(),rtol=1e-11,atol=1e-10)
                checks[name]={'rows':len(actual),'max_absolute_error':{col:float(np.max(np.abs(actual[col].to_numpy()-reference[col].to_numpy()))) for col in cols}}
                traces[name][base]=actual
            dump(folder/'autoregressive-reference-check.json',checks)
        bg=traces['background'][base];train=bg[bg.timestamp_ms<meta['validation_start_ms']]
        if len(train)==0:raise ValueError('No pre-validation scale calibration points')
        floor=max(float(np.median(train.scale))*.5,1e-9)
        z={name:z_from_trace(traces[name][base],ts,floor) for name in suites}
        level_floor=None
        if base=='robust_ar' and c['candidate_evidence'].get('autoregressive_context'):
            level_train=traces['background']['rolling_mad'];level_train=level_train[level_train.timestamp_ms<meta['validation_start_ms']]
            level_floor=max(float(np.median(level_train.scale))*.5,1e-9)
            for name in suites:
                level=z_from_trace(traces[name]['rolling_mad'],ts,level_floor)
                z[name]=np.sign(z[name])*np.sqrt(np.abs(z[name])*np.abs(level))
        modes=c['candidate_evidence']['autoregressive_scores'] if base=='robust_ar' else c['candidate_evidence']['scores']
        for mode in modes:
            channels={name:evidence_channels(v,mode,c) for name,v in z.items()}
            norms=np.maximum(np.quantile(channels['background'][valid],.99,axis=0),1e-8)
            scores={name:np.max(v/norms,axis=1) for name,v in channels.items()}
            key=base+'__'+mode;parameters[key]={'base':base,'mode':mode,'scale_floor':floor,'channel_scales':norms.tolist()}
            if model is not None:parameters[key]['autoregressive_model']=model
            if level_floor is not None:parameters[key]['level_scale_floor']=level_floor
            from production_scoring import java_batch,aligned_scores
            scored=java_batch(inputs,meta,parameters[key],c,folder/'java-production',JAR,key)
            actual={name:aligned_scores(trace,ts) for name,trace in scored.items()};errors={}
            for name in scores:
                np.testing.assert_allclose(actual[name],scores[name],rtol=1e-11,atol=1e-10,equal_nan=True)
                errors[name]=float(np.nanmax(np.abs(actual[name]-scores[name])))
            dump(folder/f'{key}-reference-check.json',errors)
            candidates[key]=choose(ts,actual,windows,meta,c);arrays[key]=actual
    winner=max(candidates,key=lambda k:selection_key(candidates[k]))
    result={'variant':winner,'parameters':parameters[winner],**candidates[winner],'all_variants':candidates}
    return result,arrays[winner]


def reference_calibrate(meta,suites,folder,c):
    """Original point and capped-EWMA algorithms under the SAME new calibration protocol."""
    ts=suites['background'][0].timestamp_ms.to_numpy(dtype=np.int64)
    traces=base_traces(meta,{name:folder/f'{name}.csv' for name in suites},folder/'java')
    z={name:z_from_trace(traces[name]['periodic'],ts,0.) for name in suites}
    windows={name:v[1] for name,v in suites.items()};results={};all_scores={}
    for method,mode in [('periodic_point','point'),('periodic_robust_ewma','ewma12')]:
        scores={name:evidence_channels(values,mode,c)[:,0] for name,values in z.items()}
        params={'base':'periodic','mode':mode,'scale_floor':0.,'channel_scales':[1.]}
        from production_scoring import java_batch,aligned_scores
        scored=java_batch({name:folder/f'{name}.csv' for name in suites},meta,params,c,folder/'java-production',JAR,method)
        actual={name:aligned_scores(trace,ts) for name,trace in scored.items()}
        for name in scores:np.testing.assert_allclose(actual[name],scores[name],rtol=1e-11,atol=1e-10,equal_nan=True)
        results[method]={'variant':'periodic__'+mode,'parameters':params,**choose(ts,actual,windows,meta,c)}
        all_scores[method]=actual
    return results,all_scores


def adaptive_reference_scores(meta,frame,p,folder,c):
    if p['base']=='robust_ar':
        from predictive_baseline import prediction_trace
        trace=prediction_trace(frame,p['autoregressive_model'],meta['period'])
    else:trace=base_traces(meta,{'test':ROOT/meta['input']},folder)['test'][p['base']]
    ts=frame.timestamp_ms.to_numpy(dtype=np.int64);z=z_from_trace(trace,ts,p['scale_floor'])
    if 'level_scale_floor' in p:
        level_trace=base_traces(meta,{'test':ROOT/meta['input']},folder)['test']['rolling_mad']
        level=z_from_trace(level_trace,ts,p['level_scale_floor'])
        z=np.sign(z)*np.sqrt(np.abs(z)*np.abs(level))
    return np.max(evidence_channels(z,p['mode'],c)/np.array(p['channel_scales']),axis=1)


def adaptive_production_scores(meta,frame,result,folder,c,tag):
    from production_scoring import java_batch,aligned_scores
    p=result['parameters'];threshold=result['selected']['threshold'];ts=frame.timestamp_ms.to_numpy(dtype=np.int64)
    trace=java_batch({'test':ROOT/meta['input']},meta,p,c,folder/'production',JAR,tag,threshold=threshold)['test']
    actual=aligned_scores(trace,ts);reference=adaptive_reference_scores(meta,frame,p,folder/'reference',c)
    np.testing.assert_allclose(actual,reference,rtol=1e-11,atol=1e-10,equal_nan=True)
    finite=np.isfinite(actual)&np.isfinite(reference)
    dump(folder/f'{tag}-java-reference-check.json',{'rows':int(finite.sum()),'max_absolute_error':float(np.max(np.abs(actual[finite]-reference[finite]))),
        'reference_threshold_flag_differences':int(np.count_nonzero((actual[finite]>threshold)!=(reference[finite]>threshold))),
        'production_flag_contract':'Java CSV flags equal round-trip Java score > frozen Java-calibrated threshold exactly',
        'score_source':'production Java; Python is a floating-point mathematical reference'})
    return actual


def fit_feature_models(x,train,width,c):
    v=np.lib.stride_tricks.sliding_window_view(x[:train],width);scaler=StandardScaler().fit(v)
    samples=scaler.transform(v[np.linspace(0,len(v)-1,min(5000,len(v)),dtype=int)])
    return {'scaler':scaler,'width':width,
            'isolation_forest':IsolationForest(n_estimators=200,max_samples=min(256,len(samples)),random_state=c['seed'],n_jobs=1).fit(samples),
            'local_outlier_factor':LocalOutlierFactor(n_neighbors=min(20,len(samples)-1),novelty=True,n_jobs=1).fit(samples)}


def literature_calibrate(meta,suites,folder,c):
    from literature_baselines import spectral_residual_online,left_matrix_profile
    ts=suites['background'][0].timestamp_ms.to_numpy(dtype=np.int64);windows={k:v[1] for k,v in suites.items()}
    x={k:v[0].value.to_numpy(dtype=float) for k,v in suites.items()};candidate_scores={};candidate_params={}
    source_hash=sha(ROOT/'scripts/literature_baselines.py')
    for width in c['baselines']['isolation_forest']['widths']:
        models=fit_feature_models(x['background'],meta['train_end_index'],width,c)
        model_path=folder/f'features-{width}.joblib';joblib.dump(models,model_path,compress=3)
        for method in ('isolation_forest','local_outlier_factor'):
            key=f'{method}__w{width}';candidate_scores[key]={};candidate_params[key]={'method':method,'width':width,'model_file':model_path.name}
            for suite,v in x.items():
                a=np.full(len(v),np.nan);features=models['scaler'].transform(np.lib.stride_tricks.sliding_window_view(v,width))
                a[width-1:]=-models[method].score_samples(features);candidate_scores[key][suite]=a
    for method,func in [('spectral_residual',spectral_residual_online),('left_matrix_profile',left_matrix_profile)]:
        for width in c['baselines'][method]['windows']:
            key=f'{method}__w{width}';candidate_scores[key]={};candidate_params[key]={'method':method,'width':width}
            for suite,v in x.items():
                cache_key={'x':hash_array(v),'source':source_hash,'method':method,'width':width,'history':2048}
                fn=lambda v=v,width=width:func(v,window=width,**({'history':2048} if method=='left_matrix_profile' else {}))
                candidate_scores[key][suite]=cached(folder/'scores'/f'{suite}-{key}.npy',cache_key,fn)
    results={};selected_scores={}
    for method in c['baselines']:
        variants={key:choose(ts,scores,windows,meta,c) for key,scores in candidate_scores.items() if key.startswith(method+'__')}
        winner=max(variants,key=lambda k:selection_key(variants[k]))
        results[method]={'variant':winner,'parameters':candidate_params[winner],**variants[winner],'all_variants':variants}
        selected_scores[method]=candidate_scores[winner]
    return results,selected_scores


def implementation_hashes(group):
    files=['benchmark_iteration2.py','benchmark_expanded.py','prepare_expanded.py','evaluation.py','predictive_baseline.py','production_scoring.py']
    if group!='adaptive':files.append('literature_baselines.py')
    return {f:sha(ROOT/'scripts'/f) for f in files}


def calibration_fingerprint(group):
    return {'contract_sha256':sha(CONTRACT),'manifest_sha256':sha(ROOT/'configs/expanded-manifest.json'),
            'implementation_sha256':implementation_hashes(group),'runner_sha256':sha(JAR),'versions':versions()}


def calibrate(manifest,c,out,group):
    if (out/'selection.json').exists():raise FileExistsError('Use a fresh output directory; frozen selection already exists')
    initial=calibration_fingerprint(group)
    if c!=json.loads(CONTRACT.read_text()) or manifest!=json.loads((ROOT/'configs/expanded-manifest.json').read_text()):raise ValueError('Inputs changed while loading')
    dump(out/'run-start.json',initial);selections={};model_hashes={};start=time.time()
    with fixed_runner(out,initial['runner_sha256']):
        for meta in manifest['candidates']:
            if not meta['admitted']:continue
            folder=out/meta['id'];folder.mkdir(parents=True,exist_ok=True)
            frame=frame_read(ROOT/meta['input']);suites=injection_suites(frame,meta,c)
            dump(folder/'injections.json',{name:win for name,(_,win) in suites.items()});selection={}
            if group!='literature':
                selection['adaptive_evidence'],_=adaptive_calibrate(meta,suites,folder,c)
                references,_=reference_calibrate(meta,suites,folder,c);selection.update(references)
            if group!='adaptive':
                lit,_=literature_calibrate(meta,suites,folder,c);selection.update(lit)
            selections[meta['id']]=selection;dump(folder/'selection.json',selection)
            model_hashes.update({str(p.relative_to(out)):sha(p) for p in folder.glob('*.joblib')})
            print('calibrated',meta['id'],{k:(v['variant'],round(v['selected']['mean_f2'],3)) for k,v in selection.items()},flush=True)
    if calibration_fingerprint(group)!=initial:raise ValueError('Implementation/config/runner changed during calibration; selection not frozen')
    for meta in manifest['candidates']:
        if meta['admitted'] and sha(ROOT/meta['input'])!=meta['processed_sha256']:raise ValueError('Input changed during calibration')
    frozen={'selections':selections,'group':group,**initial,'model_sha256':model_hashes,
            'elapsed_seconds':time.time()-start,'label_access':'none in this calibration; NAB development labels were exposed in previous round'}
    dump(out/'selection.json',frozen);return frozen


def test(manifest,c,out,frozen):
    with fixed_runner(out,frozen['runner_sha256']):return test_with_frozen_runner(manifest,c,out,frozen)


def test_with_frozen_runner(manifest,c,out,frozen):
    if frozen['contract_sha256']!=sha(CONTRACT) or frozen['manifest_sha256']!=sha(ROOT/'configs/expanded-manifest.json') or frozen['implementation_sha256']!=implementation_hashes(frozen['group']) or frozen['runner_sha256']!=sha(JAR) or frozen['versions']!=versions():raise ValueError('Frozen inputs/implementation changed')
    for p,h in frozen['model_sha256'].items():
        if sha(out/p)!=h:raise ValueError('Frozen model changed')
    label_path=RAW/'labels/combined_windows.json'
    if not label_path.exists():label_path=RAW/'combined_windows.json'
    if sha(label_path)!=manifest['label_sha256']:raise ValueError('Labels changed')
    labels=json.loads(label_path.read_text());records=[];all_windows=[]
    for meta in manifest['candidates']:
        if not meta['admitted']:continue
        folder=out/meta['id'];testdir=folder/'test';testdir.mkdir(exist_ok=True)
        frame=frame_read(ROOT/meta['input']);ts=frame.timestamp_ms.to_numpy(dtype=np.int64);x=frame.value.to_numpy(dtype=float);scores={}
        selected=frozen['selections'][meta['id']]
        for method in ('adaptive_evidence','periodic_point','periodic_robust_ewma'):
            if method in selected:
                scores[method]=adaptive_production_scores(meta,frame,selected[method],testdir,c,method)
        for method,result in selected.items():
            if method in ('adaptive_evidence','periodic_point','periodic_robust_ewma'):continue
            p=result['parameters'];width=p['width']
            if method in ('isolation_forest','local_outlier_factor'):
                models=joblib.load(folder/p['model_file']);v=models['scaler'].transform(np.lib.stride_tricks.sliding_window_view(x,width))
                a=np.full(len(x),np.nan);a[width-1:]=-models[method].score_samples(v);scores[method]=a
            else:
                from literature_baselines import spectral_residual_online,left_matrix_profile
                fn=spectral_residual_online if method=='spectral_residual' else left_matrix_profile
                scores[method]=fn(x,window=width,**({'history':2048} if method=='left_matrix_profile' else {}))
        ix=4*meta['period'];t=ts[ix:];windows=windows_for(labels[meta['source']]);table=pd.DataFrame({'timestamp_ms':t,'value':x[ix:]})
        for method,a in scores.items():
            result=selected[method];threshold=result['selected']['threshold'];s=a[ix:]
            metric,events=score_metric(t,s,threshold,windows,meta);table[method]=s;table[method+'_flag']=s>threshold
            row={'id':meta['id'],'source':meta['source'],'family':meta['family'],'method':method,'variant':result['variant'],
                 'previously_evaluated':True,'strong_periodicity':meta['strong_periodicity'],
                 **{k:v for k,v in metric.items() if k not in ('windows','point_window_proxy','definition')},
                 'point_fp':metric['point_window_proxy']['fp'],'point_tp':metric['point_window_proxy']['tp'],'point_fn':metric['point_window_proxy']['fn']}
            records.append(row);dump(testdir/f'{method}-events.json',events)
            for w in metric['windows']:
                mask=(t>=w['start_ms'])&(t<=w['end_ms'])&(t>=meta['test_start_ms'])&(t<meta['end_ms_exclusive']);sc=s[mask]
                all_windows.append({'id':meta['id'],'source':meta['source'],'method':method,'variant':result['variant'],**w,
                   'threshold':threshold,'score_interval_start_ms':int(t[mask][0]) if len(sc) else None,
                   'score_interval_end_ms':int(t[mask][-1]) if len(sc) else None,'max_score':float(np.max(sc)) if len(sc) else None,
                   'max_score_to_threshold':float(np.max(sc)/threshold) if len(sc) else None,
                   'positive_points':int(np.count_nonzero(sc>threshold)),'points':len(sc)})
        table.to_csv(testdir/'all-scores.csv',index=False);print('evaluated',meta['id'],flush=True)
    rows=aggregate(records);dump(out/'metrics.json',records);dump(out/'summary.json',rows);dump(out/'windows.json',all_windows)
    pd.DataFrame(records).to_csv(out/'metrics.csv',index=False);pd.DataFrame(rows).to_csv(out/'summary.csv',index=False);pd.DataFrame(all_windows).to_csv(out/'windows.csv',index=False)
    dump(out/'provenance.json',{'selection_sha256':sha(out/'selection.json'),'label_sha256':sha(label_path),'scope':'NAB development iteration; not fresh blind test'})
    print(pd.DataFrame(rows).query("subset=='all'").to_string(index=False))


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--group',choices=['adaptive','literature','all'],default='all');ap.add_argument('--stage',choices=['calibrate','test','all'],default='all');args=ap.parse_args()
    c=json.loads(CONTRACT.read_text());manifest=json.loads((ROOT/'configs/expanded-manifest.json').read_text());out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    for meta in manifest['candidates']:
        if meta['admitted'] and sha(ROOT/meta['input'])!=meta['processed_sha256']:raise ValueError('Input changed')
    frozen=calibrate(manifest,c,out,args.group) if args.stage in ('calibrate','all') else json.loads((out/'selection.json').read_text())
    if args.stage in ('test','all'):test(manifest,c,out,frozen)

if __name__=='__main__':main()

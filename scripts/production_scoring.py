#!/usr/bin/env python3
"""Map frozen statistical parameters to the production Java scorer."""
import hashlib
import subprocess
from pathlib import Path
import json
import numpy as np
import pandas as pd
from prepare_expanded import ROOT,dump,sha


def detector_properties(meta,p,c,threshold,raw_trace=False):
    if p['base'] not in ('periodic','robust_ar'):raise ValueError('Unsupported final base')
    props={'step_ms':meta['step_ms'],'origin_ms':meta['origin_ms'],'period':meta['period'],'k':3,'window':meta['period'],
           'tau':4,'clip':2,'epsilon':'auto','strategy':'bounded','method':p['base']}
    if raw_trace:props['score_mode']='point'
    else:
        if len(p['channel_scales'])!=1:raise ValueError('Final scorer has one scalar evidence channel')
        mode=p['mode'];span={'point':1,'ewma6':6,'ewma12':12}[mode]
        clip='none' if mode=='point' else c['candidate_evidence']['winsor_clip'] if mode=='ewma6' else c['candidate_evidence']['ewma12_clip']
        props.update({'score_mode':'ewma','evidence_span':span,'evidence_tau':threshold,'evidence_clip':clip,
                      'evidence_scale_floor':p['scale_floor'],'evidence_normalizer':p['channel_scales'][0]})
    if p['base']=='robust_ar':
        model=p['autoregressive_model']
        for field in ('center','scale','intercept','lag_clip'):props['ar_'+field]=model[field]
        props['ar_lags']=','.join(map(str,model['lags']));props['ar_coefficients']=','.join(map(str,model['coefficients']))
        props['ar_level_context']='true' if not raw_trace and 'level_scale_floor' in p else 'false'
        props['ar_level_scale_floor']=p.get('level_scale_floor',0.) if not raw_trace else 0.
    return props


def java_batch(inputs,meta,p,c,folder,jar,tag,threshold=.5,raw_trace=False):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);jobs=[];outputs={}
    for name,source in inputs.items():
        props=detector_properties(meta,p,c,threshold,raw_trace)
        config=folder/f'{name}-{tag}.properties';text=''.join(f'{k}={v}\n' for k,v in props.items());config.write_text(text)
        output=folder/f'{name}-{tag}.csv';record=output.with_suffix('.cache.json');outputs[name]=output
        key={'input':sha(source),'jar':sha(jar),'properties':hashlib.sha256(text.encode()).hexdigest()}
        old=json.loads(record.read_text()) if record.exists() else {}
        if not (output.exists() and old.get('key')==key and old.get('sha256')==sha(output)):
            jobs.append((Path(source).resolve(),output.resolve(),config.resolve(),record,key))
    if jobs:
        batch=folder/f'{tag}-jobs.tsv';batch.write_text(''.join('\t'.join(map(str,row[:3]))+'\n' for row in jobs))
        with (folder/f'{tag}-java.log').open('w') as log:
            subprocess.run(['java','-jar',str(jar),'batch',str(batch.resolve())],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
        for _,output,_,record,key in jobs:dump(record,{'key':key,'sha256':sha(output)})
    frames={name:pd.read_csv(path,float_precision='round_trip') for name,path in outputs.items()}
    for frame in frames.values():
        if not np.isfinite(frame.score.to_numpy()).all():raise ValueError('Nonfinite production score')
        if not raw_trace:np.testing.assert_array_equal(frame.flag.to_numpy(dtype=bool),frame.score.to_numpy()>threshold)
    return frames


def aligned_scores(trace,timestamps):
    result=np.full(len(timestamps),np.nan);positions=np.searchsorted(timestamps,trace.timestamp_ms.to_numpy(dtype=np.int64))
    np.testing.assert_array_equal(timestamps[positions],trace.timestamp_ms.to_numpy(dtype=np.int64))
    result[positions]=trace.score.to_numpy();return result

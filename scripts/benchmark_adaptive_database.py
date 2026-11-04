#!/usr/bin/env python3
"""Time the frozen AR branch in local IoTDB vs fetched rows + the same Java core.
The three workloads tile one public NAB series; they measure execution cost only.
Requires the already-running project-local IoTDB and the new periodic_ad deployment.
Creates only project-owned benchmark devices; never drops data or starts services.
"""
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from prepare_expanded import ROOT,dump,sha
from production_scoring import detector_properties


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--selection',type=Path,default=ROOT/'results/iteration2/final-nab/selection.json')
    ap.add_argument('--id',default='realAWSCloudwatch__ec2_cpu_utilization_24ae8d')
    ap.add_argument('--repeats',type=int,default=5)
    ap.add_argument('--output',type=Path,default=ROOT/'results/iteration2/database/performance')
    args=ap.parse_args()
    if args.repeats<1:raise ValueError('repeats must be positive')
    frozen=json.loads(args.selection.read_text());selected=frozen['selections'][args.id]['adaptive_evidence']
    p=selected['parameters']
    if p['base']!='robust_ar':raise ValueError('Choose a frozen robust_ar example to exercise this branch')
    manifest=json.loads((ROOT/'configs/expanded-manifest.json').read_text())
    meta=next(m for m in manifest['candidates'] if m.get('id')==args.id)
    source=ROOT/meta['input'];values=pd.read_csv(source,float_precision='round_trip').value.to_numpy(dtype=float)
    c=json.loads((ROOT/'configs/iteration2.json').read_text());jar=ROOT/'runner/target/periodic-ad-runner.jar'
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    inputs=ROOT/'data/processed/benchmark-iteration2';inputs.mkdir(parents=True,exist_ok=True)
    runs=[]
    for n in [10000,100000,1000000]:
        path=inputs/f'tiled-nab-{n}.csv';t=meta['origin_ms']+np.arange(n,dtype=np.int64)*meta['step_ms']
        frame=pd.DataFrame({'timestamp_ms':t,'source_timestamp':pd.to_datetime(t,unit='ms',utc=True).strftime('%Y-%m-%d %H:%M:%S'),'value':np.resize(values,n)})
        frame.to_csv(path,index=False,float_format='%.17g')
        folder=out/str(n);folder.mkdir(parents=True,exist_ok=True)
        props=detector_properties(meta,p,c,selected['selected']['threshold'])
        old=folder/'summary.json'
        if old.exists():props['db_device']=json.loads(old.read_text())['device'] # verifyRaw still checks every timestamp/value
        config=folder/'config.properties';config.write_text(''.join(f'{k}={v}\n' for k,v in props.items()))
        with (folder/'execution.log').open('w') as log:
            subprocess.run(['java','-Xmx256m','-jar',str(jar),'benchmark',str(path),str(config),str(folder),str(args.repeats)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
        result=json.loads((folder/'summary.json').read_text());result.update(name=f'tiled-nab-{n}',input_sha256=sha(path));runs.append(result)
        print(n,result['A_udf'],result['B_raw_java'],flush=True)
    dump(out/'summary.json',{'runner_sha256':sha(jar),'selection_sha256':sha(args.selection),'source_id':args.id,'source_sha256':sha(source),
        'variant':selected['variant'],'parameters':p,'threshold':selected['selected']['threshold'],
        'scope':'Frozen model; tiled public NAB workloads, not independent accuracy data. Local warm cache; 2 warmup rounds, alternating measured order, query through full result consumption; import and file output excluded.',
        'repeats':args.repeats,'runs':runs})

if __name__=='__main__':main()

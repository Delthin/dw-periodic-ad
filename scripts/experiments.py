#!/usr/bin/env python3
"""Reproduce calibration, held-out evaluation, mechanism ablations and diagnostics.

Only Java computes production scores. Python supplies an independent numerical
oracle and evaluates events. Candidate rankings are persisted before test labels
are read. All generated traces/logs stay in ignored results/.
"""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
import os
import platform
import subprocess
import time
from pathlib import Path
import numpy as np
import pandas as pd
from evaluation import evaluate, read_scores, reference_periodic

ROOT = Path(__file__).resolve().parents[1]
JAR = ROOT / "runner/target/periodic-ad-runner.jar"
CONTRACT = json.loads((ROOT/"configs/experiment.json").read_text())


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n")


def properties(path, cfg):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k,v in cfg.items()))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def batch(jobs, folder, stem):
    if not jobs:
        return 0.0
    jobfile = folder/f"{stem}.tsv"
    jobfile.write_text("".join("\t".join(str(x.resolve()) for x in job)+"\n" for job in jobs))
    start = time.perf_counter()
    with (folder/f"{stem}.log").open("w") as log:
        done = subprocess.run(["java","-Xmx768m","-jar",str(JAR),"batch",str(jobfile)],
                              cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    if done.returncode:
        raise RuntimeError(f"Java batch failed ({done.returncode}); see {folder}/{stem}.log")
    return time.perf_counter()-start


def load_splits(path):
    obj = json.loads(path.read_text())
    return obj.get("boundaries",obj)


def load_windows(path):
    obj = json.loads(path.read_text())
    return obj.get("windows",[]) if isinstance(obj,dict) else obj


def configuration(df, period, train_end):
    return {"step_ms":int(df.timestamp_ms.iloc[1]-df.timestamp_ms.iloc[0]),
            "origin_ms":int(df.timestamp_ms.iloc[0]),"period":period,"k":4,"window":period,
            "tau":4,"clip":3,"epsilon":"auto","strategy":"bounded","method":"periodic",
            "freeze_at_ms":int(train_end)}


def inject(df, splits, period):
    """Six fixed-time perturbations; amplitudes use training data only."""
    val = splits["validation"]
    step = int(df.timestamp_ms.iloc[1]-df.timestamp_ms.iloc[0])
    day = 86_400_000//step
    copy = df[df.timestamp_ms < val["end_ms_exclusive"]].copy()
    copy["value"] = copy["value"].astype(float)
    ids = np.flatnonzero((copy.timestamp_ms >= val["start_ms"]).to_numpy())
    train = copy[copy.timestamp_ms < val["start_ms"]].value.to_numpy()
    amplitude = max(1e-9,abs(float(np.median(train)))*CONTRACT["injection"]["amplitude_train_median_fraction"])
    windows=[]
    spec=CONTRACT["injection"]
    previous_end=-1
    for j,(fraction,duration,sign) in enumerate(zip(spec["fractions"],spec["duration_cycles"],spec["signs"])):
        start=int(ids[min(len(ids)-1,int(fraction*len(ids)))])
        length=max(1,round(duration*day))
        stop=min(start+length,int(ids[-1])+1)
        if start <= previous_end:
            raise ValueError("injected windows overlap under this sampling/split")
        copy.loc[start:stop-1,"value"] += sign*amplitude
        windows.append({"index":j,"start_ms":int(copy.timestamp_ms.iloc[start]),
                        "end_ms":int(copy.timestamp_ms.iloc[stop-1]),"amplitude":sign*amplitude})
        previous_end=stop-1
    return copy,windows


def calibrate(name,df,splits,period,folder,sensitivity=False):
    folder.mkdir(parents=True,exist_ok=True)
    val=splits["validation"]
    clean=folder/"calibration-clean.csv"
    df[df.timestamp_ms < val["end_ms_exclusive"]].to_csv(clean,index=False)
    perturbed,windows=inject(df,splits,period)
    injected=folder/"calibration-injected.csv"
    perturbed.to_csv(injected,index=False)
    dump(folder/"injected-windows.json",windows)
    base=configuration(df,period,val["start_ms"])
    variants=[("bounded","periodic","bounded"), ("direct","periodic","direct"),
              ("fixed","fixed","direct"), ("rolling_mad","rolling_mad","direct"),
              ("rolling_z","rolling_z","direct")]
    if sensitivity:
        variants=[("daily_bounded","periodic","bounded")]
    jobs=[]; candidates=[]; kernels={}
    for label,method,strategy in variants:
        ks=CONTRACT["k_candidates"] if method in ("periodic","fixed") else [4]
        cs=CONTRACT["clip_candidates"] if strategy=="bounded" else [3]
        for k,c in itertools.product(ks,cs):
            cfg={**base,"method":method,"strategy":strategy,"k":k,"clip":c}
            key=f"{label}-k{k}-c{c}"
            prop=folder/f"{key}.properties"
            properties(prop,cfg)
            for source,kind in [(clean,"background"),(injected,"injected")]:
                output=folder/f"{key}-{kind}.csv"
                jobs.append((source,output,prop))
            kernels[key]=(folder/f"{key}-background.csv",folder/f"{key}-injected.csv")
            for tau in CONTRACT["tau_candidates"]:
                candidates.append({"label":label,"kernel":key,"config":{**cfg,"tau":tau}})
    elapsed=batch(jobs,folder,"calibration-jobs")
    score_cache={key:tuple(read_scores(p) for p in paths) for key,paths in kernels.items()}
    rankings=[]
    for cand in candidates:
        bg,inj=score_cache[cand["kernel"]]
        tau=cand["config"]["tau"]
        start,end=val["start_ms"],val["end_ms_exclusive"]
        if (bg.timestamp_ms.between(start,end-1).sum() != val["rows"]):
            raise ValueError("candidate warmup extends into validation; unsupported split")
        bm,_=evaluate(bg.timestamp_ms,bg.score>tau,[],start,end,base["step_ms"])
        im,_=evaluate(inj.timestamp_ms,inj.score>tau,windows,start,end,base["step_ms"])
        rankings.append({**cand,"background_events_per_day":bm["all_events_per_day"],
                         "injected_event_f1":im["f1"],"injected_tp":im["tp"],"injected_fp":im["fp"],
                         "budget_met":bm["all_events_per_day"]<=CONTRACT["background_events_per_day_budget"]})
    selected={}
    for label,_,_ in variants:
        rows=[r for r in rankings if r["label"]==label]
        eligible=[r for r in rows if r["budget_met"]]
        if eligible:
            key=lambda r:(-r["injected_event_f1"],r["background_events_per_day"],r["config"]["k"],r["config"]["clip"],-r["config"]["tau"])
        else:
            eligible=rows
            key=lambda r:(r["background_events_per_day"],-r["injected_event_f1"],r["config"]["k"],r["config"]["clip"],-r["config"]["tau"])
        selected[label]=min(eligible,key=key)
    # No test labels have been opened by this function or its caller yet.
    dump(folder/"selection.json",{"dataset":name,"selection_frozen_utc":pd.Timestamp.now(tz="UTC").isoformat(),
        "selected":selected,"candidates":rankings,"candidate_count":len(rankings),
        "java_replays":len(jobs),"wall_seconds":elapsed,
        "reuse":"tau does not affect bounded/direct/fixed/rolling state; each shared trace evaluates all tau candidates",
        "validation_background_note":"unmodified validation; any genuine but unlabelled events still count against alarm budget"})
    return selected


def run_dataset(name, input_path, split_path, label_path, period, folder, primary=False):
    df=pd.read_csv(input_path)
    splits=load_splits(split_path)
    folder.mkdir(parents=True,exist_ok=True)
    chosen=calibrate(name,df,splits,period,folder/"calibration")
    if primary:
        chosen.update(calibrate(name,df,splits,CONTRACT["sensitivity_period"],folder/"daily-calibration",True))
    selected={label:r["config"] for label,r in chosen.items()}
    main=selected["bounded"]
    selected["ablation_direct"]={**main,"strategy":"direct"}
    selected["ablation_freeze"]={**main,"strategy":"freeze"}
    selected["ablation_no_period"]={**main,"method":"no_period"}
    jobs=[]
    for label,cfg in selected.items():
        prop=folder/f"{label}.properties"
        properties(prop,cfg)
        jobs.append((input_path,folder/f"{label}-scores.csv",prop))
    batch(jobs,folder,"heldout-jobs")
    # Freeze selection + replay untouched history before reading test labels.
    windows=load_windows(label_path)
    test=splits["test"]
    metrics={};scores={}
    for label,cfg in selected.items():
        score=read_scores(folder/f"{label}-scores.csv"); scores[label]=score
        if score.timestamp_ms.between(test["start_ms"],test["end_ms_exclusive"]-1).sum()!=test["rows"]:
            raise ValueError(f"{label} does not cover common test timestamps")
        m,events=evaluate(score.timestamp_ms,score.score>cfg["tau"],windows,test["start_ms"],test["end_ms_exclusive"],cfg["step_ms"])
        metrics[label]={**m,"parameters":cfg,"calibrated_independently":not label.startswith("ablation")}
        pd.DataFrame(events,columns=["start_ms","end_ms","points","window_index","classification"]).to_csv(folder/f"{label}-events.csv",index=False)
    dump(folder/"metrics.json",metrics)
    curve=[]
    for label in ["bounded","direct","fixed","rolling_mad","rolling_z"]:
        score=scores[label]
        for tau in CONTRACT["tau_candidates"]:
            m,_=evaluate(score.timestamp_ms,score.score>tau,windows,test["start_ms"],test["end_ms_exclusive"],main["step_ms"])
            curve.append({"method":label,"tau":tau,**{k:m[k] for k in ["precision","recall","f1","outside_events_per_day"]}})
    pd.DataFrame(curve).to_csv(folder/"threshold-diagnostic.csv",index=False)
    if primary:
        oracle=reference_periodic(df.timestamp_ms,df.value,main)
        actual=scores["bounded"]
        np.testing.assert_array_equal(oracle.timestamp_ms,actual.timestamp_ms)
        columns=["baseline","residual","residual_center","scale","score","update"]
        np.testing.assert_allclose(actual[columns],oracle[columns],atol=1e-9,rtol=1e-8)
        np.testing.assert_array_equal(actual.score>main["tau"],oracle.flag)
        dump(folder/"independent-reference.json",{"rows":len(actual),"passed":True,
            "atol":1e-9,"rtol":1e-8,"max_abs_error":float(np.abs(actual[columns].to_numpy()-oracle[columns].to_numpy()).max())})
        phase_analysis=[]
        s=actual[(actual.timestamp_ms>=test["start_ms"])].copy()
        member=np.zeros(len(s),dtype=bool)
        for win in windows:
            member|=(s.timestamp_ms>=win["start_ms"])&(s.timestamp_ms<=win["end_ms"])
        s["outside"]=~member
        s["hour"]=pd.to_datetime(s.timestamp_ms,unit="ms",utc=True).dt.hour
        for hour,g in s[s.outside].groupby("hour"):
            phase_analysis.append({"source_hour":int(hour),"outside_points":len(g),
                                   "flagged_points":int((g.score>main["tau"]).sum()),
                                   "flag_fraction":float((g.score>main["tau"]).mean())})
        pd.DataFrame(phase_analysis).to_csv(folder/"hourly-background-errors.csv",index=False)
    return {"name":name,"input_sha256":sha(input_path),"period":period,"rows":len(df),
            "splits":splits,"selected":chosen,"metrics":metrics,"path":str(folder.relative_to(ROOT))}


def diagnostic_synthetic(folder):
    """Controlled changes with exact onset, not additional real-data evidence."""
    folder.mkdir(parents=True,exist_ok=True)
    rng=np.random.default_rng(CONTRACT["seed"])
    p=48;n=2400;origin=1_700_006_400_000;step=1_800_000
    t=np.arange(n);base=100+15*np.sin(2*np.pi*t/p)+rng.normal(0,2,n)
    clean=base.copy()
    cases={"spike":(800,801),"sustained_drop":(800,1136),"permanent_step":(800,n),"slow_drift":(800,n)}
    jobs=[]
    for case,(a,b) in cases.items():
        values=base.copy()
        if case=="spike":values[a:b]+=30
        elif case=="slow_drift":values[a:b]+=np.linspace(0,30,b-a)
        else:values[a:b]-=30
        df=pd.DataFrame({"timestamp_ms":origin+t*step,"source_timestamp":pd.to_datetime(origin+t*step,unit="ms",utc=True).strftime("%Y-%m-%d %H:%M:%S"),"value":values})
        input=folder/f"{case}.csv";df.to_csv(input,index=False)
        for strategy in ["bounded","direct","freeze"]:
            cfg=configuration(df,p,origin+600*step);cfg.update(k=4,tau=4,clip=3)
            cfg["strategy"]=strategy
            prop=folder/f"{case}-{strategy}.properties";properties(prop,cfg)
            jobs.append((input,folder/f"{case}-{strategy}-scores.csv",prop))
    batch(jobs,folder,"synthetic-jobs")
    diagnostics=[]
    for case,(a,b) in cases.items():
        for strategy in ["bounded","direct","freeze"]:
            df=read_scores(folder/f"{case}-{strategy}-scores.csv")
            ids=((df.timestamp_ms-origin)//step).astype(int)
            affected=(ids>=a)&(ids<b)
            flags=df.score>4
            first=df[affected&flags]
            target=clean[ids.to_numpy()]+(-30 if case=="permanent_step" else 0)
            baseline_error=np.abs(df.baseline.to_numpy()+df.residual_center.to_numpy()-target)
            # First completed trailing cycle whose median prediction error <= 3 noise sigma.
            # Report the cycle's END, not a start found by looking into its future.
            adaptation=None
            if case=="permanent_step":
                for start in range(a,n-p+1):
                    segment=(ids>=start)&(ids<start+p)
                    if np.median(baseline_error[segment])<=6:
                        adaptation=(start+p-a)*step/3_600_000;break
            diagnostics.append({"case":case,"strategy":strategy,"onset_index":a,"affected_points":int(affected.sum()),
                "flagged_affected_fraction":float(flags[affected].mean()),
                "first_alert_delay_hours":None if first.empty else float((first.timestamp_ms.iloc[0]-(origin+a*step))/3_600_000),
                "adaptation_hours_error_le_6_for_cycle":adaptation,
                "late_affected_alarm_fraction":float(flags[affected & (ids>=max(a,b-p))].mean()),
                "post_return_alarm_fraction":None if b==n else float(flags[(ids>=b)&(ids<min(b+2*p,n))].mean()),
                "task_interpretation":"persistent anomaly: retained alerts desirable; allowed level change: adaptation desirable" if case=="permanent_step" else "synthetic mechanism diagnostic"})
    dump(folder/"summary.json",diagnostics)


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",default="results/experiment");parser.add_argument("--skip-supplement",action="store_true");parser.add_argument("--only",choices=["primary","supplement","synthetic"])
    args=parser.parse_args();folder=(ROOT/args.output).resolve();folder.mkdir(parents=True,exist_ok=True)
    started=pd.Timestamp.now(tz="UTC").isoformat();start=time.perf_counter()
    summaries=[]
    if args.only in (None,"primary"):
        summaries.append(run_dataset("nyc_taxi",ROOT/"data/processed/nyc_taxi.csv",ROOT/"data/processed/splits.json",ROOT/"data/processed/events.json",CONTRACT["period"],folder/"nyc_taxi",True))
    if not args.skip_supplement and args.only in (None,"supplement"):
        manifest=json.loads((ROOT/"configs/supplement-manifest.json").read_text())
        for item in manifest["selected"]:
            stem=item["stem"]
            summaries.append(run_dataset(stem,ROOT/f"data/processed/supplement/{stem}.csv",ROOT/f"data/processed/supplement/{stem}.splits.json",ROOT/f"data/processed/supplement/{stem}.events.json",item["period"],folder/stem))
    if args.only in (None,"synthetic"):diagnostic_synthetic(folder/"synthetic")
    suffix=f"-{args.only}" if args.only else ""
    dump(folder/f"summary{suffix}.json",{"started_utc":started,"ended_utc":pd.Timestamp.now(tz="UTC").isoformat(),
        "wall_seconds":time.perf_counter()-start,"git_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        "git_status":subprocess.check_output(["git","status","--short"],cwd=ROOT,text=True),
        "source_hashes":{str(p.relative_to(ROOT)):sha(p) for p in sorted(ROOT.rglob("*.java")) if ".worktrees" not in p.parts},
        "experiment_config_sha256":sha(ROOT/"configs/experiment.json"),"platform":platform.platform(),
        "seed":CONTRACT["seed"],"datasets":summaries,"status":"complete"})
    print(f"Completed {len(summaries)} datasets: {folder}")

if __name__=="__main__":main()

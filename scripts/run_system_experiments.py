#!/usr/bin/env python3
"""Run the local, warm-cache Java A/B benchmark and sample process RSS/CPU.

Requires an already-running project IoTDB and deployed periodic_ad. Does not
start/stop services, delete database series, or flush operating-system caches.
Resource samples include import/raw verification/warmup as well as timed rounds;
they are whole-command measurements, not isolated server UDF measurements.
"""
from pathlib import Path
import argparse
import json
import os
import re
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]

def process(pid):
    p=subprocess.run(["ps","-p",str(pid),"-o","rss=,time="],capture_output=True,text=True)
    fields=p.stdout.strip().split()
    if len(fields)!=2:return None
    rss,cpu=fields
    days=0
    if "-" in cpu:
        d,cpu=cpu.split("-",1);days=int(d)
    bits=[float(x) for x in cpu.split(":")]
    seconds=days*86400+sum(v*60**i for i,v in enumerate(reversed(bits)))
    return {"rss_bytes":int(rss)*1024,"cpu_seconds":seconds}

def project_servers():
    text=subprocess.check_output(["ps","-axo","pid=,command="],text=True)
    runtime=str(ROOT/".local/runtime")
    rows={}
    for line in text.splitlines():
        fields=line.strip().split(maxsplit=1)
        if len(fields)==2 and runtime in fields[1] and re.search(r"org\.apache\.iotdb\.(?:confignode|db\.service)\.",fields[1]):
            rows[int(fields[0])]=process(int(fields[0]))
    if not rows:raise RuntimeError("No project IoTDB JVM found; start and deploy explicitly first")
    return rows

def run(name,input_path,config,repeat):
    output=ROOT/"results/database"/name;output.mkdir(parents=True,exist_ok=True)
    server_start=project_servers()
    java=str(Path(os.environ["JAVA_HOME"])/"bin/java") if "JAVA_HOME" in os.environ else "java"
    command=[java,"-Xmx256m","-jar",str(ROOT/"runner/target/periodic-ad-runner.jar"),"benchmark",str(input_path),str(config),str(output),str(repeat)]
    samples=[];start=time.monotonic()
    with (output/"execution.log").open("w") as log:
        proc=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        try:
            while proc.poll() is None:
                samples.append({"elapsed_seconds":time.monotonic()-start,"client":process(proc.pid),"servers":{str(p):process(p) for p in server_start}})
                time.sleep(1)
        except BaseException:
            proc.terminate();proc.wait(timeout=20);raise
    end={str(p):process(p) for p in server_start}
    resources={"exit_code":proc.returncode,"elapsed_seconds":time.monotonic()-start,"sample_interval_seconds":1,"samples":len(samples),
        "scope":"whole benchmark command including import, verification, warmup and measured rounds; not per-path isolated resource consumption",
        "client_peak_sampled_rss_bytes":max((x["client"]["rss_bytes"] for x in samples if x["client"]),default=None),
        "server_peak_sampled_rss_bytes":{str(p):max((x["servers"][str(p)]["rss_bytes"] for x in samples if x["servers"][str(p)]),default=None) for p in server_start},
        "server_cpu_seconds_delta":{str(p):(end[str(p)]["cpu_seconds"]-server_start[p]["cpu_seconds"] if end[str(p)] and server_start[p] else None) for p in server_start},
        "server_start":{str(p):v for p,v in server_start.items()},"server_end":end}
    (output/"process-resources.json").write_text(json.dumps(resources,indent=2)+"\n")
    (output/"process-samples.json").write_text(json.dumps(samples,indent=2)+"\n")
    if proc.returncode:raise RuntimeError(f"Benchmark failed ({proc.returncode}); see {output/'execution.log'}")
    print(name,json.loads((output/"summary.json").read_text())["A_udf"],flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--only",choices=["all","taxi","100000","1000000"],default="all");ap.add_argument("--repeats",type=int,default=5);args=ap.parse_args()
    jobs=[("taxi","nyc_taxi-benchmark",ROOT/"data/processed/nyc_taxi.csv",ROOT/"configs/detector.properties")]
    jobs += [(str(n),f"periodic-{n}",ROOT/f"data/processed/benchmark/periodic-{n}.csv",ROOT/"configs/detector.properties") for n in [100000,1000000]]
    for key,name,input_path,config in jobs:
        if args.only in ("all",key):run(name,input_path,config,args.repeats)
if __name__=="__main__":main()

#!/usr/bin/env python3
"""Deterministic scaling fixtures; never used to claim real-event accuracy."""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd


def prepare(root, sizes=(100000,1000000), seed=20261015):
    folder=root/"data/processed/benchmark"
    folder.mkdir(parents=True,exist_ok=True)
    n=max(sizes);t=np.arange(n,dtype=np.int64)
    rng=np.random.default_rng(seed)
    values=15000+5000*np.sin(2*np.pi*t/48)+2000*np.cos(2*np.pi*t/336)+rng.normal(0,300,n)
    values[t%50000==25000]+=6000
    times=1404172800000+t*1_800_000
    # The Java CSV contract preserves source coordinates; milliseconds drive phase.
    df=pd.DataFrame({"timestamp_ms":times,"source_timestamp":pd.to_datetime(times,unit="ms",utc=True).strftime("%Y-%m-%d %H:%M:%S"),"value":values})
    for size in sizes:
        path=folder/f"periodic-{size}.csv"
        df.iloc[:size].to_csv(path,index=False)
        print(f"{size:,} rows -> {path}")

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--sizes",type=int,nargs="+",default=[100000,1000000]);args=parser.parse_args()
    prepare(Path(__file__).resolve().parents[1],args.sizes)

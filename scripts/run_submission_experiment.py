#!/usr/bin/env python3
"""Run the complete NAB-only experiment in a fresh source-code directory."""
import argparse,os,subprocess,sys
from pathlib import Path
from prepare_expanded import ROOT

def main():
    p=argparse.ArgumentParser(description=__doc__);p.parse_args()
    if (ROOT/'results/iteration2/final-nab/selection.json').exists():
        raise FileExistsError('Use a fresh source-only directory; archived selections must not be overwritten.')
    env=os.environ.copy()
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):env[key]='1'
    commands=[['scripts/prepare_expanded.py'],['scripts/benchmark_iteration2.py','--output','results/iteration2/final-nab','--group','all','--stage','all'],['scripts/export_submission_replay.py'],['scripts/reproduce_submission.py','--no-package-check','--output','results/refit-fusion']]
    for command in commands:
        print('+',sys.executable,*command,flush=True)
        subprocess.run([sys.executable,*command],cwd=ROOT,env=env,check=True)
    print('NAB component results: results/iteration2/final-nab/; two fusion presets and validation-selected final method: results/refit-fusion/.')
if __name__=='__main__':main()

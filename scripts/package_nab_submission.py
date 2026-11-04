#!/usr/bin/env python3
"""Create explicit NAB-only code/data and evidence archives."""
import argparse,hashlib,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TITLE='9-李思懿-周期时间序列异常检测'
SCRIPTS='benchmark_adaptive_database.py benchmark_expanded.py benchmark_iteration2.py build_final_submission.py build_nab_submission.py revise_original_for_submission.py check_default_compatibility.sh deploy_detector.sh env.sh evaluation.py export_submission_replay.py iotdb.sh literature_baselines.py nab_fusion.py package_nab_submission.py predictive_baseline.py prepare_expanded.py production_scoring.py reproduce_submission.py run_submission_experiment.py setup_runtime.py'.split()
CONFIGS='benchmark-expanded.json expanded-manifest.json iteration2.json iotdb-local.properties detector.properties temporal-evidence.properties robust-evidence.properties adaptive-evidence.properties robust-ar.properties'.split()
DOCS=['最终版-代码使用说明.md','最终版-报告正文.md','最终版-测试用例与实验描述.md','最终版-论文与实现来源.md']
FAMILIES={'realAWSCloudwatch','realKnownCause','realTweets','realTraffic','realAdExchange'}
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'results/submission/deliverables');a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    code={};evidence={}
    def add(dest,path,relative=None,required=True):
        path=Path(path)
        if not path.is_file():
            if required:raise FileNotFoundError(path)
            return
        if path.is_symlink():raise ValueError('Refuse symlink: '+str(path))
        rel=relative or str(path.relative_to(ROOT))
        if rel in dest and dest[rel]!=path:raise ValueError('Duplicate package path '+rel)
        dest[rel]=path
    def tree(dest,path):
        for file in path.rglob('*'):
            if file.is_file() and '__pycache__' not in file.parts and file.name!='.DS_Store' and file.suffix!='.pyc':add(dest,file)
    add(code,ROOT/'docs/最终版-代码使用说明.md','README.md')
    for name in ['pom.xml','requirements.in','requirements-lock.txt','requirements-docs.txt']:add(code,ROOT/name)
    for module in ['detector-core','detector-udf','runner']:
        add(code,ROOT/module/'pom.xml');tree(code,ROOT/module/'src')
    for name in SCRIPTS:add(code,ROOT/'scripts'/name)
    for name in CONFIGS:add(code,ROOT/'configs'/name)
    for name in DOCS:add(code,ROOT/'docs'/name)
    for name in ['test_evaluation.py','test_literature_baselines.py']:add(code,ROOT/'tests'/name)
    tree(code,ROOT/'tests/fixtures/pre-evidence-clip')
    add(code,ROOT/'assets/presentation-template.pptx')
    add(code,ROOT/'assets/original-project-report.pptx')
    for name in ['detector.sql','temporal-evidence.sql','robust-evidence.sql','adaptive-evidence.sql','robust-ar.sql']:add(code,ROOT/'sql'/name)
    for name in ['runner/target/periodic-ad-runner.jar','detector-udf/target/periodic-ad-udf.jar']:add(code,ROOT/name)
    raw=ROOT/'data/raw/post-feedback-nab'
    for family in FAMILIES:tree(code,raw/family)
    for name in ['LICENSE.txt','tree.json','data/README.md','labels/combined_windows.json']:add(code,raw/name)
    tree(code,ROOT/'data/processed/expanded')
    tree(evidence,ROOT/'results/submission/nab');tree(evidence,ROOT/'results/submission/nab-replay')
    final=ROOT/'results/iteration2/final-nab'
    for file in final.rglob('*'):
        # All selected models/parameters and common full-history score table; no repeated calibration CSVs.
        if file.is_file() and (file.suffix in {'.json','.joblib','.properties','.log'} or (file.suffix=='.csv' and (file.parent==final or file.name=='all-scores.csv'))):add(evidence,file)
    j=ROOT/'results/iteration2/java'
    add(evidence,j/'verification.json');add(evidence,j/'main-package.log');tree(evidence,j/'surefire');tree(evidence,j/'default-compatibility')
    for sub in [j,j/'robust-ar']:
        for file in sub.iterdir():
            if file.is_file() and file.suffix in {'.log','.exit','.json'}:add(evidence,file)
    for file in (j/'robust-ar/replays').rglob('*'):
        if file.is_file() and file.suffix in {'.json','.log','.properties'}:add(evidence,file)
    tree(evidence,ROOT/'results/iteration2/literature-baseline-checks')
    for file in (ROOT/'results/iteration2/database').rglob('*'):
        if file.is_file() and (file.suffix in {'.json','.properties','.log','.sql','.exit'} or file.name=='timings.csv'):add(evidence,file)
    for name in ['final-nab-run.log','final-nab-run.exit','performance-run.log','performance-run.exit']:
        add(evidence,ROOT/'results/iteration2'/name,required=False)
    # Fail on accidental scope expansion; source documentation is also explicitly selected above.
    for dest in [code,evidence]:
        for rel in dest:
            lower=rel.lower()
            if any(part in Path(rel).parts for part in ['.git','.local','.venv','.worktrees']) or any(x in lower for x in ['aiops','ucr','business-kpi','iteration3','experiment-focus']):raise ValueError('Out-of-scope package path: '+rel)
    if set(code)&set(evidence):raise ValueError('Packages must have disjoint entries')
    reports=[]
    for label,kind,files in [('代码与数据','code',code),('实验结果','evidence',evidence)]:
        path=out/f'{TITLE}-{label}.zip';entries=[{'path':rel,'bytes':src.stat().st_size,'sha256':sha(src)} for rel,src in sorted(files.items())]
        manifest={'package':label,'scope':'NAB only: 47 real candidates, 21 selected sequences, 22 evaluation windows; fusion remains Python','files':entries}
        with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for rel,src in sorted(files.items()):z.write(src,'dw-periodic-ad/'+rel)
            z.writestr(f'dw-periodic-ad/package-manifest-{kind}.json',json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        with zipfile.ZipFile(path) as z:
            bad=z.testzip()
            if bad is not None:raise ValueError('Bad ZIP member '+bad)
        reports.append({'file':path.name,'files':len(entries),'bytes':path.stat().st_size,'sha256':sha(path)})
    (out/'压缩包校验.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n');print(json.dumps(reports,ensure_ascii=False,indent=2))
if __name__=='__main__':main()

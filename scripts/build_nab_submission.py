#!/usr/bin/env python3
"""Build NAB documents and revise the actual original 12-slide presentation."""
import argparse,csv,json,shutil
from pathlib import Path
from build_final_submission import mdtable,md_to_docx
from revise_original_for_submission import revise
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'results/submission/nab';TITLE='9-李思懿-周期时间序列异常检测'
METHODS=['periodic_point','periodic_robust_ewma','adaptive_evidence','complementary_evidence','fast_complementary_evidence','adaptive_complementary_fusion','isolation_forest','local_outlier_factor','spectral_residual','left_matrix_profile']
NAMES={'periodic_point':'周期点检测','periodic_robust_ewma':'慢速周期 EWMA','adaptive_evidence':'自适应分支','complementary_evidence':'固定慢速融合','fast_complementary_evidence':'固定快速融合','adaptive_complementary_fusion':'最终快慢融合','isolation_forest':'Isolation Forest','local_outlier_factor':'LOF','spectral_residual':'SR','left_matrix_profile':'Left-MP'}
def load(p):return json.loads(p.read_text())
def pct(x):return f'{float(x)*100:.2f}%'
def report_table(data):
    return mdtable(['方法','TP','FP','FN','精确率','召回率','事件 F1','窗外点 FPR'],[[NAMES[m],data[m]['tp'],data[m]['fp'],data[m]['fn'],f"{float(data[m]['micro_precision']):.3f}",f"{float(data[m]['micro_recall']):.3f}",f"{float(data[m]['micro_f1']):.4f}",pct(data[m]['outside_point_fpr'])] for m in METHODS])
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--template',type=Path,default=ROOT/'assets/original-project-report.pptx');p.add_argument('--output',type=Path,default=ROOT/'results/submission/deliverables');a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    data=load(DATA/'summary.json')['all'];shutil.copytree(DATA/'figures',out/'figures',dirs_exist_ok=True)
    report=(ROOT/'docs/最终版-报告正文.md').read_text().replace('<!-- TABLE:NAB -->',report_table(data)).replace('<!-- TABLE:PERF -->',mdtable(['输入点数','库内 UDF/ms','取原值＋同 Java/ms'],[['10,000','153.8','124.0'],['100,000','1,240.4','1,205.0'],['1,000,000','13,011.1','11,895.0']]))
    for suffix,body in [('-说明文档',report),('-测试用例与实验描述',(ROOT/'docs/最终版-测试用例与实验描述.md').read_text())]:
        (out/(TITLE+suffix+'.md')).write_text(body);md_to_docx(body,out/(TITLE+suffix+'.docx'),out)
    annex=out/'附录';annex.mkdir(exist_ok=True)
    for title,path in [('论文与实现来源',ROOT/'docs/最终版-论文与实现来源.md'),('数据与逐窗口说明',DATA/'NAB数据与逐窗口说明.md')]:
        body=path.read_text();(annex/(title+'.md')).write_text(body);md_to_docx(body,annex/(title+'.docx'),out)
    for src,dest in [('comparison.csv','总体指标.csv'),('series-metrics.csv','逐序列对比.csv'),('window-metrics.csv','逐窗口对比.csv'),('candidate-sequences.csv','候选序列与来源.csv'),('window-summary.csv','22窗口摘要.csv'),('selected-fusion.json','融合模式与阈值.json'),('provenance.json','数据导出依据.json')]:shutil.copy2(DATA/src,annex/dest)
    revise(a.template,out)
    print(json.dumps({'slides':12,'output':str(out),'documents':4},ensure_ascii=False))
if __name__=='__main__':main()

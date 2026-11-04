#!/usr/bin/env python3
"""Sync the final Markdown documents and optional confirmed PPTX."""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'results/submission/nab'
TITLE = '9-李思懿-周期时间序列异常检测'
METHODS = ['periodic_point', 'periodic_robust_ewma', 'adaptive_evidence', 'complementary_evidence', 'fast_complementary_evidence', 'adaptive_complementary_fusion', 'isolation_forest', 'local_outlier_factor', 'spectral_residual', 'left_matrix_profile']
NAMES = {'periodic_point': '周期点检测', 'periodic_robust_ewma': '慢速周期 EWMA', 'adaptive_evidence': '自适应分支', 'complementary_evidence': '固定慢速融合', 'fast_complementary_evidence': '固定快速融合', 'adaptive_complementary_fusion': '最终快慢融合', 'isolation_forest': 'Isolation Forest', 'local_outlier_factor': 'LOF', 'spectral_residual': 'SR', 'left_matrix_profile': 'Left-MP'}


def mdtable(headers, rows):
    def line(cells):
        return '| ' + ' | '.join(str(x) for x in cells) + ' |'
    return '\n'.join([line(headers), line(['---'] * len(headers)), *[line(row) for row in rows]])


def report_table(data):
    return mdtable(['方法', 'TP', 'FP', 'FN', '精确率', '召回率', '事件 F1', '窗外点 FPR'], [
        [NAMES[m], data[m]['tp'], data[m]['fp'], data[m]['fn'],
         f"{float(data[m]['micro_precision']):.3f}", f"{float(data[m]['micro_recall']):.3f}",
         f"{float(data[m]['micro_f1']):.4f}", f"{float(data[m]['outside_point_fpr']) * 100:.2f}%"]
        for m in METHODS
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'deliverables/final')
    parser.add_argument('--pptx', type=Path, help='Optional existing, confirmed presentation to copy; never regenerates slides or speech notes.')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = (ROOT / 'docs/最终版-报告正文.md').read_text()
    if '<!-- TABLE:NAB -->' in report:
        data = json.loads((DATA / 'summary.json').read_text())['all']
        report = report.replace('<!-- TABLE:NAB -->', report_table(data))
    report = report.replace('<!-- TABLE:PERF -->', mdtable(
        ['输入点数', '库内 UDF/ms', '取原值＋同 Java/ms'],
        [['10,000', '153.8', '124.0'], ['100,000', '1,240.4', '1,205.0'], ['1,000,000', '13,011.1', '11,895.0']]
    ))
    (out / (TITLE + '-说明文档.md')).write_text(report)
    for source, destination in [
        ('最终版-测试用例与实验描述.md', TITLE + '-测试用例与实验描述.md'),
        ('最终版-代码使用说明.md', '代码使用说明.md'),
        ('最终版-论文与实现来源.md', '附录/论文与实现来源.md'),
        ('最终版-数据与逐窗口说明.md', '附录/数据与逐窗口说明.md'),
    ]:
        target = out / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'docs' / source, target)
    figures = DATA / 'figures'
    if not figures.exists():
        figures = ROOT / 'deliverables/final/figures'
    if figures.resolve() != (out / 'figures').resolve():
        shutil.copytree(figures, out / 'figures', dirs_exist_ok=True)
    presentation = args.pptx or ROOT / 'deliverables/final' / (TITLE + '.pptx')
    target = out / (TITLE + '.pptx')
    if presentation.exists() and presentation.resolve() != target.resolve():
        shutil.copyfile(presentation, target)
    print(f'已同步 5 份 Markdown 文档至 {out}；未生成 PDF、DOCX 或校验清单，未改讲稿。')


if __name__ == '__main__':
    main()

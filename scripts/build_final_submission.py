#!/usr/bin/env python3
"""Build the final Markdown/DOCX/PPTX from audited experiment artifacts.
Run with a Python containing python-pptx, python-docx and Pillow.
The original presentation is a required explicit template; its master is retained.
"""
import argparse
import json
import re
import shutil
from pathlib import Path
from datetime import datetime
from pptx import Presentation
from pptx.util import Inches,Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement
from docx import Document
from docx.shared import Cm,Pt as DPt,RGBColor as DColor
from docx.oxml import OxmlElement as DElem
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE

ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'results/post-feedback'
TITLE='9-李思懿-周期时间序列异常检测'
FONT='PingFang SC';PURPLE='660874';TEAL='267C8E';ORANGE='C45E30';INK='28232F';MUTED='6D6576'
NAMES={'rolling_mad':'滚动 MAD','seasonal_naive':'季节朴素','isolation_forest':'Isolation Forest','local_outlier_factor':'LOF',
       'periodic_point':'周期点分数','periodic_ewma':'原 EWMA','periodic_robust_ewma':'限幅 EWMA'}

def load(path):return json.loads(path.read_text())
def mdtable(headers,rows):return '\n'.join(['| '+' | '.join(map(str,headers))+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,row))+' |' for row in rows])
def pct(x):return f'{100*x:.2f}%'
def clean(s):return re.sub(r'\*\*|`','',s)

def set_font(run,size,color=INK,bold=False):
    run.font.name=FONT;run.font.size=Pt(size);run.font.bold=bold;run.font.color.rgb=RGBColor.from_string(color)
    rpr=run._r.get_or_add_rPr()
    for existing in list(rpr):
        if existing.tag.endswith('}ea'):rpr.remove(existing)
    ea=OxmlElement('a:ea');ea.set('typeface',FONT);rpr.append(ea)

def text(slide,x,y,w,h,content,size=20,color=INK,bold=False):
    box=slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h));tf=box.text_frame
    tf.clear();tf.word_wrap=True;tf.margin_left=tf.margin_right=Inches(.02);tf.margin_top=Inches(.01);tf.margin_bottom=0
    for i,line in enumerate(str(content).split('\n')):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph();p.space_after=Pt(8);p.line_spacing=1.15
        r=p.add_run();r.text=line;set_font(r,size,color,bold)
    return box

def rect(slide,x,y,w,h,color):
    sh=slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,Inches(x),Inches(y),Inches(w),Inches(h));sh.fill.solid();sh.fill.fore_color.rgb=RGBColor.from_string(color);sh.line.fill.background();sh._element.spPr.append(OxmlElement('a:effectLst'));return sh

def note(slide,body):slide.notes_slide.notes_text_frame.text=body

def table(slide,headers,rows,y=2.05,widths=None,size=17,x=.62,width=12.1):
    rowh=min(.52,4.0/(len(rows)+1));shape=slide.shapes.add_table(len(rows)+1,len(headers),Inches(x),Inches(y),Inches(width),Inches(rowh*(len(rows)+1)))
    tb=shape.table
    for j,w in enumerate(widths or [width/len(headers)]*len(headers)):tb.columns[j].width=Inches(w)
    for i,row in enumerate([headers]+rows):
        for j,value in enumerate(row):
            c=tb.cell(i,j);c.text=str(value);c.margin_left=Inches(.11);c.margin_right=Inches(.05);c.margin_top=Inches(.055);c.margin_bottom=Inches(.03)
            c.fill.solid();c.fill.fore_color.rgb=RGBColor.from_string(PURPLE if i==0 else ('F2EDF4' if i%2 else 'FAF8FB'))
            for p in c.text_frame.paragraphs:
                p.space_after=Pt(0)
                for r in p.runs:set_font(r,size,'FFFFFF' if i==0 else INK,i==0)
    return shape

def image(slide,path,x,y,w,h):
    from PIL import Image
    a,b=Image.open(path).size;scale=min(w/a,h/b);ww,hh=a*scale,b*scale
    slide.shapes.add_picture(str(path),Inches(x+(w-ww)/2),Inches(y+(h-hh)/2),width=Inches(ww),height=Inches(hh))


def build_deck(template,out,summary,nyc,performance):
    prs=Presentation(template)
    for sid in list(prs.slides._sldIdLst):prs.part.drop_rel(sid.rId);prs.slides._sldIdLst.remove(sid)
    for sh in prs.slide_master.shapes:
        if getattr(sh,'has_text_frame',False) and sh.text.strip()=='/ 12':
            for p in sh.text_frame.paragraphs:
                for r in p.runs:r.text=r.text.replace('/ 12','/ 20')
    # Keep the original purple master/layout, replace its unavailable Alibaba font
    # with the installed PingFang font, and prevent narrow inherited labels wrapping.
    def repair_chrome(shapes):
        for sh in shapes:
            if hasattr(sh,'shapes'):repair_chrome(sh.shapes)
            if getattr(sh,'has_text_frame',False):
                tf=sh.text_frame;tf.word_wrap=False
                tf.margin_left=tf.margin_right=tf.margin_top=tf.margin_bottom=0
                for p in tf.paragraphs:
                    for run in p.runs:
                        set_font(run,8 if '清华大学' in sh.text else 9,MUTED)
                        run._r.get_or_add_rPr().set('spc','0')
    repair_chrome(prs.slide_master.shapes)
    for layout in prs.slide_layouts:repair_chrome(layout.shapes)
    slides=[]
    def add(title,section=3,sub=None):
        sl=prs.slides.add_slide(prs.slide_layouts[section]);slides.append(sl)
        text(sl,.62,.88,12.1,.75,title,28,PURPLE,True)
        if sub:text(sl,.64,1.62,12.0,.46,sub,15,MUTED)
        # Opaque local page number avoids renderer-specific inherited field caches.
        rect(sl,12.02,7.12,.36,.30,'FFFFFF');text(sl,12.06,7.15,.29,.23,str(len(slides)),10,MUTED)
        return sl
    def foot(sl,body):text(sl,.65,6.55,12.0,.47,body,13,MUTED)
    s=add('周期时间序列异常检测',0,'IoTDB UDF 实现、独立基线比较与误报优化')
    text(s,.67,2.65,11.8,1.4,'从单序列案例\n走向多场景验证与代价分析',35,INK,True)
    text(s,.68,5.08,11.7,.65,'第 9 组  ·  李思懿 / 翁汇坤\n数据仓库与数据挖掘 · 最终提交版',20,MUTED)
    note(s,'本次补充重点是扩大真实测试范围、建立独立基线，并验证限幅证据累积的收益与边界。所有图表来自保存的实验结果。')
    s=add('根据反馈，补齐三类证据',0)
    table(s,['反馈','本次改动','验证方式'],[
        ['测试范围单一','1 条案例 → 21 条真实序列','3 类场景，22 个测试窗口'],
        ['缺少独立 baseline','4 个独立对照 + 2 项内部消融','统一分段、校准与评价规则'],
        ['误报仍然偏高','在 EWMA 输入端加入限幅','同时报告 FP、点 FPR、召回和延迟']],widths=[2.5,5.0,4.6],size=19)
    text(s,.68,4.55,11.7,1.2,'原来的“五个”是纽约出租车测试段的 5 个异常窗口。\n扩展数据仍来自同一个 NAB benchmark，不把序列数当成 benchmark 数。',21)
    s=add('问题：区分周期波动与持续异常',0)
    text(s,.7,2.18,7.5,3.7,'输入：规则采样的单变量时间序列。\n输出：当前异常分数、阈值标记与告警事件。\n约束：只能使用当前及过去观测，标签只用于评价。\n目标：抑制短时扰动带来的告警，保留持续偏移证据。',23)
    rect(s,9.0,2.18,3.6,3.4,'F2EDF4');text(s,9.25,2.5,3.1,2.8,'出租车需求\n云监控指标\n社交平台计数',25,PURPLE,True)
    foot(s,'本方法针对周期背景下的持续偏移；短尖峰另有召回要求，不能用单一工作点兼顾。')
    s=add('数据扩展：21 条真实序列，22 个测试窗口',0,'47 条真实候选按结构筛选；不插值、不去重、不按检测结果筛选')
    table(s,['类别','候选','纳入','说明'],[['AWS 云监控',17,9,'CPU、磁盘及应用指标'],['已知原因',7,2,'出租车需求、CPU 配置异常'],['Twitter 计数',10,10,'不同主题的计数变化'],['广告 / 道路交通',13,0,'网格不完整或训练历史不足']],widths=[3,1.3,1.3,6.5],size=19)
    text(s,.7,4.95,11.9,1.1,'共 223,878 点；测试段 89,559 点。\n16 条测试段含标签窗口，5 条无窗口序列仍用于统计误报。',21)
    foot(s,'14 条为此前未评估序列；7 条历史暴露单列。只有 3 条训练周期相关 ≥ 0.5，弱周期序列也保留。')
    s=add('冻结参数后再评测',0,'训练 40% → 验证 20% → 测试 40%，按时间顺序切分')
    table(s,['阶段','允许使用的信息','产物'],[['训练','数值、时间；不使用异常标签','日/周周期、尺度、ML 模型'],['验证','原始背景 + 6 个固定合成注入','每种方法独立阈值'],['冻结','数据 / 脚本 / JAR / 模型 / 依赖哈希','selection.json'],['测试','冻结完成后才解析标签','事件、点、逐窗口结果']],widths=[1.5,6.0,4.6],size=19)
    foot(s,'验证背景点报警比例 ≤ 1% 是选参约束，不是“已知正常点 FPR”，也不保证测试段 FPR ≤ 1%。')
    s=add('四个独立 baseline 与两项内部消融',1)
    table(s,['方法','核心做法','实现'],[['滚动 MAD','滚动中心与鲁棒尺度','Java'],['季节朴素','|xₜ − xₜ₋ₚ|','Python'],['Isolation Forest','200 棵树；max_samples = 256','scikit-learn'],['LOF novelty','局部密度；20 近邻','scikit-learn'],['周期点分数 / 原 EWMA','同一历史更新，只改变分数','Java'],['限幅 EWMA','先限幅，再累积带符号证据','Java']],widths=[3.5,6.0,2.6],size=17)
    foot(s,'IF / LOF：最多 24 点尾窗、训练集标准化、最多 5,000 训练窗。所有方法使用同一阈值选取和评价规则。')
    s=add('因果周期检测：先评分，再更新',1)
    text(s,.75,2.12,11.6,3.85,'① 同相位最近 K 个周期值的中位数 → 基线 bₜ\n② 残差 rₜ = xₜ − bₜ；过去 W 残差估计中心 mₜ 与尺度 sₜ\n③ 标准残差 zₜ = (rₜ − mₜ) / sₜ；点分数 = |zₜ|\n④ 同相位历史限幅更新，残差历史保留真实残差',23)
    foot(s,'尺度为 1.4826 × MAD，含 epsilon 下限。扩展实验 K = 3、W = P；原纽约案例保持 K = 6、W = 336。')
    s=add('改进：先限幅，再累积证据',1)
    rect(s,.7,2.12,11.9,1.3,'F2EDF4');text(s,1.0,2.43,11.3,.7,'eₜ = (1 − α)eₜ₋₁ + α · clip(zₜ, −4, 4)',29,PURPLE,True)
    text(s,.75,3.8,11.7,2.05,'α = 2 / 13；异常分数 = |eₜ|。\n零初始状态下，单个极端残差贡献最多 8 / 13；\n连续同号饱和证据需到第 9 点才越过阈值 3。',23)
    foot(s,'已有证据接近阈值时，单点仍可能触发；代价是短异常漏检与首警推迟。默认不启用限幅，兼容旧配置。')
    s=add('系统：同一 Java 核心，两条执行路径',2)
    for x,label in [(.8,'IoTDB 查询\nperiodic_ad UDF'),(8.25,'CSV / 原始数据\nJava Runner')]:
        rect(s,x,2.35,4.2,1.15,'F2EDF4');text(s,x+.18,2.58,3.84,.7,label,21,PURPLE,True)
    text(s,5.25,2.6,2.6,.5,'↘           ↙',26,TEAL)
    rect(s,3.75,4.0,5.9,1.0,'EAF2F4');text(s,4.1,4.28,5.2,.5,'detector-core · 独立流式状态',24,TEAL,True)
    text(s,.8,5.55,11.7,.65,'score / flag / event / active 统一参数；每个 UDF 实例独立维护状态。',21)
    foot(s,'Python 只负责数据准备、独立 ML 基线、校准与评价；主方法的所有基准分数来自生产 Java 核心。')
    s=add('评价：事件减少，不等于误报点减少',3)
    text(s,.75,2.12,11.7,3.75,'事件：相邻报警点间隔 ≤ 1 小时则合并；按事件首个时间归属。\nTP：每个标签窗口的首个事件。\nFP：窗口外事件 + 同一窗口中的重复事件。\n补充：窗口外点 FPR、召回、逐序列 F1、窗口代理 AP。',23)
    foot(s,'不做 point adjustment，不使用官方 NAB 分数。真实标签是宽窗口，首警相对窗口起点的位置不等于真实检测延迟。')
    s=add('纽约案例：仍命中 5/5，误报 29 → 11',3,'沿用旧划分与旧参数的探索性案例，测试段此前已暴露')
    image(s,BASE/'figures/nyc-errors.png',.7,2.0,8.2,4.25)
    text(s,9.1,2.45,3.5,3.2,'窗口外 FP\n12 → 0\n\n点 FPR\n0.771% → 0%',25,PURPLE,True)
    foot(s,'相对原 EWMA：总 FP 仅 12 → 11，重复 FP 9 → 11；相对点方法首警推迟 2.5–3.5 小时。')
    s=add('扩展测试：F1 改善，但未胜过所有基线',3,'21 条序列 / 22 个窗口；以下为事件级 micro 指标')
    allrows=[r for r in summary if r['subset']=='all'];table(s,['方法','TP','FP','FN','召回','F1'],[[NAMES[r['method']],r['tp'],r['fp'],r['fn'],pct(r['micro_recall']),f"{r['micro_f1']:.3f}"] for r in allrows],widths=[4.0,1.2,1.2,1.2,2.3,2.2],size=18)
    foot(s,'点方法 → 限幅：窗外点 FPR 0.950% → 1.201%。事件 FP 下降不能直接称作总体误报率下降。')
    s=add('新评估子集：收益与漏检同时存在',3,'14 条此前未评估序列 / 13 个测试窗口；不能替代全部 21 条结果')
    rows=[next(r for r in summary if r['subset']=='newly_evaluated' and r['method']==m) for m in ['periodic_point','periodic_ewma','periodic_robust_ewma','isolation_forest','local_outlier_factor']]
    table(s,['方法','TP / 窗口','FP','FN','F1'],[[NAMES[r['method']],f"{r['tp']} / 13",r['fp'],r['fn'],f"{r['micro_f1']:.3f}"] for r in rows],widths=[4.1,2.4,1.5,1.5,2.6],size=20)
    text(s,.75,5.35,11.7,.8,'限幅比原 EWMA 少 10 个误报事件，但两者都只检出 7/13。',23,PURPLE,True)
    s=add('受控实验：短异常召回是明确代价',3,'130 个固定案例 = 10 个种子 ×（无异常 + 4 种时长 × 3 种幅度）')
    image(s,BASE/'figures/synthetic-recall.png',.7,2.03,11.9,3.6)
    text(s,.75,5.75,11.6,.55,'12 小时、5σ：均命中 10/10，FP 19 → 0，限幅中位延迟 4.5 小时。',19,PURPLE,True)
    foot(s,'限幅在 3 小时异常上均为 0/10；12/24 小时、3σ 仅 2/10。不得把合成案例并入真实数据规模。')
    s=add('库内外一致性：四输出完全一致',2)
    text(s,.85,2.4,5.2,1.2,'7,968',58,PURPLE,True);text(s,.9,3.66,5.0,1.2,'纽约序列有效评分点\n两次独立查询一致',24)
    text(s,6.5,2.35,5.8,3.3,'score · flag · event · active\n逐行、逐时间戳核对\n最大绝对误差 = 0\n限幅模式与原点模式均通过',24)
    foot(s,'仅说明已测路径数值与状态一致；不是对所有数据、环境和参数的全称证明。')
    s=add('系统性能：查询到完整结果消费的实测',2,'本机暖缓存；2 轮预热 + 5 轮交替顺序测量；单位 ms，取中位数')
    rows=[]
    for r in performance['runs']:
        n=r['raw_returned_rows'];a=r['A_udf']['median_ms'];b=r['B_raw_java']['median_ms'];rows.append([f'{n:,}',f'{a:.1f}',f'{b:.1f}',f'{a/b:.2f}×'])
    table(s,['输入点数','SQL UDF','取原始数据 + Java','UDF / 库外'],rows,widths=[3,3,3.6,2.5],size=22)
    text(s,.75,4.75,11.7,1.2,'两条路径使用同一限幅 EWMA 核心，全部结果校验和一致。\n本机未观察到 UDF 加速；工程价值在于 SQL 集成与实现一致性。',21)
    foot(s,'不包含导入、文件输出；B 含取数与 Java 算法。未直接测量线上传输字节数，不宣称网络流量收益。')
    s=add('测试与复现：数据、模型、实现可追溯',2)
    table(s,['验证层','实际检查','结果'],[['Java 核心与适配','58 项受影响测试；120 × 200 默认兼容场景','通过'],['数据来源','固定 commit；递归 Git tree + 文件 blob 校验','通过'],['冻结约束','模型 / 依赖 / 脚本 / JAR / 数据变更检测','篡改副本被拒绝'],['数值传递','CSV round_trip 保留阈值边界双精度','回归用例通过'],['真实数据库','四输出逐行核对；三个规模性能','正确性一致']],widths=[2.5,7.2,2.4],size=17)
    foot(s,'脚本、固定数据、逐序列分数、阈值候选、测试记录与模型均随代码和实验材料提供。')
    s=add('结论：持续异常场景中的一种可选工作点',3)
    text(s,.75,2.15,11.65,3.9,'已完成：扩展真实数据、独立 baseline、限幅机制与 IoTDB 集成。\n收益：原纽约案例保留 5/5 命中，扩展测试事件 F1 明显改善。\n边界：召回下降、短异常漏检；全体点 FPR 未同步降低。\n后续：独立 benchmark、短 / 长双通道、成本敏感阈值与不规则采样。',23)
    foot(s,'保留默认点模式与原 EWMA，按业务容错需求选择；不将限幅模式作为无条件替代。')
    s=add('附录：逐序列差异与失败案例',3,'含标签的 16 条序列；表内为事件 F1，全部七方法结果见报告')
    per=load(BASE/'expanded/metrics.json');ids=list(dict.fromkeys(r['id'] for r in per if r['tp']+r['fn']>0));by={(r['id'],r['method']):r for r in per}
    for group,x in [(ids[:8],.65),(ids[8:],6.8)]:
        small=[]
        for idx in group:
            label=idx.split('__')[-1].replace('ec2_cpu_utilization_','EC2 ').replace('ec2_disk_write_bytes_','Disk ').replace('Twitter_volume_','Twitter ').replace('cpu_utilization_asg_misconfiguration','CPU config').replace('grok_asg_anomaly','grok ASG').replace('rds_cpu_utilization_','RDS ').replace('nyc_taxi','NYC taxi')
            r=by[idx,'periodic_point'];small.append([label,r['tp']+r['fn']]+[f"{by[idx,m]['f1']:.2f}" for m in ['periodic_point','periodic_robust_ewma','isolation_forest']])
        table(s,['序列','窗口','Point','Cap','IF'],small,x=x,width=5.85,widths=[2.25,.75,.95,.95,.95],size=15)
    foot(s,'Point = 周期点方法；Cap = 限幅 EWMA；IF = Isolation Forest。无窗口序列的误报仍计入总表。')
    s=add('参考资料与提交内容',3)
    text(s,.75,2.12,11.65,3.8,'数据：Numenta NAB，固定 commit ea702d75…，MIT 许可证。\n基线：scikit-learn IsolationForest / LocalOutlierFactor 官方文档。\n方法：Forecasting: Principles and Practice（季节朴素）；NIST（EWMA）。\n提交：展示 PPT、说明文档、测试用例与实验描述、代码及固定证据。',22)
    foot(s,'详细 URL、版本、命令与文件索引见说明文档。由组长提交本组一份作业。')
    assert len(prs.slides)==20
    for i,s in enumerate(slides):
        if not s.notes_slide.notes_text_frame.text.strip():note(s,f'第 {i+1} 页。对应数字见说明文档和 results/post-feedback 的原始结果；强调本页标题的结论与下方边界。')
    prs.save(out/f'{TITLE}.pptx')


def rich_paragraph(paragraph,content):
    """Keep source links clickable in Word and exported PDF."""
    paragraph.clear();position=0
    for match in re.finditer(r'\[([^\]]+)\]\((https?://[^\s)]+)\)',content):
        if match.start()>position:paragraph.add_run(clean(content[position:match.start()]))
        rel=paragraph.part.relate_to(match.group(2),RELATIONSHIP_TYPE.HYPERLINK,is_external=True)
        link=DElem('w:hyperlink');link.set(qn('r:id'),rel);run=DElem('w:r');props=DElem('w:rPr')
        color=DElem('w:color');color.set(qn('w:val'),PURPLE);props.append(color)
        underline=DElem('w:u');underline.set(qn('w:val'),'single');props.append(underline);run.append(props)
        value=DElem('w:t');value.text=clean(match.group(1));run.append(value);link.append(run);paragraph._p.append(link);position=match.end()
    if position<len(content):paragraph.add_run(clean(content[position:]))
    return paragraph


def md_to_docx(markdown,path,base):
    doc=Document();sec=doc.sections[0];sec.page_height=Cm(29.7);sec.page_width=Cm(21)
    sec.top_margin=sec.bottom_margin=Cm(1.8);sec.left_margin=sec.right_margin=Cm(1.9)
    for style in ['Normal','Body Text','Heading 1','Heading 2','Heading 3','Title','Caption','List Bullet']:
        st=doc.styles[style];st.font.name=FONT;st._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),FONT)
    normal=doc.styles['Normal'];normal.font.size=DPt(10.5);normal.paragraph_format.line_spacing=1.2;normal.paragraph_format.space_after=DPt(6)
    for name,size in [('Title',24),('Heading 1',18),('Heading 2',15),('Heading 3',12)]:
        st=doc.styles[name];st.font.size=DPt(size);st.font.color.rgb=DColor.from_string(PURPLE)
    header=sec.header.paragraphs[0];header.text='数据仓库与数据挖掘 · 第 9 组';header.runs[0].font.size=DPt(9)
    footer=sec.footer.paragraphs[0];footer.alignment=2
    for val in ['PAGE','NUMPAGES']:
        if val=='NUMPAGES':footer.add_run(' / ')
        field=DElem('w:fldSimple');field.set(qn('w:instr'),val);footer._p.append(field)
    lines=markdown.splitlines();i=0;code=False
    while i<len(lines):
        line=lines[i]
        if line.startswith('```'):code=not code;i+=1;continue
        if code:
            p=doc.add_paragraph();p.paragraph_format.space_after=DPt(0);r=p.add_run(line);r.font.name='Consolas';r.font.size=DPt(8);i+=1;continue
        if line.startswith('|'):
            block=[]
            while i<len(lines) and lines[i].startswith('|'):
                cells=[clean(x.strip()) for x in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r'[-: ]+',x) for x in cells):block.append(cells)
                i+=1
            table_=doc.add_table(rows=1,cols=len(block[0]));table_.style='Light Shading Accent 1'
            for j,c in enumerate(block[0]):rich_paragraph(table_.rows[0].cells[j].paragraphs[0],c)
            for row in block[1:]:
                cells=table_.add_row().cells
                for j,c in enumerate(row[:len(cells)]):rich_paragraph(cells[j].paragraphs[0],c)
            if block[0][0]=='序列' and len(block[0])==6:
                table_.autofit=False
                for j,width in enumerate([7.0,2.0,1.5,2.1,1.9,1.9]):
                    table_.columns[j].width=Cm(width)
                    for row in table_.rows:row.cells[j].width=Cm(width)
            for row_i,row in enumerate(table_.rows):
                trpr=row._tr.get_or_add_trPr();trpr.append(DElem('w:cantSplit'))
                for cell in row.cells:
                    shade=DElem('w:shd');shade.set(qn('w:fill'),PURPLE if row_i==0 else ('F2EDF4' if row_i%2 else 'FFFFFF'));cell._tc.get_or_add_tcPr().append(shade)
                    for p in cell.paragraphs:
                        p.paragraph_format.space_after=DPt(3)
                        if len(block)<=9:p.paragraph_format.keep_with_next=(row_i<len(block)-1)
                        for r in p.runs:
                            r.font.size=DPt(9);r.font.color.rgb=DColor.from_string('FFFFFF' if row_i==0 else INK)
                            if row_i==0:r.font.bold=True
            table_.rows[0]._tr.get_or_add_trPr().append(DElem('w:tblHeader'));continue
        match=re.fullmatch(r'!\[(.*?)\]\((.*?)\)',line)
        if match:
            doc.add_picture(str(base/match.group(2)),width=Cm(16.8));doc.add_paragraph(match.group(1),'Caption')
        elif line.startswith('# '):doc.add_heading(clean(line[2:]),0)
        elif line.startswith('## '):doc.add_heading(clean(line[3:]),1)
        elif line.startswith('### '):doc.add_heading(clean(line[4:]),2)
        elif line.strip():
            if line.startswith('- '):rich_paragraph(doc.add_paragraph(style='List Bullet'),line[2:])
            else:rich_paragraph(doc.add_paragraph(),line)
        i+=1
    doc.save(path)


def write_documents(out,summary,nyc,perf):
    narrative=(ROOT/'docs/final-report-narrative.md').read_text();manifest=load(ROOT/'configs/expanded-manifest.json');metrics=load(BASE/'expanded/metrics.json')
    rows=[r for r in manifest['candidates'] if r['admitted']]
    all_= [r for r in summary if r['subset']=='all'];new=[r for r in summary if r['subset']=='newly_evaluated']
    method_table=mdtable(['方法','TP','FP','FN','精确率','召回率','事件 F1','窗口外点 FPR'],[[NAMES[r['method']],r['tp'],r['fp'],r['fn'],f"{r['micro_precision']:.3f}",f"{r['micro_recall']:.3f}",f"{r['micro_f1']:.3f}",pct(r['outside_point_fpr'])] for r in all_])
    report=f'# 周期时间序列异常检测：实现与实验报告\n\n第 9 组 · 李思懿 / 翁汇坤\n\n课程：数据仓库与数据挖掘\n\n{narrative}\n\n## 实验结果明细\n\n### 全部真实序列\n\n21 条序列、22 个测试异常窗口。所有方法使用相同时间划分和事件规则。\n\n{method_table}\n\n![全部序列的事件 F1 与窗口召回](figures/expanded-baselines.png)\n\n### 此前未评估的 14 条序列\n\n该子集有 13 个测试窗口。其结果单独报告，不替代全部数据的结果。\n\n'+mdtable(['方法','TP','FP','FN','事件 F1','窗口外点 FPR'],[[NAMES[r['method']],r['tp'],r['fp'],r['fn'],f"{r['micro_f1']:.3f}",pct(r['outside_point_fpr'])] for r in new])
    report+='\n\n### 原纽约案例\n\n本表使用原训练/验证/测试划分（62/45/108 天）、P = 336、K = 6、W = 336。它与扩展协议中的纽约子实验参数及测试边界不同，不能把两表的窗口数或结果混用。\n\n'+mdtable(['方法','TP','窗外 FP','重复 FP','总 FP','F1','窗外点 FPR'],[[NAMES[m],r['tp'],r['outside_fp'],r['duplicate_fp'],r['fp'],f"{r['f1']:.3f}",pct(r['outside_point_fpr'])] for m,r in nyc.items()])
    report+='\n\n![原纽约案例误报分解](figures/nyc-errors.png)\n\n![纽约测试段、分数与官方标签窗口](figures/nyc-trace.png)\n\n### 受控实验\n\n![不同持续时间与幅度下的检出次数](figures/synthetic-recall.png)\n\n130 个案例来自 10 个固定随机种子，三个方法共执行 390 次 Java 回放。噪声标准差为 2，异常从第 1,000 点开始；采样间隔 30 分钟。该试验用于定位机制边界，未计入真实序列数量。\n\n### 数据库内外比较\n\n两条路径均使用限幅 EWMA；A 为 SQL UDF 查询并完整消费结果，B 为查询原始数据后运行同一 Java 核心。已验证输出校验和完全相同。每个规模预热 2 轮，再交替测量 5 轮；以下为本机暖缓存中位耗时。\n\n'+mdtable(['输入点数','有效输出点数','A: SQL UDF / ms','B: 取数+Java / ms','A/B'],[[f"{r['raw_returned_rows']:,}",f"{r['output_rows']:,}",f"{r['A_udf']['median_ms']:.1f}",f"{r['B_raw_java']['median_ms']:.1f}",f"{r['A_udf']['median_ms']/r['B_raw_java']['median_ms']:.2f}"] for r in perf['runs']])
    report+='\n\n三个规模下，UDF 比库外路径慢约 6%–24%，未观察到加速。导入、原始数据核对和文件输出不在计时区间内。B 的耗时包含取数与 Java 算法，不能用 B 的纯算法时间代替端到端对比。未测量实际网络字节数；本实验不支持网络流量收益或跨机器性能结论。真实数据库一致性检查另对纽约 7,968 个评分点的 score、flag、event、active 四输出逐行核验，两次查询一致，最大绝对误差为 0。\n\n## 数据清单与适用范围\n\n下表给出全部纳入序列与限幅 EWMA 结果；完整七方法指标见 `results/post-feedback/expanded/metrics.csv`。窗口数为测试段内起始的标签窗口数，无窗口序列仍保留在误报统计中。\n\n'
    inventory=[]
    for r in rows:
        m=next(x for x in metrics if x['id']==r['id'] and x['method']=='periodic_robust_ewma');inventory.append([r['source'].split('/')[-1].removesuffix('.csv'),r['rows'],r['period'],m['tp']+m['fn'],m['tp'],m['fp']])
    report+=mdtable(['序列','点数','周期 P','测试窗口','TP','FP'],inventory)
    report+='\n\n![逐序列事件 F1](figures/series-heatmap.png)\n\n47 条真实候选中排除 26 条。25 条存在不完整规则网格，7 条训练历史不足，两个原因可重叠。排除清单和原因完整记录在 `configs/expanded-manifest.json`。采样步长必须整除一天，训练段至少覆盖 4 个日周期加 1 点；选周周期还须满足相应历史长度。\n\n## 参考资料\n\n'
    report+='\n'.join(['- NAB 数据与许可证：https://github.com/numenta/NAB/tree/ea702d75cc2258d9d7dd35ca8e5e2539d71f3140','- Isolation Forest：https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html','- LOF novelty：https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.LocalOutlierFactor.html','- 季节朴素预测：https://otexts.com/fpp3/simple-methods.html','- EWMA：https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc314.htm','- 课程要求：Chap 0 About；project.pdf；本次导师反馈。'])+'\n'
    (out/f'{TITLE}-说明文档.md').write_text(report)
    md_to_docx(report,out/f'{TITLE}-说明文档.docx',out)
    test_doc=(ROOT/'docs/测试用例与实验复现.md').read_text()
    (out/f'{TITLE}-测试用例与实验描述.md').write_text(test_doc)
    md_to_docx(test_doc,out/f'{TITLE}-测试用例与实验描述.docx',out)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--template',type=Path,required=True);ap.add_argument('--output',type=Path,default=BASE/'deliverables');args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True);shutil.copytree(BASE/'figures',args.output/'figures',dirs_exist_ok=True)
    summary=load(BASE/'expanded/summary.json');nyc=load(BASE/'robust/nyc/metrics.json');perf=load(BASE/'database/performance/summary.json')
    build_deck(args.template,args.output,summary,nyc,perf);write_documents(args.output,summary,nyc,perf)
    print(args.output)

if __name__=='__main__':main()

#!/usr/bin/env python3
"""Revise the actual original 12-slide report, retaining its editable diagrams."""
import argparse, copy, csv, hashlib, json
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_AUTO_SIZE
from pptx.oxml.xmlchemy import OxmlElement
from lxml import etree
ROOT=Path(__file__).resolve().parents[1]
TITLE='9-李思懿-周期时间序列异常检测'
NS={'a':'http://schemas.openxmlformats.org/drawingml/2006/main','m':'http://schemas.openxmlformats.org/officeDocument/2006/math'}
PURPLE='660874';TEAL='267C8E';ORANGE='C45E30';MUTED='647080'

def walk(shapes):
    for s in shapes:
        yield s
        if s.shape_type==6:yield from walk(s.shapes)
def find(slide,id):return next(s for s in walk(slide.shapes) if s.shape_id==id)
def put(slide,id,value,width=None,size=None):
    s=find(slide,id);tf=s.text_frame
    p0=tf.paragraphs[0];r0=next((r for p in tf.paragraphs for r in p.runs if r.font is not None),None)
    rp=copy.deepcopy(r0._r.rPr) if r0 is not None and r0._r.rPr is not None else None
    pp=copy.deepcopy(p0._p.pPr) if p0._p.pPr is not None else None
    tf.clear()
    for i,line in enumerate(str(value).split('\n')):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph()
        if pp is not None:
            if p._p.pPr is not None:p._p.remove(p._p.pPr)
            p._p.insert(0,copy.deepcopy(pp))
        r=p.add_run();r.text=line
        if rp is not None:r._r.insert(0,copy.deepcopy(rp))
        if size is not None:r.font.size=Pt(size)
    tf.word_wrap=False;tf.auto_size=MSO_AUTO_SIZE.NONE
    tf.margin_top=tf.margin_bottom=tf.margin_left=tf.margin_right=0
    if width is not None:s.width=Inches(width)
    return s

def delete(slide,id):
    s=find(slide,id);s._element.getparent().remove(s._element)
def clone(slide,id,x=None,y=None,w=None,h=None,value=None):
    old=find(slide,id);el=copy.deepcopy(old._element)
    new_id=max(s.shape_id for s in walk(slide.shapes))+1
    nv=el.find('.//{http://schemas.openxmlformats.org/presentationml/2006/main}cNvPr');nv.set('id',str(new_id));nv.set('name',f'Updated original element {new_id}')
    slide.shapes._spTree.insert_element_before(el,'p:extLst');s=find(slide,new_id)
    for attr,val in [('left',x),('top',y),('width',w),('height',h)]:
        if val is not None:setattr(s,attr,Inches(val))
    if value is not None:put(slide,new_id,value)
    return s

def replace_numbers(slide,replacements):
    for el in slide._element.iter():
        if el.tag==f'{{{NS["a"]}}}t' and el.text:
            value=el.text
            for a,b in replacements:value=value.replace(a,b)
            etree._Element.text.__set__(el,value)
    # Office Math stores numeric literals one digit per run; replace across runs
    # without discarding the original editable formula or its run properties.
    for equation in slide._element.iter(f'{{{NS["m"]}}}oMath'):
        nodes=list(equation.iter(f'{{{NS["m"]}}}t'))
        values=[etree._Element.text.__get__(n) or '' for n in nodes]
        joined=''.join(values);changed=joined
        for a,b in replacements:changed=changed.replace(a,b)
        assert len(joined)==len(changed)
        pos=0
        for n,v in zip(nodes,values):etree._Element.text.__set__(n,changed[pos:pos+len(v)]);pos+=len(v)

def font_portability(prs):
    # The original uses a missing Alibaba EA face. Keep sizes, weights, colours,
    # geometry and equations; substitute only CJK font/fallback metadata.
    for part in prs.part.package.iter_parts():
        if hasattr(part,'_element'):
            for el in part._element.iter():
                if el.tag==f'{{{NS["a"]}}}ea' and el.get('typeface')!='Cambria Math':el.set('typeface','PingFang SC');el.attrib.pop('panose',None);el.attrib.pop('charset',None)
        if '/theme/' in str(part.partname):
            t=etree.fromstring(part.blob)
            for el in t.iter():
                if el.tag==f'{{{NS["a"]}}}ea' or (el.tag==f'{{{NS["a"]}}}font' and el.get('script') in ['Hans','Hant']):el.set('typeface','PingFang SC')
            part._blob=etree.tostring(t,xml_declaration=True,encoding='UTF-8',standalone=True)

def curve(out):
    from PIL import Image,ImageDraw
    p=ROOT/'data/processed/expanded/realKnownCause__nyc_taxi.csv'
    with p.open() as f:v=[float(x['value']) for x in csv.DictReader(f)]
    # Same full NYC series; draw the waveform and daily envelope on a transparent canvas.
    width,height=4632,574;im=Image.new('RGBA',(width,height),(255,255,255,0));draw=ImageDraw.Draw(im)
    y=lambda value:max(0,min(height-1,(1-value/40000)*(height-1)))
    days=[v[i:i+48] for i in range(0,len(v),48)]
    upper=[((i+.5)/len(days)*width,y(max(day))) for i,day in enumerate(days)]
    lower=[((i+.5)/len(days)*width,y(min(day))) for i,day in enumerate(days)]
    draw.polygon(upper+lower[::-1],fill=(97,117,139,35))
    for start,end,color in [(0,.4,(102,8,116,175)),(.4,.6,(38,124,142,175)),(.6,1,(196,94,48,175))]:
        lo=int(len(v)*start);hi=min(len(v),int(len(v)*end)+1)
        draw.line([(i/(len(v)-1)*(width-1),y(v[i])) for i in range(lo,hi)],fill=color,width=1)
    dest=out/'nyc-original-style.png';im.resize((2316,287),Image.Resampling.LANCZOS).save(dest);return dest

def revise(template,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);prs=Presentation(template)
    assert len(prs.slides)==12,'Must start from the original 12 actual slides, not a blank master.'
    ss=list(prs.slides);before=[{s.shape_id for s in walk(p.shapes)} for p in ss]
    data=json.loads((ROOT/'results/submission/nab/summary.json').read_text())['all']
    perf=json.loads((ROOT/'results/iteration2/database/performance/summary.json').read_text())['runs']
    # Keep every original slide. Widen existing title boxes instead of rebuilding.
    for p in ss:find(p,3).width=Inches(12);find(p,3).text_frame.word_wrap=False
    s=ss[0]
    put(s,4,'第 9 组  ·  李思懿 / 翁汇坤',width=4.8)
    clone(s,4,x=.60,y=1.77,w=11.9,h=.28,value='周期感知与快慢互补融合  ·  NAB 21 条真实序列 / 3 类场景')
    put(s,10,'固定步长 Δt，按时间逐点输入',width=2.1)
    put(s,27,'共享检测核心 (PeriodicDetector)',width=3.45)
    put(s,30,'① 周期 / 稳健 AR 预测',width=4.5)
    put(s,31,'训练相关性 ≥ 0.5 用同期中位数；其余使用稳健 AR',width=4.7)
    put(s,33,'② 残差与时间证据',width=4.5)
    put(s,34,'过去残差估计尺度；逐点或 EWMA 输出单路分数',width=4.7)
    put(s,36,'③ 因果状态更新',width=4.5)
    put(s,37,'先评分后更新；周期历史有界写入，原始残差保留',width=4.7)
    put(s,41,'单路检测结果',width=2.1);put(s,42,'Python 再完成双路融合',width=2.1)
    put(s,45,'单路异常分数\n融合前按各路阈值对齐',width=2.1)
    put(s,48,'告警条件：score > τ\n四种输出由 SQL 参数选择',width=2.1)
    # The original periodic example is retained; only the right explanation changes.
    s=ss[1];put(s,42,'周期分流与稳健预测',width=3.65)
    for id,t in {44:'训练期判断周期强弱',45:'在日 / 周候选中选 P，计算训练相关性 ρ',46:'固定规则网格；模型只用过去值预测当前值',48:'强周期：同期中位数',49:'ρ ≥ 0.5：同相位最近 K = 3 个历史值',50:'过去 P 个真实残差估计中心与 MAD 尺度',51:'评分后有界更新同期历史，减轻极端值影响',53:'弱周期：稳健 AR × 局部水平',54:'ρ < 0.5：训练段 Huber 回归拟合系数',55:'滞后：1, 2, 3, 6, 12, P, 2P',56:'联合创新残差与局部水平偏离',57:'本轮分流：3 条强周期，18 条弱周期'}.items():put(s,id,t,width=3.65)
    # Retain the score formula and execution-order diagram in full.
    s=ss[2];put(s,37,'示例阈值（实验中由验证段选定）：',width=2.4,size=9)
    put(s,31,'MAD：过去残差到其中位数的距离中位数',width=4.7)
    find(s,51).width=Inches(3.1);find(s,54).width=Inches(3.3);find(s,57).width=Inches(3.3)
    # Update the existing bounded-update numerical example to actual c=2 and K=3.
    s=ss[3];replace_numbers(s,[('3.0 ×','2.0 ×'),('2.2239','1.4826'),('7.7761','8.5174'),('12.2239','11.4826')])
    put(s,53,'[10, 10, 11.4826]',width=2.8)
    put(s,42,'历史更新层：限幅参数',width=3.1)
    find(s,8).width=Inches(4.4)
    find(s,18).width=Inches(.9);find(s,18).left=Inches(4.42)
    find(s,20).width=Inches(1.1);find(s,20).left=Inches(7.15)
    find(s,30).width=Inches(1.6);find(s,30).left=Inches(7.0)
    equation=find(s,57)
    for el in list(equation._element.iter('{http://schemas.microsoft.com/office/drawing/2010/main}m')):el.getparent().remove(el)
    put(s,57,'x* = b + m + clip(r − m, −ca, ca) = 10 + clip(4, −1.4826, 1.4826) = 11.4826',width=11.7,size=18)
    equation.left=Inches(.85)
    # The original equation has an MC fallback picture. After the content edit,
    # promote the editable shape so LibreOffice cannot display the stale fallback.
    choice=equation._element.getparent()
    if choice.tag.endswith('}Choice'):
        alternate=choice.getparent();parent=alternate.getparent();at=parent.index(alternate)
        choice.remove(equation._element);parent.remove(alternate);parent.insert(at,equation._element)
    for p in equation.text_frame.paragraphs:
        for r in p.runs:r.font.name='Cambria Math';r.font.color.rgb=RGBColor.from_string(PURPLE)
    find(s,10).left=Inches(3.778);find(s,10).width=Inches(2.084)
    find(s,36).left=Inches(5.787);find(s,35).left=Inches(5.98);find(s,35).width=Inches(1.55)
    find(s,37).left=Inches(5.36);find(s,37).width=Inches(1.65)
    find(s,11).left=Inches(3.70);find(s,11).width=Inches(2.5)
    # Keep the SQL block and sequence diagram; show a valid component example.
    s=ss[4];put(s,7,'周期组件 SQL（参数示例）',width=3.8)
    put(s,11,"  'period'='336', 'k'='3',")
    put(s,15,"  'evidence_span'='12', 'evidence_clip'='4',",size=9)
    put(s,21,'score / flag / event / active：独立单列输出',width=3.85,size=9.4)
    put(s,22,'融合在 Python 完成；本页为单路 UDF',width=3.85,size=9.1)
    clone(s,61,x=5.45,y=6.44,w=7.0,h=.23,value='AR 3,168 点 / 周期 8,976 点：四种输出与库外完全一致')
    # Keep NYC as the visible example; broaden the dataset text and fix split geometry.
    s=ss[5];put(s,7,'NAB：21 条真实序列 · 3 类场景 · 22 个测试窗口',width=11.7)
    x=1.021;w=11.583;b1=x+w*.4;b2=x+w*.6
    for id,l,ww in [(9,x,w*.4),(10,b1,w*.2),(11,b2,w*.4)]:find(s,id).left=Inches(l);find(s,id).width=Inches(ww)
    put(s,12,'训练 40% · 估计周期与模型',width=4.3);find(s,12).left=Inches(x+.10)
    put(s,13,'校准 20% · 选阈值',width=2.2);find(s,13).left=Inches(b1+.08)
    put(s,14,'测试 40% · 仅用于评价',width=4.3);find(s,14).left=Inches(b2+.08)
    for id,l in [(20,b1),(21,b2)]:find(s,id).left=Inches(l)
    put(s,29,'2014-09-25',width=1.0);find(s,29).left=Inches(b1-.35)
    put(s,30,'2014-11-07',width=1.0);find(s,30).left=Inches(b2-.35)
    put(s,32,'图为 NYC 全序列示例；全部纳入 AWS 9 条、已知原因 2 条、Twitter 10 条。',width=11.6)
    delete(s,22);pic=find(s,46);part,_=s.part.get_or_add_image_part(str(curve(out)));blip=pic._element.find('.//{http://schemas.openxmlformats.org/drawingml/2006/main}blip');blip.set('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed',_)
    for id,t in {36:'公开来源与纳入规则',37:'来源：NAB · Lavin & Ahmad, ICMLA 2015 [11] · MIT',38:'47 条真实候选 → 21 条：有限值、完整规则网格、足够训练历史',39:'共 223,878 点；测试 89,559 点；窗口沿用官方标签',42:'统一选参协议',43:'训练选日 / 周周期 P；K = 3，W = P，c = 2',44:'校准：短脉冲、长偏移、平线，各 6 处注入',45:'按报警预算与注入 F2 选择阈值及融合模式'}.items():put(s,id,t,width=6.8 if id<40 else 4.5,size=10.3 if id not in (36,42) else 12)
    find(s,37).text_frame.paragraphs[0].runs[0].hyperlink.address='https://doi.org/10.1109/ICMLA.2015.141'
    # Reuse the original event/label illustration and equations, with the latest denominators.
    s=ss[6]
    put(s,53,'TP：命中的官方窗口数（最多 22） │ FP：窗口内重复告警 + 窗外告警',width=9.15)
    put(s,54,'FN：未命中的窗口；全历史先合并，再按事件起点归属。不做 point adjustment。',width=9.15,size=9.6)
    put(s,62,'精确率：15 / (15 + 21) = 41.7%',width=3.4)
    put(s,70,'召回率：15 / 22 = 68.2%',width=3.4)
    put(s,78,'事件 F1 = 0.517',width=3.4)
    # Keep the original five-row stacked-bar comparison and legend.
    s=ss[7];put(s,3,'独立基线对比：窗口覆盖与告警分布',width=12)
    put(s,7,'方法 / 论文来源',width=1.8);put(s,8,'命中窗口 (TP/22)',width=1.7)
    order=['adaptive_complementary_fusion','isolation_forest','local_outlier_factor','spectral_residual','left_matrix_profile']
    labels=['本文：快慢融合','Isolation Forest','LOF','Spectral Residual','Left Matrix Profile']
    refs=['验证选择 18 慢 / 3 快','Liu et al. · ICDM 2008 [1]','Breunig et al. · SIGMOD 2000 [2]','Ren et al. · KDD 2019 [3]','Yeh 2016 / Lu 2022 [4,5]']
    labelids=[21,35,47,59,67];coverids=[23,36,48,60,68];totalids=[31,43,55,63,70];f1ids=[32,44,56,64,71]
    bars=[[(25,26),(27,28),(29,30)],[(37,38),(39,40),(41,42)],[(49,50),(51,52),(53,54)],[(61,62)],[]]
    ys=[2.67,3.46,4.10,4.74,5.38];delete(s,69)
    for i,m in enumerate(order):
        d=data[m];put(s,labelids[i],labels[i],width=1.95,size=11.1);find(s,labelids[i]).top=Inches(ys[i]-.05)
        if i==0:put(s,22,refs[i],width=2.15,size=8.0);find(s,22).top=Inches(ys[i]+.29)
        else:clone(s,22,x=.72,y=ys[i]+.28,w=2.2,h=.18,value=refs[i])
        put(s,coverids[i],f'{d["tp"]} / 22',width=1.25,size=12);find(s,coverids[i]).left=Inches(2.55)
        put(s,totalids[i],str(int(d['tp'])+int(d['fp'])),width=.6);find(s,totalids[i]).left=Inches(10.18)
        put(s,f1ids[i],f'{float(d["micro_f1"]):.3f}',width=.65)
        left=4.375
        for j,key in enumerate(['tp','duplicate_fp','outside_fp']):
            value=int(d[key]);ww=value/120*5.625
            if j<len(bars[i]):bar=find(s,bars[i][j][0]);lab=find(s,bars[i][j][1])
            else:bar=clone(s,[25,27,29][j]);lab=clone(s,[26,28,30][j])
            bar.left=Inches(left);bar.top=Inches(ys[i]);bar.width=Inches(ww)
            lab.left=Inches(left);lab.top=Inches(ys[i]+.06);lab.width=Inches(ww)
            put(s,lab.shape_id,str(value),size=9);lab.text_frame.paragraphs[0].alignment=PP_ALIGN.CENTER
            for p in lab.text_frame.paragraphs:
                for r in p.runs:r.font.color.rgb=RGBColor(255,255,255)
            left+=ww
    put(s,77,'40');put(s,79,'80');put(s,81,'120 事件',width=.7)
    clone(s,22,x=.72,y=6.40,w=11.9,h=.22,value='相同切分与评价口径；SR 非 SR-CNN，Left-MP 为有界左侧精确搜索，完整来源与适配说明见附录。')
    # Keep two case cards plus the original three-row internal comparison.
    s=ss[8];put(s,3,'互补融合的收益与漏检边界',width=12)
    for id in [10,21]:delete(s,id)
    put(s,7,'窗口 NAB-02：EC2 CPU',width=5.5,size=12)
    put(s,8,'最高分 / 阈值：本文 1.027；IF 0.926',width=5.4)
    put(s,9,'本文命中 · IF 未命中',width=5.4,size=16.5)
    put(s,18,'窗口 NAB-17：Twitter FB',width=5.5,size=12)
    put(s,19,'最高分 / 阈值：本文 0.987；IF 1.014',width=5.4)
    put(s,20,'本文未命中 · IF 命中',width=5.4,size=16.5)
    put(s,30,'同一批序列的内部消融',width=4.0)
    put(s,31,'22 窗口；标签边界与 1 小时合并规则不变',width=6.9)
    for ids,m,title in [([41,42,43,44,46,47,48],'adaptive_evidence','自适应单路'),([51,52,53,54,56,57,58],'complementary_evidence','固定慢速融合'),([62,63,64,65,67,68,69],'adaptive_complementary_fusion','验证选快慢融合')]:
        d=data[m];put(s,ids[0],title,width=2.0)
        for id,key in zip(ids[1:4],['tp','duplicate_fp','outside_fp']):put(s,id,d[key],width=.45);find(s,id).left-=Inches(.07)
        bar=find(s,ids[4]);bar.width=Inches(float(d['fp'])/50*2.5)
        put(s,ids[5],f'{d["fp"]} 次',width=.55);find(s,ids[5]).left=Inches(8.54+float(d['fp'])/50*2.5+.08)
        put(s,ids[6],f'{float(d["micro_f1"]):.3f}',width=.65)
    put(s,70,'总命中数相同不代表命中同一批窗口；窗外点 FPR：自适应 0.0714%，最终融合 0.1206%。',width=11.8,size=9.6)
    # Reuse the four-step strip, six-column worked example, and two stacked bars.
    s=ss[9];put(s,3,'改进二：快慢互补融合，误报 43 → 21 次',width=12)
    updates={7:'① 自适应分支 A',8:'强周期：同期中位数\n弱周期：稳健 AR × 水平',11:'② 周期分支 P / S',12:'P：逐点残差\nS：限幅 + EWMA(12)',15:'③ 按阈值对齐',16:'A/τA，P/τP，S/τS\n对齐到各自报警阈值',19:'④ 验证段选融合',20:'慢：(A/τA + S/τS)/2\n快：(A/τA + P/τP)/2',26:'均值融合示意：抑制单路弱越限',27:'示意：B/τB 固定为 0.4；融合阈值为 1，严格大于才报警。',35:'分支 A / τA',36:'各时刻独立示意',43:'融合分数 S',44:'S = (A/τA + 0.4) / 2',53:'第 5 点：(1.8 + 0.4) / 2 = 1.1 > 1',56:'NAB 全部 21 条序列',57:'误报构成：窗口外 / 窗口内重复',58:'自适应',64:'最终融合',70:'总命中均为 15/22；F1：0.375 → 0.517',71:'验证选择：18 条慢模式、3 条快模式',72:'融合阈值 ≥ 1，仅由验证背景预算提高',73:'均值融合不是“两路同时报警”的硬 AND'}
    for id,t in updates.items():put(s,id,t,size=10.8 if id in [8,12,16,20,27] else None)
    for ids,values in [([37,38,39,40,41,42],['1.4','1.6','1.3','1.5','1.8','2.2']),([45,46,47,48,49,51],['0.90','1.00','0.85','0.95','1.10','1.30'])]:
        for id,t in zip(ids,values):put(s,id,t)
    highlight=clone(s,50,x=5.755);s.shapes._spTree.remove(highlight._element)
    target=find(s,49)._element;target.getparent().insert(target.getparent().index(target),highlight._element)
    clone(s,52,x=5.77,value='报警')
    for nums,values in [([59,60,61,62,63],[28,15,43]),([65,66,67,68,69],[9,12,21])]:
        left=9.27
        for j in range(2):
            ww=values[j]/43*2.75
            bar=find(s,nums[j*2]);lab=find(s,nums[j*2+1]);bar.left=lab.left=Inches(left);bar.width=lab.width=Inches(ww);put(s,lab.shape_id,values[j]);left+=ww
        put(s,nums[4],values[2])
    # Original A/B execution-path diagram and ratio bars remain intact.
    s=ss[10];put(s,7,'同一冻结 AR 组件：库内 / 库外',width=5.1)
    put(s,8,'本机暖缓存；2 轮预热，5 轮交替测量取中位数',width=6.4)
    for i,(ids,d) in enumerate(zip([[41,42,43,45,46,47],[50,51,52,54,55,56],[60,61,62,64,65,66]],perf)):
        n=d['raw_returned_rows'];a=d['A_udf']['median_ms'];b=d['B_raw_java']['median_ms'];ratio=a/b
        put(s,ids[0],f'{n:,} 点',width=1.8)
        put(s,ids[1],f'{a:,.1f} ms',width=1.8,size=12);find(s,ids[1]).left=Inches(2.62)
        put(s,ids[2],f'{b:,.1f} ms',width=1.8,size=12);find(s,ids[2]).left=Inches(4.70)
        find(s,ids[3]).width=Inches(2.083*ratio)
        put(s,ids[4],f'{ratio*100:.1f}%',width=.6);find(s,ids[4]).left=Inches(7.29+2.083*ratio+.08)
        put(s,ids[5],f'+{(ratio-1)*100:.1f}%',width=.8);find(s,ids[5]).left=Inches(11.55)
    clone(s,8,x=.72,y=6.96,w=11.8,h=.20,value='仅测单路 AR 组件，不含 Python 融合；三规模校验和一致，未观察到 UDF 加速。')
    # Preserve the four-panel conclusion layout.
    s=ss[11]
    for id,t in {6:'1. 方法与实现',7:'周期 / 稳健 AR 分流，快慢证据互补\n单路 Java / UDF；Python 完成融合',10:'2. 数据与独立对照',11:'NAB 21 条序列、3 类场景、22 窗口\n4 种文献 baseline，完整来源与逐窗表',14:'3. 主要效果',15:'命中 15 / 22；误报 21；事件 F1 0.517\n最强外部基线 IF：13 / 22，FP 28，F1 0.413',18:'4. 边界与后续',19:'仍漏检 7 窗，部分子组与点指标未领先\n冻结方法后检验新时间段；评估库内融合'}.items():put(s,id,t)
    foot=clone(s,6,x=.73,y=6.97,w=11.8,h=.18,value='本轮为已暴露 NAB 数据上的开发比较；完整引用、逐窗证据与复现材料随报告提交。')
    for p in foot.text_frame.paragraphs:
        for r in p.runs:r.font.size=Pt(9);r.font.bold=False;r.font.color.rgb=RGBColor.from_string(MUTED)
    # Put the updated original improvement slide before SQL, preserving all 12 slides.
    order=[0,1,2,3,9,4,5,6,7,8,10,11]
    nodes=list(prs.slides._sldIdLst)
    for el in nodes:prs.slides._sldIdLst.remove(el)
    for i in order:prs.slides._sldIdLst.append(nodes[i])
    for rel in ss[9].part.rels.values():
        if rel.reltype.endswith('/slideLayout'):rel._target=ss[2].slide_layout.part
    notes={0:'从原汇报的输入、检测核心、输出流程讲起。此次仅在既有流程中增加周期/AR分流及库外快慢融合。NAB包含21条真实序列，不能称为21个独立数据集。',1:'左图保留原周期反例：同样是14，高峰时正常、低谷时偏高。右侧补充训练分流：相关性达到0.5用同期中位数，否则用Huber稳健AR与水平偏离联合。',2:'保留原数值例子说明原始逐点分数。4只是演示阈值，实际各分支阈值由验证段选择。所有状态先评分后更新。',3:'原三种历史写入图保持。数值例子更新为实验c=2，允许区间8.5174到11.4826；K=3。同相位历史被限幅，但真实残差仍写入统计。',9:'自适应A与周期快速P或慢速S按各自阈值归一化，再等权平均。示例只说明均值如何抑制弱的单路越限；强单路仍可能触发。验证三套注入决定18慢3快，融合阈值只按背景预算提高。总命中15不意味着命中相同窗口。',4:'保留原SQL及调用时序图。这里展示单路周期UDF示例参数；AR组件同样可接入。score、flag、event、active是四次独立单列调用。AR3168点、周期8976点均与库外一致，完整融合仍由Python完成。',5:'范围从原纽约案例扩展为NAB21条序列：AWS9、KnownCause2、Twitter10。全部47真实候选按结构筛选，排除26条，不看测试成绩。全体223878点，测试89559点、22个官方窗口。图仍为原纽约序列示例，更新为40/20/40切分；本轮纽约4窗，旧案例5窗不混计。',6:'保留原事件合并、TP/FP/FN图与公式。全历史按一小时合并，按起点归属；首警TP、重复和窗外FP、未命中FN。最终P为15/36、R为15/22、F1=.517。非NAB官方早检加权分数，不做point adjustment。',7:'原五行堆叠条形图更新为最终方法及四个外部baseline。最终15TP、12重复FP、9窗外FP，总36事件；IF13TP、28FP。IF/LOF来自sklearn，SR是因果无监督版本，Left-MP是有界左历史精确搜索，不是完整DAMP。引用编号对应来源附录。',8:'原消融页面保留上下结构。上方展示一得一失：NAB02本文1.027而IF.926；NAB17本文.987而IF1.014。下方相同总TP15时FP43到23到21。自适应点FPR.0714%仍低于最终.1206%，KnownCause子组Left-MP也更好。',10:'保留原库内/客户端两条执行路径，换成冻结AR模型新测量。B包含原值查询和同Java核心计算，不是纯内存。2预热5轮；百万点约13秒，三规模结果校验和相同，没有观察到UDF加速。平铺负载不计入精度样本，不含Python融合耗时。',11:'总结三项改进：多序列数据、文献baseline和融合降误报。最终15/22、21FP、F1.517，胜四个外部baseline；仍有7个漏窗，部分子组和点指标未领先。NAB已用于迭代，后续应冻结方法检验新时间段。'}
    manuscript=[]
    for n,i in enumerate(order,1):
        p=ss[i]
        for sh in walk(p.shapes):
            if sh.name.startswith('Slide Number'):put(p,sh.shape_id,str(n))
        p.notes_slide.notes_text_frame.text=notes[i]
        manuscript.append(f'## 第 {n} 页：{find(p,3).text}\n\n{notes[i]}\n')
    font_portability(prs)
    dest=out/(TITLE+'.pptx');prs.save(dest)
    (out/(TITLE+'-讲稿.md')).write_text('# 展示讲稿（原稿增量修改版）\n\n'+'\n'.join(manuscript))
    retention=[{'original_slide':i+1,'final_slide':order.index(i)+1,'original_shapes':len(before[i]),'retained_ids':len(before[i]&{s.shape_id for s in walk(ss[i].shapes)})} for i in range(12)]
    (out/'original-edit-provenance.json').write_text(json.dumps({'input':str(template),'input_sha256':hashlib.sha256(Path(template).read_bytes()).hexdigest(),'slides':12,'order_from_original':[i+1 for i in order],'retention':retention,'output_sha256':hashlib.sha256(dest.read_bytes()).hexdigest()},ensure_ascii=False,indent=2)+'\n')
    return dest

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--template',type=Path,default=ROOT/'assets/original-project-report.pptx');p.add_argument('--output',type=Path,default=ROOT/'results/submission/original-revision/revised');a=p.parse_args();print(revise(a.template,a.output))
if __name__=='__main__':main()

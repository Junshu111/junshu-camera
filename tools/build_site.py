"""Build a portable Chinese guide. No network, dependencies or device access."""
from pathlib import Path
import json, html, re, hashlib

BASE=Path(__file__).resolve().parents[1]
SITE=BASE/('site' if (BASE/'site').is_dir() else 'docs')
PROJECT=BASE
STAGES=['看懂，再准备','让它第一次工作','让它按你的想法工作','把它做成一台相机']
STAGE_DESC=['目标、零件、接口与购买清单。','连接、启动、传文件与第一张照片。','软件、控件、供电与外观布局。','打印、安装、检验与长期维护。']
SETUP=json.loads((BASE/'content/setup.json').read_text())
REPO=SETUP['repo_url']
TABLES=json.loads((BASE/'content/tables.json').read_text())
e=lambda v:html.escape(str(v),quote=True)

def prose(v):
    if isinstance(v,list):v='\n'.join(map(str,v))
    return ''.join('<p>'+re.sub(r'`([^`]+)`',r'<code>\1</code>',e(p))+'</p>' for p in str(v).split('\n') if p.strip())

def header(prefix='',home=False):
    return f'''<a class="skip-link" href="#main">跳到正文</a><header class="site-header {'home-header' if home else ''}"><a class="brand" href="{prefix}index.html">自制相机</a><nav aria-label="主导航"><a href="{prefix}route.html">制作路线</a><a href="{prefix}about.html">版本与验证</a><a href="{prefix}setup.html">获取项目</a><a class="nav-action" href="{prefix}route.html">开始阅读 ↗</a></nav></header>'''

def footer(prefix=''):
    return f'''<footer><div class="footer-inner layout"><strong>自制相机</strong><span>中文制作教程 · 第 1 版 · 2026.10.31</span><a href="{prefix}setup.html">获取项目与 AI 提示词 ↗</a></div></footer>'''

def stylesheet(name,prefix=''):
    version=hashlib.sha256((SITE/'assets'/name).read_bytes()).hexdigest()[:12]
    return f'<link rel="stylesheet" href="{prefix}assets/{name}?v={version}">'

def page(title,body,prefix='',home=False):
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="自制相机中文教程：从认识零件到安装、打印与最终检验，16章逐步学习。"><title>{e(title)} / 自制相机</title>{stylesheet('home.css') if home else ''}{stylesheet('guide.css',prefix)}{stylesheet('ai-setup.css',prefix)}<script src="{prefix}assets/guide.js" defer></script><script src="{prefix}assets/ai-setup.js" defer></script></head><body>{header(prefix,home)}{body}{footer(prefix)}</body></html>'''

def illustration(ch):
    """Editorial line diagrams show relationships, never connector orientation."""
    num=int(ch['id']); labels={
      1:['定目标','列工具','留记录'],2:['显示屏','主板','镜头模组'],3:['显示屏','主板','镜头模组'],4:['核对型号','核对接口','核对尺寸'],5:['显示连接','主板供电','镜头连接'],6:['选择系统','写入卡片','首次启动'],7:['同一网络','确认设备','复制文件'],8:['识别镜头','预览画面','保存照片'],9:['核对环境','安装版本','保留回退'],10:['辨认端子','分项验证','记录接线'],11:['一次改一项','检查反馈','保留基线'],12:['摆放实物','检查让位','闭合外壳'],13:['打印小样','核对配合','再打整件'],14:['固定内部','整理线材','合壳复查'],15:['单项检查','整机复查','记录未过项'],16:['备份照片','备份版本','再做改造']}[num]
    shapes='''<g stroke="#232a23" stroke-width="2" stroke-linejoin="round" fill="#f5f4ec"><path d="M267 107L390 107 448 68 325 68Z"/><path d="M390 107L448 68 448 244 390 285Z" fill="#d2d6ca"/><path d="M267 107H390V285H267Z"/><path d="M278 103L383 103 432 72 328 72Z" fill="#aebbb1"/><circle cx="327" cy="154" r="12" fill="#c3cabc"/><circle cx="327" cy="154" r="6" fill="#344438"/><path d="M278 270H390L448 231" fill="none"/><ellipse cx="416" cy="181" rx="9" ry="16"/><path d="M416 179L447 168 456 174 419 190Z"/><circle cx="417" cy="226" r="6"/><path d="M286 132v12m9-12v12m68-12v12m9-12v12"/></g>'''
    if num in [2,3,5,10,12,14]:
        shapes='''<g stroke="#232a23" stroke-width="2" stroke-linejoin="round"><path d="M274 96L390 96 447 61 331 61Z" fill="#c0cbbf"/><path d="M279 169L395 169 446 135 330 135Z" fill="#d9ddcf"/><path d="M319 147L355 147 368 140 332 140Z" fill="#aebaa3"/><path d="M274 252L390 252 447 217 331 217Z" fill="#eeeede"/><path d="M274 252v15H390L447 232v-15" fill="#c8cebf"/><path d="M300 100V232m104-149V225" stroke-dasharray="5 6" stroke="#899780"/><rect x="207" y="163" width="42" height="34" fill="#d6ddca"/><circle cx="228" cy="180" r="8" fill="#95a88e"/><path d="M249 181H278" fill="none"/><path d="M456 178l19-11v42l-19 11Z" fill="#e3e7dc"/></g>'''
    if num in [6,7,8,9,11,16]:
        shapes='''<g stroke="#232a23" stroke-width="2" stroke-linejoin="round" fill="#f7f6ee"><rect x="259" y="84" width="191" height="126" rx="6"/><rect x="269" y="94" width="171" height="100" fill="#d0dbc4"/><path d="M320 211v26h68v-26m-86 26h104"/><path d="M280 179l35-32 27 19 40-43 46 56Z" fill="#b3c4a3"/><circle cx="298" cy="115" r="9" fill="#d8f34d"/><path d="M293 267h128m-111 8h93"/><rect x="472" y="160" width="29" height="41" fill="#d3d8c9"/><path d="M478 166v10m5-10v10m5-10v10m5-10v10"/><path d="M449 184h19" fill="none"/></g>'''
    s=f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 350" role="img" aria-label="{e(ch['title'])}关系示意"><rect width="720" height="350" fill="#eeeee5"/><g stroke="#d2d6ca" stroke-width="1"><path d="M30 40H690M30 300H690M180 25V320M530 25V320"/></g>{shapes}<g font-family="PingFang SC,Microsoft YaHei,sans-serif" font-weight="700" fill="#232a23">'''
    for i,(label,x,y) in enumerate(zip(labels,[32,545,32],[92,157,245])):
        s+=f'<rect x="{x}" y="{y-22}" width="31" height="25" fill="#d8f34d"/><text x="{x+6}" y="{y-4}" font-size="15">0{i+1}</text><text x="{x}" y="{y+29}" font-size="18">{e(label)}</text>'
    connectors='M150 100H195L245 125M530 180H510M150 250H194L250 228'
    if num in [2,3,5]:connectors='M150 100H195L310 85M530 180H480L407 156M150 250H194L228 196'
    s+='''</g><g stroke="#232a23" stroke-width="1.4" fill="none"><path d="'''+connectors+'''"/></g><g fill="#d8f34d" stroke="#232a23" stroke-width="1"><circle cx="245" cy="125" r="4"/><circle cx="510" cy="180" r="4"/><circle cx="250" cy="228" r="4"/></g></svg>'''
    if num in [2,3,5]:
        s=s.replace('cx="245" cy="125"','cx="310" cy="85"').replace('cx="510" cy="180"','cx="407" cy="156"').replace('cx="250" cy="228"','cx="228" cy="196"')
    (SITE/'assets'/f'diagram-{ch["id"]}.svg').write_text(s)

def rail(chapters,current):
    out='<aside class="reading-rail" aria-label="章节目录"><h2>16 章制作路线</h2>'
    for stage in range(1,5):
        out+=f'<div class="rail-stage">0{stage} / {STAGES[stage-1]}</div>'
        for ch in chapters:
            if int(ch['stage'])==stage:out+=f'<a href="{ch["id"]}.html" class="{"active" if ch["id"]==current else ""}" {"aria-current=page" if ch["id"]==current else ""}><span>{ch["id"]}</span><span>{e(ch["title"])}</span></a>'
    return out+'</aside>'

def reference(ref):
    name=e(ref.get('title','项目资料'));url=ref.get('url')
    if url and url.startswith(('https://','http://')):return f'<a href="{e(url)}" target="_blank" rel="noopener">{name} ↗</a>'
    path=str(ref.get('path','')).replace(str(PROJECT)+'/','')
    return name+(f' · <span>{e(path)}</span>' if path else '')

def chapter_page(ch,chapters):
    n=int(ch['id']);stage=int(ch['stage']);illustration(ch)
    out=f'<main class="reading-layout layout" id="main">{rail(chapters,ch["id"])}<article class="article"><div class="chapter-meta"><span class="num">{ch["id"]} / 16</span><span>阶段 0{stage} · {STAGES[stage-1]}</span></div><h1>{e(ch["title"])}</h1><div class="lead">{prose(ch["intro"])}</div><div class="status status-note">{e(ch["status"])}</div><figure class="diagram"><img src="../assets/diagram-{ch["id"]}.svg" alt="{e(ch["title"])}关系示意图"><figcaption>关系示意，帮助理解步骤；不代表实际接线方向、零件比例或加工尺寸。具体操作以本章核对项为准。</figcaption></figure>'
    out+='<div class="prep-grid">'
    for name,key in [('本章完成目标','goals'),('开始前准备好','prerequisites')]:out+=f'<section><h2>{name}</h2><ul>'+''.join(f'<li>{e(x)}</li>' for x in ch[key])+'</ul></section>'
    if ch['id'] in TABLES:
        t=TABLES[ch['id']];out+='</div><section class="data-section"><h2>'+e(t['title'])+'</h2>'+prose(t['note'])+'<div class="table-scroll"><table><thead><tr>'+''.join('<th scope="col">'+e(h)+'</th>' for h in t['headers'])+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+e(v)+'</td>' for v in row)+'</tr>' for row in t['rows'])+'</tbody></table></div><a class="table-download" href="../downloads/chapter-'+ch['id']+'-table.csv" download>下载这张核对表（CSV） ↓</a></section><div>'
    out+='</div><dl class="term-list">'+''.join(f'<div><dt>{e(t["term"])}</dt><dd>{e(t["meaning"])}</dd></div>' for t in ch.get('terms',[]))+'</dl><div class="section-label">开始操作 / 一步一步完成</div>'
    for i,s in enumerate(ch['steps'],1):
        out+=f'<section class="step" id="step-{i}"><div class="step-no">{i:02}</div><div><h2>{e(s["title"])}</h2><div class="where">操作位置 / {e(s["where"])}</div>{prose(s["body"])}'
        if s.get('code'):out+=f'<pre><code>{e(s["code"])}</code></pre>'
        if s.get('warning'):out+=f'<p class="warning">注意 / {e(s["warning"])}</p>'
        out+=f'<div class="checkline"><b>完成标志</b><span>{e(s["check"])}</span></div></div></section>'
    out+='<section class="checklist"><h2>完成后，逐项打勾。</h2><p>勾选帮助你记录进度，不代表项目替你完成了实机验收。</p>'
    for i,item in enumerate(ch['checklist']):out+=f'<label><input type="checkbox" data-check="{ch["id"]}-{i}"><span>{e(item)}</span></label>'
    out+='<div class="checks-status" aria-live="polite"></div></section><section class="trouble"><h2>遇到问题，从这里排查。</h2>'
    for t in ch['troubleshooting']:out+=f'<details><summary>{e(t["symptom"])}</summary>{prose(t["action"])}</details>'
    out+='</section><section class="notes"><h2>本项目的实际进展</h2><ul>'+''.join(f'<li>{e(x)}</li>' for x in ch['project_notes'])+'</ul></section><section class="references"><h2>资料与核对依据</h2><ol>'+''.join('<li>'+reference(r)+'</li>' for r in ch['references'])+'</ol><p>资料核对日期：2026-10-30。不同硬件、系统版本和材料需要重新验证。</p></section><nav class="chapter-nav" aria-label="上下章">'
    out+=(f'<a href="{n-1:02}.html"><small>← 上一章</small>{e(chapters[n-2]["title"])}</a>' if n>1 else '<a href="../route.html"><small>← 返回</small>完整制作路线</a>')
    out+=(f'<a href="{n+1:02}.html"><small>下一章 →</small>{e(chapters[n]["title"])}</a>' if n<16 else '<a href="../setup.html"><small>继续 →</small>获取项目与 AI 提示词</a>')
    out+='</nav></article></main>'
    return page(ch['title'],out,'../')

def ai_setup(primary=False):
    block=(BASE/'content/ai-setup.html').read_text().replace('{{REPO}}',e(REPO)).replace('{{PROMPT}}',e(SETUP['prompt']).replace('\n','<br>'))
    if primary:
        block=block.replace('<h2 class="ai-title">','<h1 class="ai-title">').replace('</h2><p>','</h1><p>')
        block=block.replace('<h3>','<h2>').replace('</h3>','</h2>')
    return block

def build():
    chapters=[]
    for name in ['hardware','software','structure']:
        data=json.loads((BASE/'content'/f'{name}.json').read_text())
        chapters.extend(data if isinstance(data,list) else data['chapters'])
    chapters.sort(key=lambda c:int(c['id']))
    assert [int(c['id']) for c in chapters]==list(range(1,17))
    (BASE/'content/chapters.json').write_text(json.dumps(chapters,ensure_ascii=False,indent=2))
    for ch in chapters:(SITE/'chapters'/f'{ch["id"]}.html').write_text(chapter_page(ch,chapters))
    import csv
    for id,t in TABLES.items():
        with (SITE/'downloads'/f'chapter-{id}-table.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(t['headers']);writer.writerows(t['rows'])
    original=(BASE/'content/home-template.html').read_text();body=re.search(r'<main>(.*?)</main>',original,re.S).group(1)
    body=body.replace('../assets/','assets/').replace('parts.html','chapters/02.html')
    body=body.replace('视觉样稿 · 内容待核定','中文制作教程 · 第 1 版').replace('本样稿不提供它们的打印模型','它们暂无可下载的打印模型').replace('当前仍是视觉样稿','每章都有准备、步骤、完成检查与排错')
    (SITE/'index.html').write_text(page('做一台，像你的相机。','<main id="main">'+body+ai_setup()+'</main>',home=True))
    body='<main id="main" class="layout"><div class="page-head"><div class="eyebrow">四个阶段 / 十六章</div><h1>一段一段地做，<br>每一步都讲清楚。</h1><p>从零件到第一张照片，再到外壳和完整检验。首次制作请按顺序阅读；每一步先看“完成标志”，再进入下一步。</p><a class="pill-button" href="chapters/01.html">从第 01 章开始 ↗</a></div><div class="route-toolbar"><label><span class="eyebrow">查找章节</span><input id="chapter-search" type="search" placeholder="搜索零件、接线、打印…" aria-label="搜索章节"></label><span id="search-count" aria-live="polite">共 16 章，按顺序阅读</span></div><div id="no-results" class="empty-results" hidden>没有找到匹配章节。试试“软件”“打印”或“检验”。</div>'
    for stage in range(1,5):
        body+=f'<section class="stage-block" id="stage-0{stage}"><div class="stage-title"><span class="big">0{stage}</span><div><h2>{STAGES[stage-1]}</h2><p>{STAGE_DESC[stage-1]}</p></div></div><div class="chapter-list">'
        for ch in chapters:
            if int(ch['stage'])==stage:body+=f'<a class="chapter-row" href="chapters/{ch["id"]}.html"><span class="row-no">{ch["id"]}</span><div><h3>{e(ch["title"])}</h3><span class="status">{e(ch["status"])}</span></div><p class="row-intro">{e(ch["goals"][0])}</p><span class="row-arrow">↗</span></a>'
        body+='</div></section>'
    body+='</main>';(SITE/'route.html').write_text(page('完整制作路线',body))
    prompt=SETUP['prompt'].replace('README、AGENTS.md（如有）和环境搭建文档','README、AGENTS.md（如有）和 GETTING-STARTED.md').replace('按文档安装依赖并运行自检','先运行文档规定的只读自检；确有需要时再按文档安装依赖')
    SETUP['prompt']=prompt;(BASE/'content/setup.json').write_text(json.dumps(SETUP,ensure_ascii=False,indent=2))
    (SITE/'downloads/AI搭建环境提示词.txt').write_text(prompt+'\n')
    body='<main id="main">'+ai_setup(primary=True)+'</main>'
    (SITE/'setup.html').write_text(page('获取项目与 AI 提示词',body))
    body='''<main id="main" class="layout"><div class="page-head"><div class="eyebrow">第 1 版 / 2026.10.30</div><h1>知道进展，<br>也知道下一步。</h1><p>这是一份依据 自制相机制作记录整理的中文教程。它帮助你理解、复现和逐步验证，不把设计图或电脑上的检查当作整机验收。</p></div><div class="setup-layout"><section><h2>阅读前先分清三件事。</h2><ol class="setup-list"><li><strong>已实测：</strong>有对应设备操作或明确用户反馈，例如基础拍照、基础开关机与手机传图。仍要看具体条件和未覆盖的边界。</li><li><strong>本地检查：</strong>软件测试、网格检查、切片与包校验，可发现部分问题，不能替代真实安装和使用。</li><li><strong>待验证：</strong>未确认的版本安装、材料配合、长期稳定性和完整整机检查，需要照检查项补做。</li></ol><h2>首页的三台相机</h2><p>中间高方盒沿用本项目外形，旁边两台用于展示 DIY 外形可能性。产品图为材质与灯光演绎，概念机尚未做内部结构适配。</p></section><section><h2>当前关键边界</h2><ul class="setup-list"><li>软件版本以第 09 章核对记录为准。New Cam 1.0 已有修复后的实机配置成功报告；仍需下一次开机观察与完整回归。</li><li>没有电量采样硬件时，界面不显示推算的电量百分比。</li><li>结构文件的网格、切片通过，不等于打印后已经全部装配通过。</li><li>材料、镜头、屏幕或主板不同，尺寸、排线、软件与供电均需重新核对。</li><li>每个章节保留来源和完成检查。遇到不一致，先暂停该步骤并记录现象。</li></ul><div class="review-banner">本轮发布范围为相机制作教程及项目技术资料。频道视频制作资料不在此范围内。</div><a class="pill-button" href="chapters/15.html">打开最终检验章节 ↗</a></section></div></main>'''
    (SITE/'about.html').write_text(page('版本与验证',body))
    print(f'Built {len(chapters)+4} HTML pages; PDF is excluded from the website by user request.')

if __name__=='__main__':build()

"""Self-contained Chinese download page; no remote assets or insecure Web Share API."""
import base64
import hashlib
import html


SCRIPT = r"""(()=>{
const form=document.getElementById('photos'), boxes=[...form.querySelectorAll('input[name=ids]')],
count=document.getElementById('count'), button=document.getElementById('download'),
state=document.getElementById('state'); let left=Number(document.body.dataset.seconds), active=true, polling=false, disconnected=false;
function update(){const n=boxes.filter(x=>x.checked).length;count.textContent=n;button.disabled=!active||!n;}
function connected(){state.textContent='链接剩余 '+Math.ceil(left/60)+' 分钟；请保持相机与手机在同一 Wi-Fi。';}
function stop(){active=false;button.disabled=true;state.textContent='本次分享已结束，请在相机上重新开启分享。';document.querySelectorAll('.original').forEach(a=>{a.removeAttribute('href');a.setAttribute('aria-disabled','true');});}
boxes.forEach(x=>x.addEventListener('change',update));update();
setInterval(()=>{if(active){left--;if(left<=0)stop();else if(!disconnected)connected();}},1000);
setInterval(()=>{
 if(!active||polling)return;
 polling=true;const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),4000);
 fetch('status',{cache:'no-store',signal:controller.signal}).then(r=>{
  if(r.status===410){stop();return null;}if(!r.ok)throw Error();return r.json();
 }).then(s=>{
  if(!active||s===null)return;
  if(s.active===false){stop();return;}
  if(s.active!==true||typeof s.seconds_left!=='number'||!Number.isFinite(s.seconds_left))throw Error();
  left=s.seconds_left;if(left<=0){stop();return;}disconnected=false;connected();update();
 }).catch(()=>{if(active){disconnected=true;state.textContent='连接暂时中断，正在自动重试；照片选择会保留。';}})
 .finally(()=>{clearTimeout(timeout);polling=false;});
},5000);
form.addEventListener('submit',e=>{if(!active||!boxes.some(x=>x.checked))e.preventDefault();});
})();"""

CSP = ("default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
       "script-src 'sha256-{}'; connect-src 'self'; form-action 'self'; "
       "base-uri 'none'; frame-ancestors 'none'; object-src 'none'").format(
           base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode())


def render_page(records, seconds_left):
    cards = []
    for index, record in enumerate(records):
        name = html.escape(record.name, quote=True)
        cards.append('''<article><label><input type="checkbox" name="ids" value="{id}" checked>
<img src="thumbnail/{id}" alt="照片 {number}" loading="lazy"><span>{name}</span>
<small>{size:.1f} MB · {width} × {height}</small></label>
<a class="original" href="original/{id}" download>下载这张原图</a></article>'''.format(
            id=index, number=index + 1, name=name, size=record.byte_count / 1024 ** 2,
            width=record.width, height=record.height))
    return ('''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>相机照片 · 下载</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f5f3ed;color:#252525;font:16px -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;line-height:1.6}main{max-width:850px;margin:auto;padding:24px 18px 40px}h1{font-size:28px;margin:0 0 10px}p{margin:10px 0}#state{color:#605c50}.guide{padding:16px;background:white;border-radius:14px;margin:20px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:14px}article{min-width:0;border:1px solid #d8d4c9;border-radius:14px;padding:10px;background:white}label{display:block;cursor:pointer}input{width:22px;height:22px;accent-color:#234e3b}img{display:block;width:100%;height:150px;object-fit:contain;background:#ece9e1;border-radius:8px}span{display:block;overflow-wrap:anywhere;font-size:14px}small{display:block;color:#615f58;font-size:12px}a{display:inline-block;color:#234e3b;padding:10px 0}a[aria-disabled]{color:#888}button{display:block;width:100%;margin:24px 0 10px;padding:16px;border:0;border-radius:12px;background:#234e3b;color:white;font-size:18px;font-weight:600}button:disabled{background:#969b95}.notice{font-size:14px;color:#605c50}noscript{display:block;margin:12px 0}</style></head>
<body data-seconds="SECONDS"><main><h1>相机照片</h1><p>已选照片全部勾选，你可以取消不需要的照片。</p>
<p id="state">链接仅在本次分享期间有效，最长 15 分钟。请保持同一 Wi-Fi。</p>
<div class="guide"><strong>保存到 iPhone 照片</strong><p>多张请下载 ZIP，随后在“文件”→“下载”中解压，打开照片并选择“分享”→“存储图像”。</p><p class="notice">扫码只会打开本页；浏览器开始或完成下载不代表已存入“照片”。单张可点击“下载这张原图”。相机上的原图会保留。</p></div>
<form id="photos" method="get" action="download"><div class="grid">CARDS</div>
<button id="download" type="submit">下载勾选的原图 ZIP（<b id="count">COUNT</b> 张）</button>
<noscript>请选择至少一张再下载。关闭分享或超过有效期后，请在相机上重新开启。</noscript></form>
<p class="notice">原始 JPG 不压缩、不改名。若下载失败或页面已失效，请回相机重新分享。</p></main><script>SCRIPT</script></body></html>'''
            .replace('SECONDS', str(max(0, int(seconds_left))))
            .replace('COUNT', str(len(records))).replace('SCRIPT', SCRIPT)
            .replace('CARDS', ''.join(cards))).encode('utf-8')

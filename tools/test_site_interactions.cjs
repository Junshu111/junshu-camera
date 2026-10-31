/* Exercise rendered controls and links, not just source attributes.
 * Requires Playwright and a running HTTP preview. Reports stay outside the site.
 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const base = process.env.SITE_BASE_URL || 'http://127.0.0.1:8791/';
const pages = ['index.html', 'route.html', 'setup.html', 'about.html', ...Array.from({length:16}, (_,i) => 'chapters/' + String(i+1).padStart(2,'0') + '.html')];

(async () => {
  const launch = { headless: true, chromiumSandbox: true };
  const chrome = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  if (fs.existsSync(chrome)) launch.executablePath = chrome;
  const browser = await chromium.launch(launch);
  const context = await browser.newContext({ viewport: {width:1440,height:1000}, acceptDownloads:true });
  const page = await context.newPage();
  const report = {base, pages:0, links:0, copyButtons:0, checkboxes:0, disclosures:0, downloads:[], navigations:0, responsive:[], errors:[]};
  const internal = new Map();
  const external = new Set();
  const downloadLinks = new Map();
  page.on('pageerror', e => report.errors.push(e.message));
  async function check(name, fn) {
    try { await fn(); } catch(e) { report.errors.push(name + ': ' + e.message); }
  }
  async function visit(file) {
    const r = await page.goto(new URL(file, base).href);
    assert.equal(r.status(),200, file + ' must load');
    await page.waitForLoadState('networkidle');
  }
  try {
    for (const file of pages) {
      await visit(file);
      report.pages++;
      const links = await page.locator('a').evaluateAll(nodes => nodes.map(a => ({href:a.href, text:a.innerText, download:a.hasAttribute('download')})));
      report.links += links.length;
      report.copyButtons += await page.locator('button[data-ai-copy]').count();
      for (const link of links) {
        const u = new URL(link.href);
        if (u.origin === new URL(base).origin && u.pathname.startsWith(new URL(base).pathname)) {
          internal.set(u.href, link);
          if (link.download) downloadLinks.set(u.href,{file,...link});
        } else if (/^https?:/.test(u.protocol)) external.add(u.href);
      }
      await check(file + ' image loading', async () => {
        const broken = await page.locator('img').evaluateAll(nodes => nodes.filter(n => !n.complete || !n.naturalWidth).map(n => n.src));
        assert.deepEqual(broken,[]);
      });
      const boxes = page.locator('[data-check]');
      const n = await boxes.count();
      if (n) await check(file + ' checklist and persistence', async () => {
        for (let i=0;i<n;i++) await boxes.nth(i).check();
        assert.equal(await page.locator('[data-check]:checked').count(),n);
        assert.ok((await page.locator('.checks-status').innerText()).includes(n+' / '+n));
        await page.reload(); await page.waitForLoadState('networkidle');
        assert.equal(await page.locator('[data-check]:checked').count(),n,'All progress must survive reload');
        for (let i=0;i<n;i++) await boxes.nth(i).uncheck();
        report.checkboxes += n;
      });
      const details = page.locator('details');
      for (let i=0;i<await details.count();i++) await check(file+' disclosure '+i,async()=>{
        const d=details.nth(i);await d.locator('summary').click();
        assert.equal(await d.evaluate(el=>el.open),true);
        await d.locator('summary').click();assert.equal(await d.evaluate(el=>el.open),false);
        report.disclosures++;
      });
      if (file.startsWith('chapters/')) await check(file+' next chapter', async()=>{
        const link=page.locator('.chapter-nav a').last();const expected=await link.getAttribute('href');
        await link.click(); await page.waitForLoadState('networkidle');
        assert.equal(page.url(),new URL(expected,new URL(file,base)).href);report.navigations++;
      });
    }
    await check('chapter search',async()=>{
      await visit('route.html');
      await page.locator('#chapter-search').fill('打印');
      const rows=await page.locator('.chapter-row:visible').allTextContents();
      assert.ok(rows.length>0 && rows.length<16);
      assert.ok(rows.every(text=>text.includes('打印')),'Non-matching chapters must be hidden visually');
      await page.locator('#chapter-search').fill('zzzx-no-matching-chapter');
      assert.equal(await page.locator('.chapter-row:visible').count(),0);
      assert.equal(await page.locator('#no-results').isVisible(),true);
      await page.locator('#chapter-search').fill('');
      assert.equal(await page.locator('.chapter-row:visible').count(),16);
      assert.equal(await page.locator('#no-results').isVisible(),false);
    });
    await check('primary navigation',async()=>{
      await visit('index.html');
      for (const label of ['制作路线','版本与验证','获取项目','开始阅读 ↗']) {
        const link=page.locator('.site-header nav').getByRole('link',{name:label,exact:true});
        const expected=await link.getAttribute('href');await link.click();await page.waitForLoadState('networkidle');
        assert.equal(page.url(),new URL(expected,base).href);report.navigations++;
      }
      await page.locator('.brand').click();await page.waitForLoadState('networkidle');
      await page.locator('.poster-cta').click();
      assert.equal(new URL(page.url()).hash,'#ai-start');report.navigations+=2;
    });
    for (const [href,link] of internal) await check('internal destination '+href,async()=>{
      const u=new URL(href);const r=await context.request.get(u.href);assert.equal(r.status(),200);
      if(u.hash){ const html=await r.text();assert.ok(html.includes('id="'+decodeURIComponent(u.hash.slice(1))+'"')); }
    });
    for (const [href,link] of downloadLinks) await check('download '+href,async()=>{
      await visit(link.file);
      const locator=page.locator('a[download]').filter({hasText:link.text}).first();
      const pending=page.waitForEvent('download');await locator.click();const download=await pending;
      assert.equal(await download.failure(),null);
      const bytes=fs.readFileSync(await download.path());assert.ok(bytes.length>0);
      if(href.endsWith('.pdf'))assert.ok(bytes.subarray(0,5).toString()==='%PDF-');
      if(href.endsWith('.csv'))assert.ok(bytes.toString('utf8').includes(','));
      report.downloads.push({url:href,bytes:bytes.length,filename:download.suggestedFilename()});
    });
    for (const width of [390,768,2560]) {
      await page.setViewportSize({width,height:1000});
      for (const file of ['index.html','route.html','setup.html','chapters/01.html']) await check('responsive '+width+' '+file,async()=>{
        await visit(file);
        const size=await page.evaluate(()=>({viewport:innerWidth,content:document.documentElement.scrollWidth}));
        assert.ok(size.content<=size.viewport+1,'Page must not overflow horizontally: '+JSON.stringify(size));
        const buttons=page.locator('button[data-ai-copy]');
        for(let i=0;i<await buttons.count();i++){
          const b=buttons.nth(i);await b.scrollIntoViewIfNeeded();const box=await b.boundingBox();
          assert.ok(box.x>=0 && box.x+box.width<=width+1);
          // A tap must work at the same place a visitor sees the button.
          await b.click();assert.ok((await b.innerText()).includes('已复制') || (await page.locator('.ai-manual-copy').count())>0);
        }
        report.responsive.push({width,page:file,status:'PASS'});
      });
    }
    report.internalDestinations=internal.size;
    report.externalLinks=[...external];
    report.status=report.errors.length?'FAIL':'PASS';
    if(process.env.AUDIT_REPORT)fs.writeFileSync(process.env.AUDIT_REPORT,JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify(report,null,2));
    process.exitCode=report.errors.length?1:0;
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});

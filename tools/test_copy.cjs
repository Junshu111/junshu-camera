/* Browser regression tests. Run against a local HTTP server or the published site.
 * Requires Playwright; normal offline reading/building has no Node dependency.
 * A broken/removed copy fallback must fail on the actual clipboard contents.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const base = process.env.SITE_BASE_URL || 'http://127.0.0.1:8791/';
const setup = JSON.parse(fs.readFileSync(require('node:path').join(__dirname, '../content/setup.json'), 'utf8'));
const cases = process.env.COPY_CASES ? process.env.COPY_CASES.split(',') : ['normal', 'write-denied', 'api-missing', 'legacy-unavailable', 'manual', 'write-pending'];

(async () => {
  const launch = { headless: true, chromiumSandbox: true };
  const chrome = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  if (fs.existsSync(chrome)) launch.executablePath = chrome;
  const browser = await chromium.launch(launch);
  const results = [];
  try {
    for (const pageName of ['index.html', 'setup.html']) {
      for (const mode of cases) {
        const context = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'], viewport: { width: 1280, height: 900 } });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', e => errors.push(e.message));
        try {
          await page.goto(new URL(pageName, base).href);
          await page.waitForLoadState('networkidle');
          await page.evaluate(async mode => {
            await navigator.clipboard.writeText('__copy_regression_sentinel__');
            if (mode === 'write-denied' || mode === 'manual') {
              Object.defineProperty(navigator.clipboard, 'writeText', { configurable: true, value: () => Promise.reject(new DOMException('Test browser permission denied', 'NotAllowedError')) });
            }
            if (mode === 'api-missing') Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
            if (mode === 'write-pending') Object.defineProperty(navigator.clipboard, 'writeText', { configurable:true, value:()=>new Promise(()=>{}) });
            if (mode === 'legacy-unavailable' || mode === 'manual' || mode === 'write-pending') document.execCommand = () => false;
          }, mode);
          for (const [target, expected] of [['ai-repo', setup.repo_url], ['ai-copy-text', setup.prompt]]) {
            const button = page.locator('[data-ai-copy="' + target + '"]');
            await button.click();
            if (mode === 'manual' || mode === 'write-pending') {
              const input = page.locator(target === 'ai-repo' ? '.ai-link-block' : '.ai-prompt-block').locator('.ai-manual-copy textarea');
              await input.waitFor({ state: 'visible', timeout: 2500 });
              assert.equal(await input.inputValue(), expected, 'Manual text must contain the full intended content');
              assert.equal(await input.evaluate(el => el.selectionEnd - el.selectionStart), expected.length);
              assert.ok(!/已复制/.test(await button.innerText()), 'Blocked copy must not claim success');
            } else {
              const value = await page.evaluate(async mode => {
                if (mode === 'api-missing') delete navigator.clipboard;
                const value = await navigator.clipboard.readText();
                if (mode === 'api-missing') Object.defineProperty(navigator, 'clipboard', { configurable:true, value:undefined });
                return value;
              }, mode);
              assert.equal(value, expected, 'Click must put the complete intended content on the clipboard');
              assert.match(await button.innerText(), /已复制/, 'Success feedback must be on the button');
            }
          }
          assert.deepEqual(errors, [], 'No uncaught JavaScript errors');
          results.push({ page: pageName, mode, status: 'PASS' });
        } catch (e) {
          results.push({ page: pageName, mode, status: 'FAIL', error: e.message });
        } finally { await context.close(); }
      }
    }
  } finally { await browser.close(); }
  const report = { base, results, failures: results.filter(r => r.status !== 'PASS').length };
  if (process.env.COPY_REPORT) fs.writeFileSync(process.env.COPY_REPORT, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
  process.exitCode = report.failures ? 1 : 0;
})().catch(e => { console.error(e); process.exitCode = 1; });

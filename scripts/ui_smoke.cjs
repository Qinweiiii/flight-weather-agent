// Run with Playwright installed (or NODE_PATH pointing at a local installation).
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:5010';
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  const external = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (!r.url().startsWith(base)) external.push(r.url()); });
  const out = path.resolve(__dirname, '../artifacts/ui');
  fs.mkdirSync(out, { recursive: true });
  try {
    await page.goto(base);
    await page.getByLabel('用户 ID').fill('ui-audit');
    await page.getByRole('button', { name: '进入', exact: true }).click();
    await page.getByRole('button', { name: /原因与行动/ }).waitFor();
    await page.screenshot({ path: path.join(out, 'desktop-start.png') });
    await page.getByRole('button', { name: /原因与行动/ }).click();
    await page.getByRole('heading', { name: '行动候选与验证', exact: true }).waitFor({ timeout: 30000 });
    await page.locator('.chart-panel canvas').waitFor();
    const chartPixels = await page.locator('.chart-panel canvas').first().evaluate(canvas => {
      const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let nonblank = 0;
      for (let i = 3; i < data.length; i += 4) if (data[i] > 0) nonblank++;
      return nonblank;
    });
    assert(chartPixels > 1000, 'Chart must render nonblank pixels');
    const desktopScroll = await page.locator('.message-list').evaluate(el => ({ height: el.clientHeight, content: el.scrollHeight }));
    assert(desktopScroll.content > desktopScroll.height, 'Long responses must scroll');
    await page.screenshot({ path: path.join(out, 'desktop-result.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No horizontal overflow');
    await page.locator('.message-list').evaluate(el => el.scrollTop = el.scrollHeight);
    await page.screenshot({ path: path.join(out, 'mobile-result.png') });
    await page.getByRole('button', { name: 'Memory', exact: true }).click();
    await page.getByRole('heading', { name: '长期记忆', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Skills', exact: true }).click();
    assert.equal(await page.locator('.skill-card').count(), 8);
    await page.screenshot({ path: path.join(out, 'mobile-skills.png') });
    await page.getByRole('button', { name: 'Workbench', exact: true }).click();
    await page.getByRole('button', { name: '新会话', exact: true }).click();
    await page.getByRole('button', { name: /原因与行动/ }).waitFor();
    await page.route('**/api/query_stream', route => route.fulfill({
      contentType: 'text/event-stream',
      body: 'data: ' + JSON.stringify({ type: 'chunk', content: '部分结果' }) + '\n\n',
    }));
    await page.locator('textarea').fill('测试连接提前结束');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await page.locator('.message-error').filter({ hasText: '连接提前结束' }).waitFor();
    assert.equal(await page.locator('.answer-body').last().innerText(), '部分结果');
    assert.equal(errors.length, 0, errors.join('\n'));
    assert.equal(external.length, 0, 'Browser assets must load locally');
    fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({ chartPixels, desktopScroll, errors, external, mobileWidth:390, truncatedStreamDetected:true }, null, 2));
    console.log(JSON.stringify({ chartPixels, desktopScroll, errors, external, screenshots:out }));
  } finally {
    await browser.close();
  }
})().catch(err => { console.error(err); process.exit(1); });

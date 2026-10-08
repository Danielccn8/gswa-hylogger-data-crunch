/* Browser acceptance for HyLogger Explorer v2 against the running local API (no mocks).
   PLAYWRIGHT_MODULE=<path to playwright> REVIEW_URL=http://127.0.0.1:8100 REVIEW_OUTPUT=<dir> node scripts/check_explorer.cjs */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const output = path.resolve(process.env.REVIEW_OUTPUT || 'C:/HyLoggerData/logs/browser');
  fs.mkdirSync(output, {recursive: true});
  const base = process.env.REVIEW_URL || 'http://127.0.0.1:8100';
  const hole = process.env.REVIEW_HOLE || '13TRDD012';
  const browser = await chromium.launch({headless: true, channel: process.env.BROWSER_CHANNEL || 'msedge'});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  page.on('pageerror', e => errors.push(`${page.url()} :: ${e?.stack || e?.message || JSON.stringify(e)}`));
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 500) errors.push(`${r.status()} ${r.url()}`); });
  const timings = {};
  const shot = name => page.screenshot({path: path.join(output, `${name}.png`), fullPage: false});
  const time = async (name, fn) => { const t = Date.now(); await fn(); timings[name] = Date.now() - t; };

  await time('map', async () => { await page.goto(`${base}/#/map`); await page.locator('#metrics .metric').first().waitFor(); await page.locator('#legend span').first().waitFor(); });
  const summary = await (await page.request.get(`${base}/api/v2/summary`)).json();
  assert.ok(summary.counts.prepared > 1000, 'statewide processed holes prepared');
  await shot('01-map-status');
  await page.selectOption('#analysis', 'tir');
  await page.waitForTimeout(800);
  await page.selectOption('#colour', 'cluster');
  await page.selectOption('#hole-k', '10');
  await page.waitForTimeout(1500);
  assert.match(await page.locator('#legend').textContent(), /\+|dominant/);
  await shot('02-map-hole-clusters');

  await time('hole', async () => { await page.goto(`${base}/#/hole/${encodeURIComponent(hole)}`); await page.locator('#strip canvas').waitFor(); });
  const box = await page.locator('#strip canvas').boundingBox();
  await page.mouse.click(box.x + 120, box.y + Math.min(250, box.height / 3));
  await page.locator('#interval .bars').first().waitFor();
  await shot('03-hole-striplog');

  await time('similarity', async () => { await page.goto(`${base}/#/similarity/${encodeURIComponent(hole)}`); await page.locator('#matrix canvas').waitFor({timeout: 30000}); await page.locator('#embed canvas').waitFor(); });
  await shot('04-similarity');
  await page.click('#compare-strips');
  await page.locator('#strips canvas').first().waitFor();
  assert.ok(await page.locator('#strips canvas').count() >= 2);
  await page.locator('#strips').scrollIntoViewIfNeeded();
  await shot('05-downhole-comparison');

  await time('clusters', async () => { await page.goto(`${base}/#/clusters`); await page.locator('.cluster-cards .panel').first().waitFor(); });
  await shot('06-clusters');
  await page.goto(`${base}/#/anomalies`); await page.locator('#body table tbody tr').first().waitFor();
  await shot('07-anomalies');

  await time('prediction', async () => { await page.goto(`${base}/#/prediction`); await page.locator('#cv .metric').first().waitFor(); });
  const map = await page.locator('#map').boundingBox();
  await page.mouse.click(map.x + map.width * 0.55, map.y + map.height * 0.6);
  await page.locator('#result .stack').first().waitFor();
  await shot('08-prediction');

  await page.goto(`${base}/#/data`); await page.locator('#catalog table').waitFor(); await page.locator('#jobs table, #jobs .notice').first().waitFor();
  await shot('09-data');
  await page.goto(`${base}/#/method`); await page.locator('h1').waitFor();
  await shot('10-method');

  const mobile = await browser.newPage({viewport: {width: 390, height: 844}});
  await mobile.goto(`${base}/#/hole/${encodeURIComponent(hole)}`); await mobile.locator('#strip canvas').waitFor();
  const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  await mobile.screenshot({path: path.join(output, '11-mobile-hole.png')});

  await browser.close();
  const result = {base, hole, timings, mobile_horizontal_overflow_px: overflow, errors, prepared: summary.counts.prepared};
  fs.writeFileSync(path.join(output, 'acceptance.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  assert.equal(errors.length, 0, `Page or API errors: ${errors.join('; ')}`);
  assert.ok(overflow <= 2, 'no horizontal page overflow on mobile');
})().catch(error => { console.error(error); process.exit(1); });

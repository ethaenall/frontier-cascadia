/** Bounded, static-only presenter QA. Outputs are isolated under /tmp.
 * Run: node static/cinematic/tests/presenter.mjs
 * Requires the separately started STATIC deck server at http://127.0.0.1:8894/.
 * Never starts a server, follows a console link, or requests a detector API.
 */
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from 'node:fs';
import http from 'node:http';

const root = realpathSync(resolve(dirname(fileURLToPath(import.meta.url)), '../../..'));
const deckRoot = realpathSync(join(root, 'static/cinematic'));
const base = new URL('http://127.0.0.1:8894/');
const output = mkdtempSync('/tmp/threshold-presentation-qa-');
const browserTemp = join(output, 'browser-temp');
mkdirSync(browserTemp);
process.env.TMPDIR = browserTemp;
process.env.TMP = browserTemp;
process.env.TEMP = browserTemp;
const started = Date.now();
const session = `qa-${randomUUID()}`;
const report = {
  schemaVersion: 1, startedAt: new Date(started).toISOString(), output,
  staticURL: base.href, status: 'RUNNING', tests: [], screenshots: [], renderers: [],
  safety: { serverStarted: false, detectorAPIRequests: 0, consoleLinksFollowed: 0,
    hardwareOperations: 0, requestedOrigin: base.origin, serviceWorkers: 'blocked' },
  limits: { overallMs: 115000, actionMs: 3500, navigationMs: 9000,
    noAutoAdvanceObservationMs: 2600, viewportDesktop: [1440, 900], viewportNarrow: [390, 844] },
  scope: 'Functional presentation evidence only. Not a GPU performance, hardware, physical sensing, or software-alarm verification.',
  backendCaveat: 'A fallback functional pass is never proof that WebGPU rendered. See exact backend and diagnostics.',
  network: { requests: [], blocked: [], failures: [], responses: [] },
  pageErrors: [], consoleErrors: [], harnessErrors: [], source: {}, cleanup: { status: 'PENDING' },
};
console.log(`THRESHOLD_QA_OUTPUT=${output}`);
const sourceFiles = () => {
  const result = [];
  function walk(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.name.startsWith('.') || ['node_modules', 'tests'].includes(entry.name)) continue;
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (entry.isFile()) result.push(path);
    }
  }
  walk(deckRoot);
  result.push(join(deckRoot, 'tests/presenter.mjs'), join(root, 'scripts/present.py'));
  return result.sort();
};
function hashes() {
  return Object.fromEntries(sourceFiles().map(path => [relative(root, path),
    createHash('sha256').update(readFileSync(path)).digest('hex')]));
}
function persist() { writeFileSync(join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n'); }
report.source.before = hashes();
persist();
let browser, desktop, currentPage, cancelled = false;
const contexts = new Set();
const allowedPaths = new Set(['/', '/__version']);
for (const path of sourceFiles()) {
  if (path.startsWith(deckRoot + '/') && /\.(?:html|js|css|png|jpg|jpeg|webp|svg|woff2|ico)$/.test(path)) {
    allowedPaths.add('/' + relative(deckRoot, path).split('\\').join('/'));
  }
}
const sleepForObservation = (page, ms) => page.waitForTimeout(ms); // Deliberate, bounded test dwell; not agent polling.
const state = page => page.evaluate(() => window.thresholdPresentation.getState());
const normalized = value => value.replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
const errorData = error => ({ name: error?.name || 'Error', message: String(error?.message || error), stack: error?.stack });
process.on('uncaughtExceptionMonitor', error => {
  report.harnessErrors.push({ phase: 'uncaught exception', ...errorData(error) }); persist();
});
process.once('exit', code => {
  if (report.status !== 'RUNNING') return;
  report.status = 'UNKNOWN'; report.interrupted = true;
  report.cleanup = { status: 'UNKNOWN', browserCloseResolved: false };
  report.harnessErrors.push({ name: 'InterruptedHarness', message: `Process exited ${code} before finalization. No PASS.` });
  try { report.source.after = hashes(); } catch (error) { report.source.hashError = errorData(error); }
  report.source.stable = false; persist();
  console.error(`THRESHOLD_QA_RESULT=UNKNOWN interrupted; report=${join(output, 'report.json')}`);
});
async function test(name, fn) {
  if (cancelled) return false;
  const start = Date.now();
  const row = { name, status: 'RUNNING' };
  report.tests.push(row); persist();
  try { row.details = (await fn()) ?? null; row.status = 'PASS'; }
  catch (error) {
    row.status = error?.name === 'AssertionError' ? 'FAIL' : 'UNKNOWN';
    row.error = errorData(error);
    if (row.status === 'UNKNOWN') report.harnessErrors.push({ test: name, ...row.error });
    console.error(`${row.status}: ${name}: ${row.error.message}`);
  }
  if (row.status !== 'PASS' && currentPage && !currentPage.isClosed()) {
    try { await currentPage.evaluate(() => document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close())); }
    catch (error) { report.harnessErrors.push({ test: name, phase: 'test UI cleanup', ...errorData(error) }); }
  }
  row.durationMs = Date.now() - start; persist();
  console.log(`${row.status}: ${name} (${row.durationMs} ms)`);
  return row.status === 'PASS';
}
async function capture(page, name) {
  const filename = `${name}.png`;
  await page.screenshot({ path: join(output, filename), fullPage: false, timeout: 6000 });
  report.screenshots.push({ filename, viewport: page.viewportSize(), state: await state(page), at: new Date().toISOString() });
}
function rawRequest(path, method = 'HEAD') {
  assert(!path.toLowerCase().includes('/api'), 'Harness refuses all API paths');
  return new Promise((resolveRequest, reject) => {
    const req = http.request({ hostname: base.hostname, port: base.port, method, path,
      headers: { 'User-Agent': 'Threshold-Static-Presenter-QA' } }, res => {
      const chunks = []; let size = 0;
      res.on('data', chunk => {
        size += chunk.length;
        if (size > 512000) { req.destroy(new Error('Unexpectedly large static response')); return; }
        chunks.push(chunk);
      });
      res.on('end', () => resolveRequest({ status: res.statusCode, headers: res.headers,
        ...(method === 'GET' ? { body: Buffer.concat(chunks).toString('utf8') } : {}) }));
      res.on('error', reject);
    });
    req.setTimeout(3000, () => req.destroy(new Error(`Static request timeout: ${path}`)));
    req.on('error', reject); req.end();
  });
}
function trackPage(page, label) {
  page.on('pageerror', error => {
    const entry = { page: label, url: page.url(), ...errorData(error) };
    report.pageErrors.push(entry);
    if (error.stack?.includes('initializeThresholdQA')) report.harnessErrors.push({ ...entry, phase: 'test-only browser init' });
  });
  page.on('console', message => {
    if (message.type() === 'error') report.consoleErrors.push({ page: label, text: message.text(), location: message.location() });
  });
  page.on('crash', () => report.harnessErrors.push({ page: label, name: 'PageCrash', message: 'Chromium page crashed' }));
  page.setDefaultTimeout(3500); page.setDefaultNavigationTimeout(9000);
}
async function makeContext({ viewport = { width: 1440, height: 900 }, reducedMotion = 'no-preference' } = {}) {
  const context = await browser.newContext({ viewport, reducedMotion, deviceScaleFactor: 1,
    acceptDownloads: false, serviceWorkers: 'block' });
  contexts.add(context);
  await context.addInitScript(function initializeThresholdQA({ session, origin }) {
    // about:blank is an opaque origin. Do not touch its forbidden sessionStorage.
    if (location.origin !== origin) return;
    // Test-only storage is inside a fresh, temporary browser context. No app settings are touched.
    sessionStorage.setItem('threshold-quality', 'low');
    sessionStorage.setItem('threshold-session', session);
  }, { session, origin: base.origin });
  context.on('page', page => trackPage(page, `page-${context.pages().length}`));
  context.on('request', req => report.network.requests.push({ url: req.url(), method: req.method(), type: req.resourceType() }));
  context.on('requestfailed', req => report.network.failures.push({ url: req.url(), failure: req.failure()?.errorText }));
  context.on('response', res => { if (res.status() >= 400) report.network.responses.push({ url: res.url(), status: res.status() }); });
  await context.route('**/*', route => {
    const req = route.request(); const url = new URL(req.url());
    if (url.origin !== base.origin || !allowedPaths.has(url.pathname) || req.method() !== 'GET' || url.username || url.password) {
      report.network.blocked.push({ url: url.href, method: req.method(), reason: 'Not an allowed static deck resource' });
      if (/\/api(?:\/|$)/i.test(url.pathname)) report.safety.detectorAPIRequests++;
      return route.abort('blockedbyclient');
    }
    return route.continue();
  });
  if (typeof context.routeWebSocket === 'function') {
    await context.routeWebSocket('**/*', ws => {
      report.network.blocked.push({ url: ws.url(), reason: 'WebSocket prohibited in static presentation QA' }); ws.close();
    });
  }
  return context;
}
async function openAudience(context) {
  const page = await context.newPage(); currentPage = page;
  await page.goto(base.href, { waitUntil: 'load' });
  await page.waitForFunction(() => window.thresholdPresentation && document.body.dataset.ready === 'true', null, { timeout: 15000 });
  const info = await state(page);
  report.renderers.push({ viewport: page.viewportSize(), state: info });
  assert.equal(info.audience, true);
  assert.notEqual(info.renderer, 'Loading graphics');
  return page;
}
async function settle(page) {
  await page.waitForFunction(() => {
    const copy = document.querySelector('#copy');
    return window.thresholdPresentation.getState().transition?.active !== true &&
      !document.body.classList.contains('revealing') && document.querySelector('#letter-flight').childElementCount === 0 &&
      copy.getAnimations().every(animation => animation.playState !== 'running');
  }, null, { timeout: 4500 });
}
async function go(page, index, { settled = true } = {}) {
  await page.evaluate(value => window.thresholdPresentation.goTo(value), index);
  await page.waitForFunction(value => window.thresholdPresentation.getState().chapter === value, index, { timeout: 3500 });
  if (settled) await settle(page);
}
async function keyboardTarget(page) {
  await page.evaluate(() => document.activeElement?.blur());
}
async function checkTitle(page, chapter) {
  const lines = await page.locator('#chapter-title .title-line').allTextContents();
  assert.deepEqual(lines.map(normalized), chapter.title.map(normalized));
}
async function bounds(page) {
  return page.evaluate(() => {
    const width = innerWidth, height = innerHeight, violations = [], ellipsized = [];
    const visible = element => {
      for (let p = element; p; p = p.parentElement) {
        const css = getComputedStyle(p);
        if (p.hidden || css.display === 'none' || css.visibility === 'hidden' || Number(css.opacity) < 0.05) return false;
      }
      return !!element.getClientRects().length;
    };
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let text;
    while ((text = walker.nextNode())) {
      const element = text.parentElement;
      if (!element || !text.textContent.trim() || element.closest('script, style, .sr-only, #letter-flight') ||
          (element.closest('.skip-link') && document.activeElement !== element) || !visible(element)) continue;
      const range = document.createRange(); range.selectNodeContents(text);
      const css = getComputedStyle(element);
      if (css.textOverflow === 'ellipsis') { if (element.scrollWidth > element.clientWidth) ellipsized.push(text.textContent); continue; }
      for (const r of range.getClientRects()) {
        if (r.width && r.height && (r.left < -1 || r.right > width + 1 || r.top < -1 || r.bottom > height + 1)) {
          violations.push({ text: text.textContent.slice(0, 90), parent: element.id || element.className || element.tagName,
            rect: { left: r.left, right: r.right, top: r.top, bottom: r.bottom } });
        }
      }
    }
    for (const selector of ['#copy', '.masthead', '.foot', '#demo-stage', '#comparison-stage']) {
      const el = document.querySelector(selector); if (!el || !visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.left < -1 || r.right > width + 1 || r.top < -1 || r.bottom > height + 1)
        violations.push({ selector, rect: { left: r.left, right: r.right, top: r.top, bottom: r.bottom } });
    }
    const separatedPairs = [
      ['#copy', '.masthead'], ['#copy', '.foot'], ['#copy', '#comparison-stage'], ['#copy', '#demo-stage'],
      ['#copy', '#product-stage'], ['#copy', '#detection-cue'], ['#copy', '#future-annotation'],
      ['#comparison-stage', '#visual-caption'], ['#demo-stage', '#visual-caption'],
      ['#product-stage', '#visual-caption'],
    ];
    for (const [a, b] of separatedPairs) {
      const first = document.querySelector(a), second = document.querySelector(b);
      if (!first || !second || !visible(first) || !visible(second)) continue;
      const x = first.getBoundingClientRect(), y = second.getBoundingClientRect();
      const overlapWidth = Math.min(x.right, y.right) - Math.max(x.left, y.left);
      const overlapHeight = Math.min(x.bottom, y.bottom) - Math.max(x.top, y.top);
      if (overlapWidth > 2 && overlapHeight > 2) violations.push({ overlap: [a, b], overlapWidth, overlapHeight });
    }
    return { width, height, scrollWidth: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight,
      violations, ellipsized };
  });
}
async function focusChecks(page) {
  await go(page, 3); await page.locator('#settings-toggle').focus();
  await page.keyboard.press('Enter');
  assert(await page.locator('#settings-dialog').evaluate(dialog => dialog.open));
  const focusTrace = [];
  report.focusTraces ||= []; report.focusTraces.push({ viewport: page.viewportSize(), focusTrace });
  let browserChromeTransition = false;
  for (let n = 0; n < 14; n++) {
    await page.keyboard.press('Tab');
    const focus = await page.evaluate(() => ({ inside: !!document.activeElement.closest('#settings-dialog'),
      id: document.activeElement.id, tag: document.activeElement.tagName, documentFocused: document.hasFocus() }));
    focusTrace.push(focus);
    // Native Chromium dialogs may pass focus through browser chrome (BODY) when tabbing around.
    // An outside page control is never allowed; the next Tab must return to the modal.
    assert(focus.inside || (focus.tag === 'BODY' && !browserChromeTransition),
      `Modal focus reached an outside page control or failed to wrap: ${JSON.stringify(focusTrace)}`);
    browserChromeTransition = !focus.inside;
  }
  if (browserChromeTransition) {
    await page.keyboard.press('Tab');
    assert(await page.evaluate(() => !!document.activeElement.closest('#settings-dialog')), 'Modal tab cycle did not return');
  }
  await page.locator('#next').focus();
  assert.notEqual(await page.evaluate(() => document.activeElement.id), 'next', 'Modal must make audience controls inert');
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'settings-toggle', 'Closing dialog must restore focus');
  await page.locator('#next').focus();
  const outline = await page.locator('#next').evaluate(el => ({ style: getComputedStyle(el).outlineStyle,
    width: parseFloat(getComputedStyle(el).outlineWidth) }));
  assert(outline.style !== 'none' && outline.width >= 1, 'Keyboard focus outline missing');
  await page.keyboard.press('Space');
  assert.equal((await state(page)).chapter, 4, 'Space on focused Next must advance exactly one chapter');
  return { focusRestored: true, nextButtonOutline: outline, focusTrace };
}
const rejectedFixtures = [
  'https://example.invalid/', 'http://example.invalid/', 'http://127.0.0.1.evil.invalid/',
  'http://127.0.0.1:8894/?token=qa-placeholder-not-a-secret', 'http://127.0.0.1:8894/?mode=test',
  'http://fixture:fixture@127.0.0.1:8894/', 'http://127.0.0.1:8894/#pairing',
];
async function runSuite() {
  const serverOK = await test('static server identity and isolation headers', async () => {
    const res = await rawRequest('/', 'GET');
    assert.equal(res.status, 200);
    assert(res.body.includes('id="chapter-title"') && res.body.includes('id="presenter-script"'), 'Not the known STATIC presentation');
    assert.equal(res.headers['x-content-type-options'], 'nosniff');
    assert.match(res.headers['content-security-policy'] || '', /connect-src 'self'/);
    return { status: res.status, csp: res.headers['content-security-policy'] };
  });
  if (!serverOK) throw new Error('Static identity gate failed; browser was not admitted');
  await test('static server denies traversal, secrets, dependencies and tests', async () => {
    const paths = ['/.env', '/.git/config', '/node_modules/three/build/three.webgpu.js', '/tests/presenter.mjs',
      '/..%2findex.html', '/%2e%2e%2f%2e%2e%2f.env', '/assets/../node_modules/three/build/three.webgpu.js', '/package.json'];
    const checks = [];
    for (const path of paths) {
      const response = await rawRequest(path);
      checks.push({ path, status: response.status });
    }
    assert(checks.every(check => [400, 403, 404].includes(check.status)), JSON.stringify(checks));
    return checks;
  });
  const require = createRequire(import.meta.url);
  const playwrightPath = process.env.PLAYWRIGHT_MODULE || 'playwright';
  const { chromium } = require(playwrightPath);
  report.browser = { playwrightPath, headless: true, channel: 'chromium', quality: 'low',
    args: ['--enable-unsafe-webgpu', '--use-angle=metal', '--disable-background-networking', '--disable-component-update', '--disable-sync'] };
  browser = await chromium.launch({ headless: true, channel: 'chromium', args: report.browser.args,
    downloadsPath: join(output, 'downloads'), timeout: 15000 });
  report.browser.version = browser.version();
  desktop = await makeContext();
  const page = await openAudience(desktop); currentPage = page;
  const chapters = await page.evaluate(() => window.thresholdPresentation.getChapters());
  const idx = Object.fromEntries(chapters.map((chapter, index) => [chapter.id, index]));
  const finalIndex = chapters.length - 1;
  const expectedIDs = ['emancipator', 'reveal', 'a-door', 'door-state', 'sensor-comparison', 'the-question', 'the-signal', 'built-software', 'console-handoff', 'fall-research', 'start-here'];
  await test('eleven chapter schema and serious unbranded opening', async () => {
    assert.equal(chapters.length, 11); assert.deepEqual(chapters.map(c => c.id), expectedIDs);
    assert.equal((await state(page)).chapter, 0); await checkTitle(page, chapters[0]);
    const brand = await page.locator('#brand').evaluate(el => ({ display: getComputedStyle(el).display, opacity: getComputedStyle(el).opacity }));
    assert(brand.display === 'none' || Number(brand.opacity) === 0, 'Opening brand is visible');
    const visibleText = await page.locator('#stage').innerText();
    assert(!/threshold|prototype|joke|not implemented|synthetic/i.test(visibleText), 'Opening leaks brand/disclaimer/joke');
    assert(await page.locator('#previous').isDisabled());
    await capture(page, 'desktop-00-opening'); return { chapters, brand };
  });
  await test('Space reveals settled THRESHOLD glyphs', async () => {
    await keyboardTarget(page); await page.keyboard.press('Space');
    assert.equal((await state(page)).chapter, 1);
    await settle(page); await checkTitle(page, chapters[1]);
    assert.equal(await page.locator('#chapter-title .glyph').allTextContents().then(text => text.join('')), 'THRESHOLD');
    assert.equal(await page.locator('.flying-letter').count(), 0);
    assert.equal(await page.locator('#chapter-title').evaluate(el => getComputedStyle(el).opacity), '1');
    await capture(page, 'desktop-01-reveal');
  });
  await test('settled reveal-to-joke echo preserves centered glyph geometry on its first frame', async () => {
    await go(page, idx.reveal);
    const geometry = await page.evaluate(next => {
      const before = [...document.querySelectorAll('#chapter-title .glyph')].map(glyph => ({
        text: glyph.textContent, left: glyph.getBoundingClientRect().left,
      }));
      window.thresholdPresentation.goTo(next);
      const after = [...document.querySelectorAll('.transition-echo .glyph')].map(glyph => ({
        text: glyph.textContent, left: glyph.getBoundingClientRect().left,
      }));
      return { before, after, transition: window.thresholdPresentation.getState().transition };
    }, idx['a-door']);
    assert.equal(geometry.before.map(glyph => glyph.text).join(''), 'THRESHOLD');
    assert.equal(geometry.after.map(glyph => glyph.text).join(''), 'THRESHOLD', 'Reveal ghost lost its glyph structure');
    const deltas = geometry.before.map((glyph, index) => Math.abs(glyph.left - geometry.after[index].left));
    assert(deltas.every(delta => delta < 3), `Centered reveal ghost jumped horizontally: ${JSON.stringify(deltas)}`);
    await settle(page);
    assert.equal(await page.locator('.transition-echo').count(), 0);
    return { ...geometry, leftDeltasPx: deltas, synchronousFirstFrameMeasurement: true };
  });
  await test('rapid delayed transition retarget never resurrects a hidden incoming product title', async () => {
    await go(page, idx['a-door']);
    const observations = await page.evaluate(async indices => {
      const trace = [];
      const observe = label => trace.push({ label, elapsedMs: performance.now() - start,
        ghosts: [...document.querySelectorAll('.transition-echo')].map(node => node.textContent),
        transition: window.thresholdPresentation.getState().transition });
      const dwell = ms => new Promise(resolve => setTimeout(resolve, ms));
      const start = performance.now();
      window.thresholdPresentation.goTo(indices.comparison); observe('comparison-first-frame');
      await dwell(170); observe('comparison-before-retarget');
      window.thresholdPresentation.goTo(indices.product); observe('product-first-frame');
      await dwell(140); observe('product-before-retarget');
      window.thresholdPresentation.goTo(indices.door); observe('door-first-frame');
      await dwell(150); observe('door-plus-150ms');
      return trace;
    }, { comparison: idx['sensor-comparison'], product: idx['built-software'], door: idx['door-state'] });
    const prerequisites = observations.filter(row => row.label.endsWith('before-retarget'));
    if (!prerequisites.every(row => row.transition.active)) throw new Error(`Timed transition precondition missed: ${JSON.stringify(observations)}`);
    const interrupted = observations.filter(row => ['product-first-frame', 'product-before-retarget', 'door-first-frame', 'door-plus-150ms'].includes(row.label));
    assert(interrupted.every(row => row.ghosts.length === 0), `Delayed hidden title became a phantom echo: ${JSON.stringify(observations)}`);
    assert(observations.every(row => row.transition.animations <= 24 && row.transition.echoes <= 3), 'Transition work exceeded its bounded fixture budget');
    await settle(page); await checkTitle(page, chapters[idx['door-state']]);
    const final = (await state(page)).transition;
    assert.deepEqual({ active: final.active, animations: final.animations, echoes: final.echoes },
      { active: false, animations: 0, echoes: 0 });
    assert.equal(await page.locator('.transition-echo, .flying-letter').count(), 0);
    return { observations, final };
  });
  await test('quick navigation cancels flying letters and stale callbacks', async () => {
    await go(page, 0); await go(page, 1, { settled: false });
    const inFlight = await page.locator('.flying-letter').count();
    assert(inFlight > 0 && inFlight <= 80, 'Full-motion reveal must create a bounded glyph flight');
    await go(page, 2, { settled: false });
    assert.equal(await page.locator('.flying-letter').count(), 0);
    await sleepForObservation(page, 2100); await checkTitle(page, chapters[2]);
    assert.equal((await state(page)).chapter, 2);
    assert.equal(await page.locator('.flying-letter').count(), 0);
    assert.equal(await page.locator('#chapter-title').evaluate(el => getComputedStyle(el).opacity), '1');
    const transition = (await state(page)).transition;
    assert(transition && !transition.active && transition.animations === 0 && transition.echoes === 0, 'Interrupted transition resources must settle and clear');
    assert.equal(await page.locator('.transition-echo').count(), 0);
    return { observedFlyingLetters: inFlight, cancelled: true, transition };
  });
  await test('keyboard navigation, repeated key guard and disabled audience bounds', async () => {
    await go(page, 2); await keyboardTarget(page);
    await page.keyboard.down('Space'); assert.equal((await state(page)).chapter, 3);
    await page.keyboard.down('Space'); assert.equal((await state(page)).chapter, 3, 'Repeated key advanced again');
    await page.keyboard.up('Space');
    await page.keyboard.press('ArrowLeft'); assert.equal((await state(page)).chapter, 2);
    await page.keyboard.press('End'); assert.equal((await state(page)).chapter, finalIndex);
    assert(await page.locator('#next').isDisabled()); await page.keyboard.press('Space'); assert.equal((await state(page)).chapter, finalIndex);
    await page.keyboard.press('Home'); assert.equal((await state(page)).chapter, 0);
    assert(await page.locator('#previous').isDisabled()); await page.keyboard.press('ArrowLeft'); assert.equal((await state(page)).chapter, 0);
  });
  await test('no automatic advancement during bounded dwell', async () => {
    await go(page, idx['the-question']); const before = await state(page);
    await sleepForObservation(page, report.limits.noAutoAdvanceObservationMs);
    assert.equal((await state(page)).chapter, before.chapter);
    return { chapter: before.chapter, observedMs: report.limits.noAutoAdvanceObservationMs, notAnIndefiniteGuarantee: true };
  });
  await test('desktop all chapters: correct titles, labels and bounded visible text', async () => {
    const layouts = [];
    for (let i = 0; i < chapters.length; i++) {
      await go(page, i); await checkTitle(page, chapters[i]);
      const layout = await bounds(page); layouts.push({ chapter: i, ...layout });
      if (chapters[i].badge) assert.equal(normalized(await page.locator('#claim-badge').innerText()), chapters[i].badge.text);
    }
    writeFileSync(join(output, 'desktop-layout.json'), JSON.stringify(layouts, null, 2) + '\n');
    assert(layouts.every(item => item.scrollWidth <= 1441 && item.scrollHeight <= 901 && item.violations.length === 0),
      JSON.stringify(layouts.filter(item => item.violations.length || item.scrollWidth > 1441 || item.scrollHeight > 901)));
    await capture(page, 'desktop-close'); return layouts;
  });
  await test('contact comparison frames intended entry counting as product vision', async () => {
    await go(page, idx['sensor-comparison']);
    assert(await page.locator('#comparison-stage').isVisible());
    assert.match(await page.locator('#chapter-description').innerText(), /Magnetic contact sensor vs\. Threshold/);
    assert.equal(await page.locator('#comparison-rows tr').count(), 4);
    const entry = page.locator('#comparison-rows tr').filter({ hasText: /count.*(?:entries|people)|room entries/i });
    assert.equal(await entry.count(), 1);
    const cells = (await entry.locator('th, td').allTextContents()).map(normalized);
    assert.equal(cells.length, 3);
    assert.match(cells[0], /count|entries/i);
    assert.match(cells[1], /not from|door state alone|open.*closed/i);
    assert.match(cells[2], /count|entr(?:ies|y)|people/i);
    assert.match(await page.locator('#claim-badge').innerText(), /PRODUCT VISION/);
    const columnHeading = await page.locator('#comparison-stage thead th').last().innerText();
    assert.match(columnHeading, /The Threshold vision/i);
    await capture(page, 'desktop-contact-comparison');
    return { entryCountRow: cells, columnHeading, distinction: 'Clearly framed intended product capability, not verified physical counting' };
  });
  await test('illustrated walk-through emits a red SIMULATED DETECTION cue and clears on navigation', async () => {
    await go(page, idx['the-question']);
    const start = await state(page);
    if (!start.diagnostics?.cue) throw new Error(`Engine cue diagnostics unavailable on ${start.renderer}; no cue proof`);
    await page.waitForFunction(() => document.querySelector('#detection-state').textContent === 'SIMULATED DETECTION' &&
      document.body.classList.contains('illustrated-alert'), null, { timeout: 6500 });
    await page.waitForFunction(() => {
      const color = getComputedStyle(document.querySelector('#detection-cue')).color.match(/[\d.]+/g).map(Number);
      return color[0] > 180 && color[0] > color[1] * 1.3 && color[0] > color[2] * 1.3;
    }, null, { timeout: 1200 });
    const cue = await state(page);
    assert.equal(cue.chapter, idx['the-question'], 'Animated scene must not advance the story index');
    assert.equal(cue.diagnostics.cue.active, true);
    assert.match(await page.locator('#detection-cue').innerText(), /ILLUSTRATION.*NOT LIVE SENSOR DATA/);
    const color = await page.locator('#detection-cue').evaluate(el => getComputedStyle(el).color);
    await capture(page, 'desktop-simulated-detection');
    await go(page, idx['the-signal']);
    assert(await page.locator('#detection-cue').isHidden());
    assert.equal(await page.locator('body').evaluate(body => body.classList.contains('illustrated-alert')), false);
    return { cue, color, clearsOnNavigation: true, hardwareMeaning: 'None; presentation-only illustration' };
  });
  await test('native image transition never outruns visible TEST and Simulator provenance', async () => {
    await go(page, idx['the-signal']);
    const samples = await page.evaluate(async productIndex => {
      const opacity = selector => {
        const node = document.querySelector(selector); let value = 1;
        for (let element = node; element; element = element.parentElement) {
          const css = getComputedStyle(element);
          if (element.hidden || css.display === 'none' || css.visibility === 'hidden') return 0;
          value *= Number(css.opacity);
        }
        return value;
      };
      const results = []; const start = performance.now();
      window.thresholdPresentation.goTo(productIndex);
      // Bounded temporal regression. Sample the actual live UI; do not alter animation time.
      for (let i = 0; i < 44; i++) {
        results.push({ elapsedMs: performance.now() - start, imageOpacity: opacity('#product-stage img'),
          badgeOpacity: opacity('#claim-badge'), captionOpacity: opacity('#visual-caption'),
          badge: document.querySelector('#claim-badge').textContent,
          caption: document.querySelector('#visual-caption').textContent });
        if (i < 43) await new Promise(resolve => setTimeout(resolve, 40));
      }
      return results;
    }, idx['built-software']);
    const visibleImage = samples.filter(sample => sample.imageOpacity > 0);
    assert(visibleImage.length > 0, 'Product image never appeared during the bounded transition');
    assert(visibleImage.every(sample => sample.badgeOpacity >= 0.95 && sample.captionOpacity >= 0.95 &&
      /TEST/.test(sample.badge) && /simulator/i.test(sample.caption) && /synthetic/i.test(sample.caption)),
    `Native imagery appeared before its provenance: ${JSON.stringify(samples)}`);
    await settle(page);
    return { observationCount: samples.length, samples, implication: 'Any visible image requires opaque, accurate provenance' };
  });
  await test('native archived screenshot has visible Simulator and synthetic provenance', async () => {
    await go(page, idx['built-software']); await capture(page, 'desktop-native');
    const provenance = JSON.parse(readFileSync(join(deckRoot, 'assets/PROVENANCE.json'), 'utf8'));
    const expectedHash = provenance['native-test.png'].sha256;
    assert.equal(createHash('sha256').update(readFileSync(join(deckRoot, 'assets/native-test.png'))).digest('hex'), expectedHash);
    const image = await page.locator('#product-stage img').evaluate(img => ({ complete: img.complete, width: img.naturalWidth, src: img.getAttribute('src') }));
    assert(image.complete && image.width > 0); assert.equal(image.src, 'assets/native-test.png');
    const visible = [await page.locator('#product-stage').innerText(), await page.locator('#visual-caption').innerText(),
      await page.locator('#claim-badge').innerText()].join(' ');
    assert.match(visible, /simulator/i, 'Visible caption must identify Simulator, not imply a real iPhone capture');
    assert.match(visible, /archiv|recorded|capture/i); assert.match(visible, /synthetic/i); assert.match(visible, /TEST/);
    return { visible, image, sha256: expectedHash };
  });
  await test('fall sensing is visibly framed as product vision, not a built detector', async () => {
    await go(page, idx['fall-research']); await capture(page, 'desktop-future');
    assert.equal((await state(page)).kind, 'future');
    assert(await page.locator('#claim-badge').isVisible());
    assert.match(await page.locator('#claim-badge').innerText(), /PRODUCT VISION/);
    return { framing: await page.locator('#claim-badge').innerText(), kind: (await state(page)).kind };
  });
  await test('empty handoff is honest fallback, never an app or source-mode switch', async () => {
    await go(page, idx['console-handoff']); await capture(page, 'desktop-handoff');
    assert.equal((await state(page)).consoleConfigured, false);
    assert(await page.locator('#open-console').isHidden()); assert.equal(await page.locator('#open-console').getAttribute('href'), null);
    assert.match(await page.locator('#console-detail').innerText(), /no console selected/i);
    assert.match(await page.locator('#demo-title').innerText(), /not the animation/i);
    const controls = await page.locator('button, select, input, a[href]').evaluateAll(elements => elements.map(el => ({
      tag: el.tagName, id: el.id, name: el.getAttribute('aria-label') || el.textContent.trim(), href: el.getAttribute('href') })));
    assert(!controls.some(c => /^(?:calibrate|arm|disarm|acknowledge|trigger|source\s*(?:mode|switch)|switch\s*source)\b/i.test(c.name)),
      'Deck must not impersonate detector controls');
    assert.equal(await page.locator('iframe, embed, object').count(), 0);
    assert.deepEqual(await page.locator('select').evaluateAll(els => els.map(el => el.id).sort()), ['motion-select', 'quality-select']);
    await page.locator('#recorded-demo').click();
    assert.match(await page.locator('.recording-disclosure').innerText(), /archived.*synthetic TEST.*not a live console/i);
    await page.keyboard.press('Escape'); return { controls, consoleOpened: false };
  });
  await test('settings typing does not navigate; unsafe URL submissions are rejected', async () => {
    await go(page, idx['the-question']); await page.locator('#settings-toggle').click();
    await page.locator('#console-url').fill('http://127.0.0.1:8894/');
    for (const key of ['End', 'Space', 'ArrowLeft', 'Home']) await page.locator('#console-url').press(key);
    assert.equal((await state(page)).chapter, idx['the-question']);
    for (const fixture of rejectedFixtures) {
      await page.locator('#console-url').fill(fixture);
      await page.locator('#console-form button[type=submit]').click();
      assert(await page.locator('#settings-dialog').evaluate(dialog => dialog.open), `Unsafe URL was accepted: ${fixture}`);
      assert.equal((await state(page)).consoleConfigured, false, `Unsafe URL configured: ${fixture}`);
      assert.match(await page.locator('#console-error').innerText(), /local|credentials|query|fragment/i);
    }
    await page.locator('#console-url').fill(''); await page.locator('#console-form button[type=submit]').click();
    assert.equal((await state(page)).consoleConfigured, false);
    assert.equal(await page.locator('#settings-dialog').evaluate(dialog => dialog.open), false);
    return { invalidFixturesTested: rejectedFixtures, noURLFollowed: true };
  });
  await test('desktop keyboard focus, modal trap and one-step button activation', () => focusChecks(page));
  await test('separate presenter window forwards BroadcastChannel navigation and notes', async () => {
    await go(page, 2);
    const [presenter] = await Promise.all([
      page.waitForEvent('popup', { timeout: 5500 }), page.locator('#presenter-toggle').click(),
    ]);
    currentPage = presenter;
    try {
      await presenter.waitForFunction(() => window.thresholdPresentation?.getState().audience === false);
      await presenter.waitForFunction(() => window.thresholdPresentation.getState().chapter === 2);
      assert.match(await presenter.locator('#presenter-script').innerText(), /SAY:/);
      assert.equal((await state(presenter)).renderer, 'Presenter controls · no GPU');
      await presenter.locator('#presenter-next-button').click();
      await page.waitForFunction(() => window.thresholdPresentation.getState().chapter === 3);
      await go(page, idx['fall-research']);
      await presenter.waitForFunction(index => window.thresholdPresentation.getState().chapter === index, idx['fall-research']);
      assert.match(await presenter.locator('#presenter-script').innerText(), /fall|vision|research/i);
      await capture(presenter, 'presenter-private-notes');
      await keyboardTarget(presenter); await presenter.keyboard.press('Home');
      await page.waitForFunction(() => window.thresholdPresentation.getState().chapter === 0);
      assert(await presenter.locator('#presenter-prev').isDisabled(), 'Presenter previous must disable at opening');
      await presenter.keyboard.press('End'); await page.waitForFunction(index => window.thresholdPresentation.getState().chapter === index, finalIndex);
      assert(await presenter.locator('#presenter-next-button').isDisabled(), 'Presenter next must disable at end');
      return { separateWindow: true, presenterToAudience: true, audienceToPresenter: true, notesVisible: true, gpuInPresenter: false };
    } finally { await presenter.close(); currentPage = page; }
  });
  await test('prefetched story works with browser network disabled', async () => {
    const assets = [...allowedPaths].filter(path => path !== '/__version' && !path.endsWith('/index.html') && path !== '/');
    const prefetch = await page.evaluate(async paths => {
      const results = [];
      for (const path of paths) {
        const response = await fetch(path); if (!response.ok) throw new Error(`Asset prefetch failed: ${path} ${response.status}`);
        const bytes = (await response.arrayBuffer()).byteLength; results.push({ path, bytes });
      }
      await document.fonts.ready;
      await Promise.all([...document.images].map(image => image.decode()));
      return results;
    }, assets);
    await desktop.setOffline(true);
    try {
      for (let i = 0; i < chapters.length; i++) { await go(page, i); await checkTitle(page, chapters[i]); }
      assert.equal((await state(page)).chapter, finalIndex); await capture(page, 'desktop-offline-close');
    } finally { await desktop.setOffline(false); }
    return { prefetched: prefetch, allChapters: chapters.length, boundary: 'After all assets loaded; not a cold offline reload claim' };
  });
  report.renderers.push({ viewport: page.viewportSize(), state: await state(page), phase: 'desktop-final' });
  await desktop.close(); contexts.delete(desktop); currentPage = null;
  const configContext = await makeContext({ reducedMotion: 'reduce' });
  const configPage = await configContext.newPage(); currentPage = configPage;
  await configPage.goto(new URL('?presenter=1&session=qa-storage-only', base).href, { waitUntil: 'load' });
  await configPage.waitForFunction(() => !!window.thresholdPresentation);
  await test('restored unsafe console configuration is rejected before exposing a link', async () => {
    const results = [];
    for (const fixture of rejectedFixtures) {
      await configPage.evaluate(value => sessionStorage.setItem('threshold-console', value), fixture);
      await configPage.reload({ waitUntil: 'load' });
      await configPage.waitForFunction(() => !!window.thresholdPresentation);
      const saved = await state(configPage);
      const href = await configPage.locator('#open-console').getAttribute('href');
      results.push({ fixture, configured: saved.consoleConfigured, href });
    }
    assert(results.every(result => !result.configured && !result.href), JSON.stringify(results));
    return results;
  });
  await configContext.close(); contexts.delete(configContext); currentPage = null;
  const narrow = await makeContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
  const mobile = await openAudience(narrow); currentPage = mobile;
  await test('reduced motion settles final title without flying glyphs or repeated rendering', async () => {
    assert.equal((await state(mobile)).reducedMotion, true);
    await go(mobile, 1); await checkTitle(mobile, chapters[1]);
    assert.equal(await mobile.locator('.flying-letter').count(), 0);
    const before = await state(mobile); await sleepForObservation(mobile, 800); const after = await state(mobile);
    assert.equal(await mobile.locator('.flying-letter').count(), 0);
    assert.equal(await mobile.locator('#chapter-title').evaluate(el => getComputedStyle(el).opacity), '1');
    if (before.diagnostics?.frame && after.diagnostics?.frame) {
      assert(after.diagnostics.frame.count - before.diagnostics.frame.count <= 2, 'Reduced-motion scene continues rendering while idle');
    }
    await go(mobile, idx['the-question']);
    await mobile.waitForFunction(() => document.querySelector('#detection-state').textContent === 'SIMULATED DETECTION', null, { timeout: 1500 });
    const finalPose = await state(mobile);
    assert.equal(finalPose.diagnostics?.cue?.active, true, 'Reduced-motion walk-through must show its deterministic final illustrative pose');
    assert.equal(finalPose.transition?.active, false);
    await capture(mobile, 'narrow-reduced-simulated-pose');
    return { before, after, finalPose, noGPUProofFromFallback: true };
  });
  await test('390x844 all chapters: bounded readable text and complete story', async () => {
    const layouts = [];
    for (let i = 0; i < chapters.length; i++) {
      await go(mobile, i); await checkTitle(mobile, chapters[i]);
      const layout = await bounds(mobile); layouts.push({ chapter: i, ...layout });
      if (['emancipator', 'sensor-comparison', 'built-software', 'console-handoff', 'fall-research'].includes(chapters[i].id)) await capture(mobile, `narrow-${chapters[i].id}`);
    }
    writeFileSync(join(output, 'narrow-layout.json'), JSON.stringify(layouts, null, 2) + '\n');
    assert(layouts.every(item => item.scrollWidth <= 391 && item.scrollHeight <= 845 && item.violations.length === 0),
      JSON.stringify(layouts.filter(item => item.violations.length || item.scrollWidth > 391 || item.scrollHeight > 845)));
    return layouts;
  });
  await test('390x844 keyboard focus and settings modal behavior', () => focusChecks(mobile));
  report.renderers.push({ viewport: mobile.viewportSize(), state: await state(mobile), phase: 'narrow-final' });
  await narrow.close(); contexts.delete(narrow); currentPage = null;
  await test('no page errors, crashes, unsafe networks or local asset failures', async () => {
    assert.deepEqual(report.pageErrors, [], 'Unhandled browser page error');
    assert.deepEqual(report.network.blocked, [], 'Page attempted network outside static allowlist');
    assert.deepEqual(report.network.responses, [], 'Static asset returned HTTP error');
    assert(!report.harnessErrors.some(error => error.name === 'PageCrash'), 'Chromium crash');
    // Offline watcher failures are expected. Other failures must be reviewed rather than called PASS.
    const unexpected = report.network.failures.filter(item => !item.url.endsWith('/__version'));
    assert.deepEqual(unexpected, [], 'Unexpected asset/network load failure');
    const renderErrors = report.consoleErrors.filter(error => /(?:Invalid Texture|Validation Error|GPUValidationError|shader.*error|uncaptured.*error)/i.test(error.text));
    assert.deepEqual(renderErrors, [], 'GPU validation error (reported separately from backend type)');
    return { pageErrors: 0, blockedRequests: 0, rendererValidationErrors: 0,
      expectedOfflineWatcherFailures: report.network.failures.length, consoleErrorsForReview: report.consoleErrors };
  });
}
let overallTimer;
try {
  await Promise.race([runSuite(), new Promise((_, reject) => {
    overallTimer = setTimeout(() => { cancelled = true; reject(new Error('Overall 115-second QA deadline exceeded')); }, report.limits.overallMs);
  })]);
} catch (error) {
  cancelled = true; report.harnessErrors.push(errorData(error));
  console.error(`UNKNOWN: harness: ${error.message}`);
} finally {
  clearTimeout(overallTimer); cancelled = true;
  let closeTimer;
  try {
    if (browser) await Promise.race([browser.close(), new Promise((_, reject) => {
      closeTimer = setTimeout(() => reject(new Error('Browser cleanup did not complete in 5 seconds')), 5000);
    })]);
    report.cleanup = { status: 'CLOSED', browserCloseResolved: true, note: 'Only this test-owned Chromium was closed. The supplied static server was not touched.' };
  } catch (error) { report.cleanup = { status: 'UNKNOWN', ...errorData(error) }; report.harnessErrors.push(errorData(error)); }
  clearTimeout(closeTimer);
  try {
    report.source.after = hashes();
    const names = new Set([...Object.keys(report.source.before), ...Object.keys(report.source.after)]);
    report.source.changed = [...names].filter(name => report.source.before[name] !== report.source.after[name]);
    report.source.stable = report.source.changed.length === 0;
  } catch (error) { report.source.stable = false; report.source.hashError = errorData(error); report.harnessErrors.push(errorData(error)); }
  report.completedAt = new Date().toISOString(); report.durationMs = Date.now() - started;
  report.counts = Object.fromEntries(['PASS', 'FAIL', 'UNKNOWN', 'RUNNING'].map(status => [status, report.tests.filter(test => test.status === status).length]));
  report.status = report.harnessErrors.length || report.counts.UNKNOWN || report.counts.RUNNING ? 'UNKNOWN' : report.counts.FAIL ? 'FAIL' : 'PASS';
  if (!report.source.stable) { report.provisional = true; report.status = report.status === 'PASS' ? 'PROVISIONAL' : report.status + '_PROVISIONAL'; }
  persist();
  writeFileSync(join(output, 'summary.txt'), [
    `THRESHOLD presentation QA: ${report.status}`, `Output: ${output}`, `Duration: ${report.durationMs} ms`,
    `Tests: ${JSON.stringify(report.counts)}`, `Source stable: ${report.source.stable}`, `Source drift: ${JSON.stringify(report.source.changed || [])}`,
    `Render backends: ${report.renderers.map(item => item.state.renderer).join(', ')}`,
    ...report.tests.filter(item => item.status !== 'PASS').map(item => `${item.status}: ${item.name}: ${item.error?.message || 'incomplete'}`),
    'No detector API, console navigation, actual app configuration, alarms, radios, or calls were operated.',
    'A fallback pass is not proof of WebGPU. Full shader/performance verification is separate.',
  ].join('\n') + '\n');
  console.log(`THRESHOLD_QA_RESULT=${report.status}`);
  console.log(`THRESHOLD_QA_REPORT=${join(output, 'report.json')}`);
  process.exitCode = report.status === 'PASS' ? 0 : report.status.startsWith('FAIL') ? 1 : 2;
}

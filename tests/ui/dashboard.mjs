/** Local browser gate. Uses an already-installed Playwright package; never installs.
 * Run from the repo root after the lead hands over the running TEST server:
 * PLAYWRIGHT_MODULE=/absolute/path/to/playwright node tests/ui/dashboard.mjs
 * Optional THRESHOLD_URL, THRESHOLD_TOKEN_FILE. No token, cookie, or request body is logged.
 */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const url = process.env.THRESHOLD_URL || "http://127.0.0.1:8765";
assert.equal(new URL(url).hostname, "127.0.0.1", "This gate is restricted to a loopback TEST server.");
const output = path.join(root, "evidence/ui");
await fs.mkdir(output, { recursive: true });
const checks = [];
const check = (name, detail = "PASS") => { checks.push({ name, detail }); console.log(`PASS ${name}: ${detail}`); };
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 1000 }, reducedMotion: "reduce" });
const page = await context.newPage();
const runtimeErrors = [];
page.on("pageerror", error => runtimeErrors.push(error.message));
await context.route("**/*", route => {
  if (new URL(route.request().url()).origin !== new URL(url).origin) return route.abort("blockedbyclient");
  return route.continue();
});
await page.addInitScript(() => {
  window.__audioEvidence = { started: 0, active: 0 };
  const Audio = window.AudioContext || window.webkitAudioContext;
  if (!Audio) return;
  const create = Audio.prototype.createOscillator;
  Audio.prototype.createOscillator = function (...args) {
    const oscillator = create.apply(this, args);
    const start = oscillator.start.bind(oscillator);
    oscillator.start = (...values) => { window.__audioEvidence.started += 1; window.__audioEvidence.active += 1; return start(...values); };
    oscillator.addEventListener("ended", () => { window.__audioEvidence.active -= 1; });
    return oscillator;
  };
});
const waitState = (wanted) => page.waitForFunction(value => document.querySelector("#alarm-state").textContent === value, wanted, { timeout: 15000 });
const readState = async () => {
  const response = await context.request.get(url + "/api/state");
  assert.equal(response.status(), 200);
  return response.json();
};
const screenshot = async (name) => page.screenshot({ path: path.join(output, name), fullPage: true, animations: "disabled" });
try {
  await page.goto(url);
  await page.locator("#pair-form").waitFor({ state: "visible" });
  assert.equal(await page.locator("#dashboard").isVisible(), false);
  assert.equal(await page.locator("#header-tools").isVisible(), false);
  check("unauthenticated shell", "No detector or sound controls visible");
  await screenshot("pairing-desktop.png");
  let token = (await fs.readFile(process.env.THRESHOLD_TOKEN_FILE || path.join(root, ".local/control-token"), "utf8")).trim();
  await page.locator("#token").fill(token);
  token = "";
  await page.locator("#pair-submit").click();
  await page.locator("#dashboard").waitFor({ state: "visible", timeout: 10000 });
  assert.equal(await page.locator("#token").inputValue(), "");
  assert.equal(await page.locator("#source-mode").textContent(), "TEST");
  const cookies = await context.cookies(url);
  assert(cookies.some(cookie => cookie.httpOnly && cookie.sameSite === "Strict"));
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  check("pairing", "Token input cleared; HttpOnly SameSite=Strict cookie; no browser storage");
  let state = await readState();
  assert.equal(state.source_mode, "TEST", "Never mutate LIVE or REPLAY in this gate");
  assert.equal(state.calls.enabled, false, "No real call policy may be enabled for this gate");
  if (["ARMED", "ALARM", "CALIBRATING"].includes(state.alarm_state)) {
    await page.locator("#disarm").click();
    await waitState("DISARMED");
  }
  await page.waitForFunction(() => document.querySelector("#connection-strip").dataset.health === "healthy");
  await page.locator(".tuning summary").click();
  await page.locator("#threshold-input").fill(state.features.threshold === 3 ? "3.1" : "3");
  await page.locator("#threshold-save").click();
  await page.waitForFunction(() => document.querySelector("#action-message").textContent.startsWith("Threshold changed"));
  assert.equal(await page.locator("#arm").isDisabled(), true);
  await page.locator(".tuning summary").click();
  assert.equal(await page.evaluate(() => window.__audioEvidence.started), 0);
  await page.locator("#sound-test").click();
  await page.waitForFunction(() => window.__audioEvidence.started >= 2 && window.__audioEvidence.active === 0);
  const baselineToneCount = await page.evaluate(() => window.__audioEvidence.started);
  check("explicit sound test", "WebAudio nodes started only after a user gesture; no detector event");
  await page.locator("#calibrate").click();
  await waitState("CALIBRATING");
  assert.equal(await page.locator("#arm").isDisabled(), true);
  await page.waitForFunction(() => document.querySelector("#calibration-progress").value > 0);
  await waitState("READY");
  assert.equal(await page.locator("#arm").isDisabled(), false);
  assert.equal(await page.evaluate(() => window.__audioEvidence.started), baselineToneCount);
  assert.equal(await page.locator("#calibration-progress").getAttribute("max"), "1");
  check("calibration and quiet TEST baseline", "Progress reached READY; arm gated until ready; baseline emitted no alarm tones");
  await screenshot("desktop-ready.png");
  await page.locator("#arm").click();
  await waitState("ARMED");
  await page.locator("#test-motion").click();
  await waitState("ALARM");
  await page.waitForFunction(count => window.__audioEvidence.started >= count + 4, baselineToneCount, { timeout: 7000 });
  assert.equal(await page.locator("#alarm-actions").isVisible(), true);
  assert((await page.locator("#events .event").count()) > 0);
  check("armed synthetic alarm", "TEST injection latched event and repeated bounded WebAudio tone pairs");
  await screenshot("desktop-alarm.png");

  await page.route("**/api/state", route => route.abort("failed"));
  await page.waitForFunction(() => document.querySelector("#health-status").textContent === "Connection lost", null, { timeout: 6000 });
  assert.equal(await page.locator("#connection-strip").getAttribute("data-health"), "stale");
  assert.equal(await page.locator("#alarm-state").textContent(), "ALARM");
  assert.equal(await page.locator("#arm").isDisabled(), true);
  await screenshot("desktop-stale-alarm.png");
  check("API failure while alarm latched", "Health changed to stale; ALARM retained independently; arm blocked");
  await page.unroute("**/api/state");
  await page.waitForFunction(() => document.querySelector("#connection-strip").dataset.health === "healthy");
  await page.locator("#acknowledge").click();
  await waitState("ARMED");
  await page.waitForFunction(() => window.__audioEvidence.active === 0);
  state = await readState();
  assert.equal(state.events[0].acknowledged, true);
  check("acknowledgement", "Server returned ARMED, newest event acknowledged, local tones stopped");
  await page.locator("#disarm").click();
  await waitState("DISARMED");
  await page.waitForFunction(() => window.__audioEvidence.active === 0);
  check("disarm", "DISARMED confirmed and sound stopped");

  let statePolls = 0;
  const countPoll = request => { if (new URL(request.url()).pathname === "/api/state") statePolls += 1; };
  page.on("request", countPoll);
  // A bounded measurement interval, not an agent polling loop.
  await page.waitForTimeout(2100);
  assert(statePolls <= 5, `State polls exceeded 2 Hz target: ${statePolls} in 2.1 seconds`);
  const visiblePolls = statePolls;
  await page.evaluate(() => { Object.defineProperty(document, "hidden", { value: true, configurable: true }); document.dispatchEvent(new Event("visibilitychange")); });
  await page.waitForTimeout(1100);
  assert.equal(statePolls, visiblePolls);
  assert.equal(await page.locator("#arm").isDisabled(), true);
  assert.equal(await page.locator("#health-status").textContent(), "Updates paused");
  await page.evaluate(() => { delete document.hidden; document.dispatchEvent(new Event("visibilitychange")); });
  await page.waitForFunction(() => document.querySelector("#connection-strip").dataset.health === "healthy");
  page.off("request", countPoll);
  check("bounded polling and visibility handler", `${visiblePolls} requests in 2.1s; simulated hidden-tab event paused requests and blocked arm`);

  await page.route("**/api/state", () => {}); // Intentionally stalled response exercises AbortController.
  await page.waitForFunction(() => document.querySelector("#health-detail").textContent.includes("timed out"), null, { timeout: 6000 });
  assert.equal(await page.locator("#arm").isDisabled(), true);
  check("request timeout", "A stalled API request became stale and blocked arm");
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.waitForFunction(() => document.querySelector("#connection-strip").dataset.health === "healthy");

  for (const width of [390, 320, 1440]) {
    await page.setViewportSize({ width, height: width < 500 ? 844 : 1000 });
    const layout = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
    assert(layout.document <= width && layout.body <= width, `Horizontal overflow at ${width}px`);
    const tinyTargets = await page.locator("button:visible, input:visible, summary:visible").evaluateAll(elements => elements.filter(el => { const r = el.getBoundingClientRect(); return r.width < 44 || r.height < 44; }).map(el => el.id || el.tagName));
    assert.deepEqual(tinyTargets, [], `Targets under 44px at ${width}px`);
    await screenshot(`dashboard-${width}.png`);
    check(`layout ${width}px`, "No horizontal overflow; visible controls at least 44px in both dimensions");
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("#logout").focus();
  await page.keyboard.press("Shift+Tab");
  const focus = await page.locator("#sound-toggle").evaluate(el => ({ active: document.activeElement === el, outline: getComputedStyle(el).outlineStyle }));
  assert.equal(focus.active, true);
  assert.notEqual(focus.outline, "none");
  check("keyboard focus", "Visible outline on focused sound control");

  // Fixture-only rendering checks: never posted to the detector and labelled in evidence.
  const fixture = await readState();
  fixture.source_mode = "REPLAY";
  fixture.area_name = "<img src=x onerror=alert(1)>";
  fixture.graph = Array.from({ length: 1500 }, (_, i) => ({ timestamp: new Date(Date.now() - (1500 - i) * 30).toISOString(), activity_score: 1 + Math.sin(i) }));
  fixture.events = Array.from({ length: 125 }, (_, i) => ({ event_id: `fixture-${i}`, session_id: fixture.session_id, occurred_at: new Date().toISOString(), source_mode: "REPLAY", type: "motion_near_entrance", area_name: "<script>window.bad=true</script>", activity_score: 4.5, threshold: 3, acknowledged: true, call_status: "blocked_mode", call_detail: "<img src=x onerror=alert(1)>" }));
  await page.route("**/api/state", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fixture) }));
  await page.waitForFunction(() => document.querySelector("#source-mode").textContent === "REPLAY");
  assert.equal(await page.locator("#test-tools").isVisible(), false);
  assert.equal(await page.locator("#events .event").count(), 100);
  assert.equal(await page.locator("#events img, #events script, #area-name img").count(), 0);
  assert((await page.locator("#events").textContent()).includes("<img src=x onerror=alert(1)>"));
  const points = (await page.locator("#graph-line").getAttribute("d")).match(/[ML]/g)?.length || 0;
  assert(points <= 180);
  check("fixture-only safe rendering", "REPLAY hides synthetic control; 125 events bounded to 100; 1500 points bounded to 180; HTML treated as text");
  fixture.source_mode = "LIVE";
  await page.waitForFunction(() => document.querySelector("#source-mode").textContent === "LIVE");
  assert.equal(await page.locator("#test-tools").isVisible(), false);
  check("fixture-only LIVE mode", "Synthetic controls hidden; source remains explicit");
  await page.unroute("**/api/state");
  await page.waitForFunction(() => document.querySelector("#source-mode").textContent === "TEST");
  await page.locator("#logout").click();
  await page.locator("#pairing").waitFor({ state: "visible" });
  assert.equal(await page.locator("#dashboard").isVisible(), false);
  assert.equal(await page.locator("#token").inputValue(), "");
  assert.equal((await context.request.get(url + "/api/state")).status(), 401);
  check("unpair", "Server rejects former cookie; private controls hidden; token input empty");
  assert.deepEqual(runtimeErrors, []);
  check("browser JavaScript", "No uncaught runtime errors");
  const result = { status: "PASS", source: "TEST synthetic server; LIVE/REPLAY rendering checks are intercepted fixtures only", checked_at: new Date().toISOString(), checks, screenshots: (await fs.readdir(output)).filter(name => name.endsWith(".png")) };
  await fs.writeFile(path.join(output, "browser-results.json"), JSON.stringify(result, null, 2) + "\n");
} catch (error) {
  // No request bodies, cookies, token values, or traces are printed/saved.
  // Playwright action error text can echo fill() values. Never persist it:
  // a failed token-entry action must not expose the local control secret.
  const result = { status: "FAIL", checked_at: new Date().toISOString(), checks,
    error: error?.name || "BrowserCheckError", last_pass: checks.at(-1)?.name || "none",
    runtime_error_count: runtimeErrors.length };
  await fs.writeFile(path.join(output, "browser-results.json"), JSON.stringify(result, null, 2) + "\n");
  console.error("FAIL", result.error, "after", result.last_pass);
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}

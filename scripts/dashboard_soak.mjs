/** Bounded synthetic dashboard soak. No server start, installs, serial, or real calls.
 * PLAYWRIGHT_MODULE=/absolute/installed/playwright node scripts/dashboard_soak.mjs
 * Lead must grant exclusive control of the existing loopback TEST server first.
 */
import fs from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
delete process.env.DEBUG;
delete process.env.PWDEBUG;
const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const url = process.env.THRESHOLD_URL || "http://127.0.0.1:8765";
const origin = new URL(url).origin;
const cycles = Number(process.env.THRESHOLD_SOAK_CYCLES || 12);
assert.equal(new URL(url).hostname, "127.0.0.1");
assert(Number.isInteger(cycles) && cycles >= 10 && cycles <= 20);
const began = Date.now();
const deadline = began + 8 * 60 * 1000;
const limit = (preferred = 5000) => {
  if (Date.now() >= deadline) { const error = new Error(); error.name = "SoakDeadline"; throw error; }
  return Math.max(1, Math.min(preferred, deadline - Date.now()));
};
const output = path.join(root, "evidence/simulation");
await fs.mkdir(output, { recursive: true });
const report = { status: "RUNNING", source_mode: "TEST", synthetic_only: true,
  began_at: new Date(began).toISOString(), requested_cycles: cycles, viewport: { width: 390, height: 844 },
  scope: "Browser control and software-state soak only. Not RF, physical motion, or mobile background-audio evidence.",
  cycles: [], client_faults: [], checks: [], cleanup: { disarmed_confirmed: false, unpaired_confirmed: false, browser_closed: false } };
let browser = null, context = null, page = null, expectedSession = null;
let phase = "launch";
let runtimeErrorCount = 0;
let sessionPaired = false;
const polls = [];
const bootstrapPolls = [];
let measurePolling = false;
const pendingPolls = new Set();
let maxPendingPolls = 0, maxDomEvents = 0, maxGraphPoints = 0, forbiddenNetwork = 0;
const check = name => { report.checks.push(name); console.log("PASS", name); };
const safeState = async (cleanup = false) => {
  const response = await context.request.get(origin + "/api/state", { timeout: cleanup ? 5000 : limit() });
  assert.equal(response.status(), 200);
  const data = await response.json();
  // This guard runs immediately before EVERY detector mutation, including cleanup.
  assert.equal(data.source_mode, "TEST");
  assert.equal(data.calls.enabled, false);
  if (expectedSession !== null) assert.equal(data.session_id, expectedSession);
  return data;
};
const waitState = state => page.waitForFunction(value => document.querySelector("#alarm-state").textContent === value, state, { timeout: limit(13000) });
const clickControl = async id => {
  await safeState();
  await page.locator(id).click({ timeout: limit() });
};
const audio = () => page.evaluate(() => ({ ...window.__soakAudio }));
const waitQuiet = async milliseconds => {
  await page.evaluate(() => { window.__soakQuietSince = null; });
  await page.waitForFunction(duration => {
    const score = Number(document.querySelector("#activity-score").textContent.replace(/,/g, ""));
    const threshold = Number(document.querySelector("#threshold-value").textContent.replace(/,/g, ""));
    const quiet = Number.isFinite(score) && score < threshold * 0.6 && document.querySelector("#connection-strip").dataset.health === "healthy";
    if (!quiet) window.__soakQuietSince = null;
    else if (window.__soakQuietSince === null) window.__soakQuietSince = Date.now();
    return quiet && Date.now() - window.__soakQuietSince >= duration;
  }, milliseconds, { timeout: limit(12000) });
};
const capture = name => page.screenshot({ path: path.join(output, "ui-soak-" + name + ".png"), fullPage: true, animations: "disabled", timeout: limit() });
const newEvents = (state, before) => state.events.filter(event => !before.has(event.event_id));
try {
  const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
  browser = await chromium.launch({ headless: true, timeout: limit(15000) });
  context = await browser.newContext({ viewport: report.viewport, reducedMotion: "reduce" });
  await context.route("**/*", route => {
    if (new URL(route.request().url()).origin !== origin) { forbiddenNetwork += 1; return route.abort("blockedbyclient"); }
    return route.continue();
  });
  page = await context.newPage();
  page.setDefaultTimeout(5000);
  page.on("pageerror", () => { runtimeErrorCount += 1; });
  page.on("request", request => {
    if (new URL(request.url()).pathname === "/api/state" && request.method() === "GET") {
      if (measurePolling) { polls.push(Date.now()); pendingPolls.add(request); maxPendingPolls = Math.max(maxPendingPolls, pendingPolls.size); }
      else bootstrapPolls.push(Date.now());
    }
  });
  const finishPoll = request => pendingPolls.delete(request);
  page.on("requestfinished", finishPoll);
  page.on("requestfailed", finishPoll);
  await page.addInitScript(() => {
    window.__soakAudio = { started: 0, active: 0, peak_active: 0 };
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return;
    const create = Audio.prototype.createOscillator;
    Audio.prototype.createOscillator = function (...args) {
      const oscillator = create.apply(this, args);
      const start = oscillator.start.bind(oscillator);
      oscillator.start = (...values) => {
        window.__soakAudio.started += 1; window.__soakAudio.active += 1;
        window.__soakAudio.peak_active = Math.max(window.__soakAudio.peak_active, window.__soakAudio.active);
        return start(...values);
      };
      oscillator.addEventListener("ended", () => { window.__soakAudio.active -= 1; });
      return oscillator;
    };
  });
  phase = "pair";
  await page.goto(url, { timeout: limit(10000) });
  let token = (await fs.readFile(process.env.THRESHOLD_TOKEN_FILE || path.join(root, ".local/control-token"), "utf8")).trim();
  await page.locator("#token").fill(token, { timeout: limit() });
  token = "";
  await page.locator("#pair-submit").click({ timeout: limit() });
  await page.locator("#dashboard").waitFor({ state: "visible", timeout: limit(10000) });
  sessionPaired = true;
  assert.equal(await page.locator("#token").inputValue(), "");
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  let state = await safeState();
  expectedSession = state.session_id;
  report.initial_events = state.events.length;
  report.threshold = state.features.threshold;
  check("paired without token persistence; TEST and real calls disabled confirmed");
  if (["ARMED", "ALARM", "CALIBRATING"].includes(state.alarm_state)) {
    await clickControl("#disarm"); await waitState("DISARMED");
  }
  await page.waitForFunction(() => document.querySelector("#connection-strip").dataset.health === "healthy", null, { timeout: limit() });
  if (state.features.threshold !== 3) {
    await page.locator(".tuning summary").click({ timeout: limit() });
    await page.locator("#threshold-input").fill("3", { timeout: limit() });
    await clickControl("#threshold-save");
    await page.waitForFunction(() => document.querySelector("#action-message").textContent.startsWith("Threshold changed"), null, { timeout: limit() });
    await page.locator(".tuning summary").click({ timeout: limit() });
    report.threshold = 3;
  }
  await page.locator("#sound-toggle").click({ timeout: limit() });
  await page.waitForFunction(() => document.querySelector("#sound-toggle").getAttribute("aria-pressed") === "true", null, { timeout: limit() });
  assert.equal((await audio()).started, 0);
  measurePolling = true; // Session/bootstrap fetches are not recurring state polls.
  for (let cycle = 1; cycle <= cycles; cycle += 1) {
    const row = { cycle, status: "RUNNING" };
    report.cycles.push(row);
    const cycleStart = Date.now();
    phase = `cycle-${cycle}:calibrate`;
    if (cycle > 1) await waitQuiet(700); // Let the preceding synthetic pulse finish before empty-area calibration.
    state = await safeState();
    assert.equal(state.alarm_state, "DISARMED");
    const before = new Set(state.events.map(event => event.event_id));
    const quietAudioCount = (await audio()).started;
    const calStart = Date.now();
    await clickControl("#calibrate");
    await waitState("CALIBRATING");
    assert.equal(await page.locator("#arm").isDisabled(), true);
    await page.waitForFunction(() => document.querySelector("#calibration-progress").value > 0, null, { timeout: limit() });
    await waitState("READY");
    row.calibration_ms = Date.now() - calStart;
    assert(row.calibration_ms >= 4500);
    assert.equal((await audio()).started, quietAudioCount);
    assert.equal(await page.locator("#arm").isDisabled(), false);
    if (cycle === 1) await capture("390-ready");
    phase = `cycle-${cycle}:first-alarm`;
    await clickControl("#arm"); await waitState("ARMED");
    const motionStart = Date.now();
    await clickControl("#test-motion"); await waitState("ALARM");
    row.first_latch_ms = Date.now() - motionStart;
    state = await safeState();
    const firstNew = newEvents(state, before);
    assert.equal(firstNew.length, 1);
    const firstId = state.active_event_id;
    assert.equal(firstNew[0].event_id, firstId);
    assert.equal(firstNew[0].source_mode, "TEST");
    assert.notEqual(firstNew[0].call_status, "sent");
    await page.waitForFunction(count => window.__soakAudio.started >= count + 4, quietAudioCount, { timeout: limit(7000) });
    state = await safeState();
    assert.equal(newEvents(state, before).length, 1); // Repeating sound must not create duplicate latch events.
    if (cycle === 1) await capture("390-alarm");
    if (cycle === 3 || cycle === 9) {
      phase = `cycle-${cycle}:client-fault`;
      const failedAt = Date.now();
      await page.route("**/api/state", route => route.abort("failed"));
      await page.waitForFunction(() => document.querySelector("#health-status").textContent === "Connection lost", null, { timeout: limit(6000) });
      assert.equal(await page.locator("#alarm-state").textContent(), "ALARM");
      assert.equal(await page.locator("#arm").isDisabled(), true);
      assert.equal(await page.locator("#connection-strip").getAttribute("data-health"), "stale");
      const staleMs = Date.now() - failedAt;
      if (cycle === 3) await capture("390-stale-alarm");
      await page.unroute("**/api/state");
      const recoveryAt = Date.now();
      await page.waitForFunction(() => document.querySelector("#connection-strip").dataset.health === "healthy", null, { timeout: limit(6000) });
      report.client_faults.push({ cycle, stale_ms: staleMs, recovery_ms: Date.now() - recoveryAt, alarm_retained: true });
    }
    phase = `cycle-${cycle}:acknowledge-and-quiet`;
    await clickControl("#acknowledge"); await waitState("ARMED");
    await page.waitForFunction(() => window.__soakAudio.active === 0, null, { timeout: limit() });
    state = await safeState();
    assert.equal(state.events.find(event => event.event_id === firstId).acknowledged, true);
    assert.equal(state.active_event_id, null);
    const afterAckAudio = (await audio()).started;
    const quietStart = Date.now();
    await waitQuiet(2400); // Backend requires 2s quiet; this verifies a sustained displayed quiet interval.
    row.quiet_wait_ms = Date.now() - quietStart;
    state = await safeState();
    assert.equal(state.alarm_state, "ARMED");
    assert.equal(newEvents(state, before).length, 1);
    assert.equal((await audio()).started, afterAckAudio);
    phase = `cycle-${cycle}:quiet-retrigger`;
    const secondMotionStart = Date.now();
    await clickControl("#test-motion"); await waitState("ALARM");
    row.retrigger_ms = Date.now() - secondMotionStart;
    state = await safeState();
    assert.equal(newEvents(state, before).length, 2);
    assert.notEqual(state.active_event_id, firstId);
    assert.equal(state.events[0].source_mode, "TEST");
    assert.notEqual(state.events[0].call_status, "sent");
    const secondId = state.active_event_id;
    await clickControl("#acknowledge"); await waitState("ARMED");
    state = await safeState();
    assert.equal(state.events.find(event => event.event_id === secondId).acknowledged, true);
    await clickControl("#disarm"); await waitState("DISARMED");
    await page.waitForFunction(() => window.__soakAudio.active === 0, null, { timeout: limit() });
    state = await safeState();
    assert.equal(newEvents(state, before).length, 2);
    assert.equal(state.active_event_id, null);
    const bounds = await page.evaluate(() => ({
      events: document.querySelectorAll("#events .event").length,
      points: (document.querySelector("#graph-line").getAttribute("d").match(/[ML]/g) || []).length,
      document_width: document.documentElement.scrollWidth,
      viewport_width: innerWidth,
      small_targets: [...document.querySelectorAll("button,input,summary")].filter(el => el.getClientRects().length).filter(el => { const box = el.getBoundingClientRect(); return box.width < 44 || box.height < 44; }).length,
      dom_nodes: document.querySelectorAll("*").length
    }));
    assert(bounds.events <= 100 && bounds.points <= 180);
    assert(bounds.document_width <= bounds.viewport_width);
    assert.equal(bounds.small_targets, 0);
    maxDomEvents = Math.max(maxDomEvents, bounds.events); maxGraphPoints = Math.max(maxGraphPoints, bounds.points);
    row.events_created = 2; row.event_history_entries = state.events.length;
    row.packet_rate_hz = state.health.packet_rate_hz; row.dom_nodes = bounds.dom_nodes;
    row.audio_nodes_started = (await audio()).started;
    row.duration_ms = Date.now() - cycleStart; row.status = "PASS";
    console.log("PASS synthetic cycle", cycle, "of", cycles, "duration_ms", row.duration_ms);
    await fs.writeFile(path.join(output, "ui-soak-progress.json"), JSON.stringify(report, null, 2) + "\n");
  }
  phase = "final-bounds";
  report.audio = await audio();
  assert(report.audio.peak_active <= 4);
  assert.equal(report.audio.active, 0);
  let maxPollsPerSecond = 0;
  for (let i = 0, left = 0; i < polls.length; i += 1) {
    while (polls[i] - polls[left] >= 1000) left += 1;
    maxPollsPerSecond = Math.max(maxPollsPerSecond, i - left + 1);
  }
  report.bounds = { ui_state_requests: polls.length, bootstrap_state_requests: bootstrapPolls.length, max_ui_state_requests_in_1s: maxPollsPerSecond, max_inflight_ui_state_requests: maxPendingPolls, max_rendered_events: maxDomEvents, max_graph_points: maxGraphPoints, runtime_error_count: runtimeErrorCount, blocked_external_requests: forbiddenNetwork };
  phase = "final-bounds:recurring-poll-rate";
  assert(maxPollsPerSecond <= 3);
  phase = "final-bounds:inflight-polls";
  assert(maxPendingPolls <= 1);
  phase = "final-bounds:runtime";
  assert.equal(runtimeErrorCount, 0);
  phase = "final-bounds:external-network";
  assert.equal(forbiddenNetwork, 0);
  await capture("390-final-disarmed");
  check(`${cycles} complete synthetic cycles; ${cycles * 2} acknowledged events; no duplicate latch events`);
  check("client-only transport loss and recovery retained independent ALARM twice");
  check("390px layout, 44px controls, bounded graph/history/polling/audio, zero runtime errors");
  report.status = "PASS";
} catch (error) {
  report.status = "FAIL";
  report.failure = { phase, error_name: error?.name || "SoakError", runtime_error_count: runtimeErrorCount };
  // NEVER print or persist error.message/stack: Playwright fill failures can echo tokens.
  console.error("FAIL", report.failure.error_name, "phase", phase);
  process.exitCode = 1;
} finally {
  if (context && sessionPaired) {
    try {
      if (page) await page.unrouteAll({ behavior: "ignoreErrors" });
      let final = await safeState(true);
      if (final.alarm_state !== "DISARMED") {
        // The same explicit TEST/no-calls guard immediately precedes this cleanup write.
        const response = await context.request.post(origin + "/api/control", { data: { action: "disarm" }, headers: { Origin: origin }, timeout: 5000 });
        assert.equal(response.status(), 200);
        final = await safeState(true);
      }
      report.cleanup.disarmed_confirmed = final.alarm_state === "DISARMED";
      report.cleanup.final_source_mode = final.source_mode;
      report.cleanup.final_events = final.events.length;
      const logout = await context.request.post(origin + "/api/logout", { headers: { Origin: origin }, timeout: 5000 });
      assert.equal(logout.status(), 200);
      report.cleanup.unpaired_confirmed = (await context.request.get(origin + "/api/state", { timeout: 5000 })).status() === 401;
    } catch (error) {
      report.cleanup.error_name = error?.name || "CleanupError";
    }
  }
  try { if (context) await context.close(); if (browser) await browser.close(); report.cleanup.browser_closed = true; }
  catch (error) { report.cleanup.close_error_name = error?.name || "BrowserCloseError"; }
  if (!report.cleanup.disarmed_confirmed || !report.cleanup.unpaired_confirmed || !report.cleanup.browser_closed) {
    report.status = "FAIL"; process.exitCode = 1;
  }
  report.finished_at = new Date().toISOString();
  report.duration_ms = Date.now() - began;
  await fs.writeFile(path.join(output, "ui-soak-results.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(report.status, "bounded TEST soak", "completed_cycles", report.cycles.filter(row => row.status === "PASS").length,
    "disarmed", report.cleanup.disarmed_confirmed, "unpaired", report.cleanup.unpaired_confirmed, "browser_closed", report.cleanup.browser_closed);
}

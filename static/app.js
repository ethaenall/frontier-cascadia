/* Threshold: local-only UI. No external assets, token storage, or implicit mode fallback. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const POLL_MS = 500; // At most 2 state polls per second; never overlap polls.
  const REQUEST_TIMEOUT_MS = 2200;
  const TRANSPORT_STALE_MS = 3500;
  const MAX_GRAPH_POINTS = 180;
  const MAX_EVENTS = 100;
  const modes = {
    LIVE: "Serial CSI input. This is live data; stream health is shown separately below.",
    REPLAY: "Recorded LIVE CSI. This is a replay, not a live entrance monitor.",
    TEST: "Synthetic CSI-shaped samples. No hardware or physical motion evidence."
  };
  const alarmStates = new Set(["DISARMED", "CALIBRATING", "READY", "ARMED", "ALARM"]);
  const healthStates = new Set(["connecting", "healthy", "stale", "disconnected", "degraded"]);
  let state = null;
  let authenticated = false;
  let busy = false;
  let spatialDraftDirty = false;
  let pairBusy = false;
  let polling = false;
  let pollTimer = null;
  let lastPollStarted = 0;
  let lastResponseAt = 0;
  let transportFault = "";
  let epoch = 0;
  let eventFingerprint = "";
  let previousAnnouncement = "";
  const requests = new Set();
  let audioContext = null;
  let soundEnabled = false;
  let soundTimer = null;
  let mutedEvent = null;
  let soundAttempt = 0;
  const voices = new Set();

  const number = (value) => typeof value === "number" && Number.isFinite(value);
  const fmt = (value, digits = 2) => number(value) ? value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—";
  const text = (id, value) => { const el = $(id); const safe = String(value ?? "—"); if (el.textContent !== safe) el.textContent = safe; };
  const boundedText = (value, length = 700) => typeof value === "string" ? value.slice(0, length) : "";
  const clock = (value, seconds = true) => {
    const d = new Date(value);
    return Number.isFinite(d.getTime()) ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}) }) : "Unknown time";
  };

  class ApiError extends Error {
    constructor(message, status = 0) { super(message); this.status = status; }
  }
  async function api(path, options = {}) {
    const controller = new AbortController();
    requests.add(controller);
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(path, {
        ...options,
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) },
        signal: controller.signal
      });
      let payload;
      try { payload = await response.json(); }
      catch { throw new ApiError("The server returned an unreadable response.", response.status); }
      if (!response.ok) {
        throw new ApiError(boundedText(payload.detail) || `Request failed (${response.status}).`, response.status);
      }
      return payload;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(error.name === "AbortError" ? "The server response timed out." : "The server could not be reached.");
    } finally {
      clearTimeout(timeout);
      requests.delete(controller);
    }
  }

  function validState(data) {
    return data && data.schema_version === 1 && typeof data.session_id === "string" &&
      Object.hasOwn(modes, data.source_mode) && alarmStates.has(data.alarm_state) &&
      typeof data.area_name === "string" && Number.isFinite(Date.parse(data.server_time)) &&
      data.health && healthStates.has(data.health.status) &&
      data.calibration && typeof data.calibration.ready === "boolean" && number(data.calibration.progress) &&
      data.features && number(data.features.threshold) && data.features.threshold > 0 &&
      (data.features.activity_score === null || (number(data.features.activity_score) && data.features.activity_score >= 0)) &&
      Array.isArray(data.graph) && Array.isArray(data.events) && data.calls && data.recording && Array.isArray(data.limitations);
  }

  function announce(message) {
    if (message && message !== previousAnnouncement) {
      text("announcer", message);
      previousAnnouncement = message;
    }
  }
  function clearPoll() { clearTimeout(pollTimer); pollTimer = null; }
  function showPair(message = "Enter your token to open the local console.") {
    epoch += 1;
    authenticated = false;
    state = null;
    transportFault = "";
    busy = false;
    lastResponseAt = 0;
    clearPoll();
    for (const controller of requests) controller.abort();
    disableSound();
    $("dashboard").hidden = true;
    $("header-tools").hidden = true;
    $("local-label").hidden = false;
    $("pairing").hidden = false;
    $("token").value = "";
    text("pair-message", message);
    $("pair-message").classList.remove("error");
    // Clear retained event history and graph data on unpair; all controls are gated.
    $("events").replaceChildren();
    eventFingerprint = "";
    text("area-name", "Entrance");
    text("activity-score", "—");
    $("graph-line").setAttribute("d", "");
    $("graph-area").setAttribute("d", "");
    $("graph-end").setAttribute("visibility", "hidden");
    renderControls();
    window.dispatchEvent(new CustomEvent("threshold-state", { detail: null }));
  }

  function effectiveHealth() {
    if (!state) return { status: "connecting", label: "Connecting", detail: "Waiting for a current server response." };
    if (document.hidden) return { status: "stale", label: "Updates paused", detail: "This tab is hidden. Stream health is not being checked. Monitoring on the server is separate." };
    if (transportFault || performance.now() - lastResponseAt > TRANSPORT_STALE_MS) {
      return { status: "stale", label: "Connection lost", detail: `${transportFault || "No recent server response."} Alarm state is last known, not an all-clear. Arming is blocked.` };
    }
    if (state.health.status === "healthy" && (!number(state.health.sample_age_s) || state.health.sample_age_s > 3)) {
      return { status: "stale", label: "Samples stale", detail: "The API responded, but a fresh input sample is not confirmed. Arming is blocked." };
    }
    const labels = { healthy: "Stream healthy", stale: "Samples stale", disconnected: "Input disconnected", degraded: "Stream degraded", connecting: "Input connecting" };
    return { status: state.health.status, label: labels[state.health.status], detail: boundedText(state.health.detail) || "No stream detail available." };
  }

  function renderHealth() {
    if (!authenticated || !state) return;
    const health = effectiveHealth();
    $("connection-strip").dataset.health = health.status;
    text("health-status", health.label);
    text("health-detail", health.detail);
    text("packet-rate", health.status === "healthy" || (!transportFault && !document.hidden && performance.now() - lastResponseAt <= TRANSPORT_STALE_MS) ? fmt(state.health.packet_rate_hz, 1) : "—");
    text("updated-at", transportFault ? "Last response " + clock(state.server_time) : "Response " + clock(state.server_time));
    renderControls();
    const alarm = state.alarm_state;
    const announceKey = `${health.status}|${alarm}|${state.active_event_id || ""}`;
    if (renderHealth.lastKey !== announceKey) {
      renderHealth.lastKey = announceKey;
      const active = state.events.find((event) => event.event_id === state.active_event_id);
      const alarmLabel = active?.type === "zone_entry" && active.source_mode === "TEST" ? "Synthetic zone entry" : "Motion near entrance detected";
      announce(`${health.label}. Alert ${alarm.toLowerCase()}.${alarm === "ALARM" ? " " + alarmLabel + ". Acknowledge or disarm the alert." : ""}`);
    }
  }

  function renderControls() {
    const connected = authenticated && state && effectiveHealth().status === "healthy";
    const current = state?.alarm_state;
    const adjustable = current === "DISARMED" || current === "READY";
    const active = current === "ARMED" || current === "ALARM" || current === "CALIBRATING";
    const zone = state?.spatial?.zone;
    const position = state?.spatial?.position;
    const targetEligible = state?.spatial?.target !== "zone-entry" || Boolean(state.source_mode === "TEST" && zone && ["test-plan", "webxr-local", "arkit-world"].includes(zone.coordinate_space) && typeof zone.frame_id === "string" && zone.frame_id.length > 0 && state.spatial.localization?.status === "test-simulated" && state.spatial.localization?.exact_zone_verified === false && position?.source_mode === "TEST" && position?.synthetic === true && position?.frame_id === zone.frame_id && position?.inside === false && !state.spatial.test_actor?.running);
    $("calibrate").disabled = !connected || busy || !adjustable || !targetEligible || spatialDraftDirty;
    $("arm").disabled = !connected || busy || !adjustable || !state?.calibration.ready || !state?.features.baseline_ready || !targetEligible || spatialDraftDirty;
    $("arm").hidden = Boolean(active);
    $("disarm").hidden = !active;
    // A safe disarm attempt remains possible if transport is lost.
    $("disarm").disabled = !authenticated || busy || !active;
    $("acknowledge").disabled = !authenticated || busy || current !== "ALARM";
    $("threshold-input").disabled = !connected || busy || !adjustable;
    $("threshold-save").disabled = !connected || busy || !adjustable;
    $("test-motion").disabled = !connected || busy || state?.source_mode !== "TEST" || current === "CALIBRATING";
    $("test-tools").hidden = !authenticated || state?.source_mode !== "TEST";
    $("sound-toggle").disabled = !authenticated;
    $("sound-test").disabled = !authenticated;
    let hint = "Keep the area empty during calibration.";
    if (!connected) hint = "A fresh, healthy stream is required to calibrate or arm.";
    else if (current === "CALIBRATING") hint = "Keep the area empty. Disarm cancels calibration.";
    else if (current === "ARMED" || current === "ALARM") hint = "Detection is armed. Stream faults block new detection.";
    else if (state?.calibration.ready) hint = "Baseline ready. Arm when you want local alerts.";
    if (state?.spatial?.target === "zone-entry" && !targetEligible && adjustable) hint = "TEST zone setup requires a saved outline and a fresh synthetic actor outside. Reset the actor before calibrating or arming. AR outlines support simulation only; real localization is not validated.";
    if (spatialDraftDirty && adjustable) hint = "Save or discard the unsaved zone draft before calibrating or arming from this view.";
    text("control-hint", hint);
    window.dispatchEvent(new CustomEvent("threshold-interface", { detail: {
      authenticated, busy, health: authenticated && state ? effectiveHealth().status : "unpaired",
      api_fresh: Boolean(authenticated && state && !document.hidden && !transportFault && performance.now() - lastResponseAt <= TRANSPORT_STALE_MS)
    } }));
  }

  function renderGraph() {
    const threshold = state.features.threshold;
    const candidates = state.graph.slice(-MAX_GRAPH_POINTS * 4)
      .filter((point) => point && number(point.activity_score) && point.activity_score >= 0 && Number.isFinite(Date.parse(point.timestamp)))
      .map((point) => ({ t: Date.parse(point.timestamp), value: point.activity_score }))
      .sort((a, b) => a.t - b.t);
    const lastTime = candidates.length ? candidates[candidates.length - 1].t : 0;
    const recent = candidates.filter((point) => point.t >= lastTime - 60000);
    // Bounded peak-preserving buckets; no ever-growing client sample history.
    let points = recent;
    if (recent.length > MAX_GRAPH_POINTS) {
      points = [];
      const buckets = Math.floor(MAX_GRAPH_POINTS / 2);
      for (let i = 0; i < buckets; i += 1) {
        const part = recent.slice(Math.floor(i * recent.length / buckets), Math.floor((i + 1) * recent.length / buckets));
        const low = part.reduce((a, b) => a.value < b.value ? a : b);
        const high = part.reduce((a, b) => a.value > b.value ? a : b);
        points.push(...(low.t <= high.t ? [low, high] : [high, low]));
      }
    }
    const maxValue = Math.max(threshold * 1.5, ...points.map((point) => point.value * 1.18), 1);
    const y = (value) => 210 - Math.min(1, Math.max(0, value / maxValue)) * 194;
    const thresholdY = y(threshold);
    $("graph-threshold").setAttribute("y1", thresholdY.toFixed(2));
    $("graph-threshold").setAttribute("y2", thresholdY.toFixed(2));
    $("graph-threshold").setAttribute("visibility", "visible");
    $("graph-empty").hidden = points.length > 0;
    if (!points.length) {
      $("graph-line").setAttribute("d", "");
      $("graph-area").setAttribute("d", "");
      $("graph-end").setAttribute("visibility", "hidden");
      text("graph-window", "No samples yet");
      text("graph-start", "RECENT HISTORY");
      text("graph-description", `No activity samples. Threshold ${fmt(threshold)}. Current score is available as text above.`);
      return;
    }
    const duration = Math.max(1000, lastTime - points[0].t);
    const x = (time) => Math.min(716, Math.max(0, 716 * (time - (lastTime - duration)) / duration));
    const path = points.map((point, i) => `${i ? "L" : "M"}${x(point.t).toFixed(2)},${y(point.value).toFixed(2)}`).join(" ");
    $("graph-line").setAttribute("d", path);
    $("graph-area").setAttribute("d", `${path} L716,210 L${x(points[0].t).toFixed(2)},210 Z`);
    const end = points[points.length - 1];
    $("graph-end").setAttribute("cx", x(end.t).toFixed(2));
    $("graph-end").setAttribute("cy", y(end.value).toFixed(2));
    $("graph-end").setAttribute("visibility", "visible");
    const span = Math.max(0, Math.round((lastTime - points[0].t) / 1000));
    text("graph-window", `${span}s · recent activity`);
    text("graph-start", `${span}s AGO`);
    text("graph-description", `Recent activity over ${span} seconds. Latest ${fmt(end.value)}. Threshold ${fmt(threshold)}. Vertical scale zero to ${fmt(maxValue)}. The chart shows measured or source-labelled synthetic scores, not occupancy.`);
  }

  function element(tag, className, value) {
    const el = document.createElement(tag);
    el.className = className;
    if (value !== undefined) el.textContent = String(value);
    return el;
  }
  function renderEvents() {
    const events = state.events.slice(0, MAX_EVENTS).filter((event) => event && typeof event.event_id === "string");
    const fingerprint = JSON.stringify(events);
    if (eventFingerprint === fingerprint) return;
    eventFingerprint = fingerprint;
    const fragment = document.createDocumentFragment();
    for (const event of events) {
      const item = element("li", "event");
      const time = element("div", "event-time", clock(event.occurred_at));
      const date = new Date(event.occurred_at);
      time.append(element("span", "", Number.isFinite(date.getTime()) ? date.toLocaleDateString([], { month: "short", day: "numeric" }) : "Unknown date"));
      const body = element("div", "event-body");
      const label = element("div", "event-label");
      label.append(element("strong", "event-title", event.type === "zone_entry" ? (event.source_mode === "TEST" ? "Synthetic zone entry" : "Unverified zone-entry report") : "Motion near entrance"));
      label.append(element("span", "event-mode", Object.hasOwn(modes, event.source_mode) ? event.source_mode : "UNKNOWN SOURCE"));
      body.append(label);
      body.append(element("p", "event-detail", `${boundedText(event.area_name, 120)} · score ${fmt(event.activity_score)} / threshold ${fmt(event.threshold)}`));
      body.append(element("p", "event-detail", `Call: ${boundedText(event.call_status, 30) || "unknown"}${event.call_detail ? " · " + boundedText(event.call_detail, 400) : ""}`));
      const acknowledgement = element("div", `event-ack${event.acknowledged ? "" : " pending"}`, event.acknowledged ? "ACKNOWLEDGED" : "NOT ACKNOWLEDGED");
      item.append(time, body, acknowledgement);
      fragment.append(item);
    }
    $("events").replaceChildren(fragment);
    $("history-empty").hidden = events.length > 0;
    text("event-count", `${events.length}${state.events.length > MAX_EVENTS ? "+" : ""} ${events.length === 1 ? "EVENT" : "EVENTS"}`);
  }

  function applyState(data) {
    if (!validState(data)) throw new ApiError("The server state does not match this console’s contract.");
    const oldSession = state?.session_id;
    state = data;
    authenticated = true;
    lastResponseAt = performance.now();
    transportFault = "";
    if (oldSession !== data.session_id) { mutedEvent = null; eventFingerprint = ""; }
    $("pairing").hidden = true;
    $("local-label").hidden = true;
    $("dashboard").hidden = false;
    $("header-tools").hidden = false;
    $("source-banner").dataset.mode = state.source_mode;
    text("source-mode", state.source_mode);
    text("source-explanation", modes[state.source_mode]);
    text("area-name", boundedText(state.area_name, 160));
    const scoreText = number(state.features.activity_score) && state.features.activity_score >= 1000 ? state.features.activity_score.toExponential(1) : fmt(state.features.activity_score);
    text("activity-score", scoreText);
    $("activity-score").classList.toggle("compact-score", scoreText.length > 5);
    text("threshold-value", fmt(state.features.threshold));
    if (document.activeElement !== $("threshold-input") && !$("threshold-form").dataset.dirty) $("threshold-input").value = state.features.threshold;
    text("sample-age", number(state.health.sample_age_s) ? `${fmt(state.health.sample_age_s, 1)}s ago` : "No sample");
    text("valid-packets", fmt(state.health.valid_packets, 0));
    text("invalid-packets", fmt(state.health.invalid_packets, 0));
    const progress = Math.max(0, Math.min(1, state.calibration.progress));
    $("calibration-progress").value = progress;
    text("calibration-percent", `${Math.round(progress * 100)}%`);
    text("calibration-detail", boundedText(state.calibration.detail));
    const current = state.alarm_state;
    $("control-title").closest("aside").dataset.alarm = current;
    text("alarm-state", current);
    const alarmCopy = {
      DISARMED: ["—", "Not armed", "No new motion alarms will be raised. Calibrate an empty area before arming."],
      CALIBRATING: ["◌", "Learning the baseline", "Keep the area empty. Calibration needs enough fresh, valid input."],
      READY: ["·", "Ready when you are", "An empty-area baseline is ready. Arm to enable motion alerts."],
      ARMED: ["+", "Listening for change", "Sustained activity above the threshold can raise a motion-near-entrance alert. This is not an all-clear."],
      ALARM: ["!", "Motion near entrance", "An alert is latched. This does not establish occupancy, direction, or an entrance crossing."]
    };
    if (state.spatial?.target === "zone-entry") {
      alarmCopy.ARMED = ["+", "Watching the TEST boundary", "Only a qualifying synthetic outside-to-inside transition can gate this alert. Real person localization is not validated."];
      const active = state.events.find((event) => event.event_id === state.active_event_id);
      if (current === "ALARM" && active?.type === "zone_entry" && active.source_mode === "TEST") alarmCopy.ALARM = ["!", "Synthetic zone entry", "A TEST actor entered the saved zone. This is not measured person localization, physical entry, or radio coverage."];
    }
    text("alert-glyph", alarmCopy[current][0]);
    text("alarm-title", alarmCopy[current][1]);
    text("alarm-detail", alarmCopy[current][2]);
    $("alarm-actions").hidden = current !== "ALARM";
    text("signal-note", state.features.baseline_ready ? `Activity reflects changes in the radio signal. Quality: ${boundedText(state.features.quality, 100)}. A quiet signal does not prove an empty area.` : "Calibrate an empty area before interpreting the activity score. This signal does not establish occupancy.");
    text("call-status", boundedText(state.calls.status, 50) || "Unknown");
    text("call-detail", boundedText(state.calls.detail) || "No call detail available.");
    text("recording-status", state.recording.error ? "Recording error" : state.recording.enabled ? "Enabled locally" : "Off");
    text("recording-detail", state.recording.error ? boundedText(state.recording.error) : state.recording.enabled ? "Raw records stay on this server." : "No raw recording is enabled.");
    text("limitations", state.limitations.filter((value) => typeof value === "string").slice(0, 5).map((value) => boundedText(value, 350)).join(" ") || "Motion near entrance only. No occupancy or crossing inference.");
    renderGraph();
    renderEvents();
    renderHealth();
    syncAlarmSound();
    window.dispatchEvent(new CustomEvent("threshold-state", { detail: state }));
  }

  function handleFailure(error) {
    if (error.status === 401) { showPair("Your local session has expired. Pair this browser again."); announce("Session expired. Pair this browser to continue."); return; }
    transportFault = error.message || "The server could not be reached.";
    renderHealth();
  }
  function schedulePoll() {
    clearPoll();
    if (!authenticated || document.hidden || busy) return;
    const delay = Math.max(0, POLL_MS - (performance.now() - lastPollStarted));
    pollTimer = setTimeout(poll, delay);
  }
  async function poll() {
    if (!authenticated || document.hidden || polling || busy) return;
    polling = true;
    lastPollStarted = performance.now();
    const requestEpoch = epoch;
    try {
      const data = await api("/api/state");
      if (requestEpoch === epoch && authenticated) applyState(data);
    } catch (error) {
      if (requestEpoch === epoch && authenticated) handleFailure(error);
    } finally {
      polling = false;
      schedulePoll();
    }
  }

  async function control(action, extra = {}) {
    if (!authenticated || busy || !state) return;
    if (action === "arm" && $("arm").disabled) return;
    if (action === "calibrate" && $("calibrate").disabled) return;
    if (action === "set_threshold" && $("threshold-save").disabled) return;
    if (action === "test_motion" && $("test-motion").disabled) return;
    const requestEpoch = ++epoch; // An older in-flight poll must not overwrite a control result.
    busy = true;
    clearPoll();
    if (action === "acknowledge" || action === "disarm") {
      mutedEvent = state.active_event_id || `${state.session_id}:alarm`;
      stopAlarmSound();
    }
    text("action-message", "Sending command…");
    renderControls();
    try {
      const data = await api("/api/control", { method: "POST", body: JSON.stringify({ action, ...extra }) });
      if (requestEpoch !== epoch || !authenticated) return;
      applyState(data);
      if (action === "set_threshold") { delete $("threshold-form").dataset.dirty; $("threshold-input").value = state.features.threshold; }
      const messages = { calibrate: "Calibration started. Keep the monitored area empty.", arm: "Alert armed. Stream health remains separate from alert state.", disarm: "Alert disarmed. Browser alarm silenced.", acknowledge: "Alarm acknowledged. Monitoring remains armed; a quiet interval is required before another alert.", test_motion: "Synthetic TEST motion requested. This is not hardware evidence.", set_threshold: "Threshold changed. Calibrate again before arming." };
      text("action-message", messages[action]);
    } catch (error) {
      if (requestEpoch !== epoch || !authenticated) return;
      if (error.status === 401) { handleFailure(error); return; }
      if (!error.status || error.status >= 500) handleFailure(error);
      const suffix = action === "acknowledge" || action === "disarm" ? " Browser sound is silenced for this event, but server acknowledgement or disarm is NOT confirmed. Retry when connected." : "";
      text("action-message", `${error.message}${suffix}`);
    } finally {
      busy = false;
      renderControls();
      schedulePoll();
    }
  }

  // Spatial is an adapter to this same authenticated request/state/alarm system.
  window.addEventListener("threshold-spatial-draft", (event) => {
    const next = event.detail?.dirty === true;
    if (spatialDraftDirty !== next) { spatialDraftDirty = next; renderControls(); }
  });
  window.addEventListener("threshold-request-state", () => {
    window.dispatchEvent(new CustomEvent("threshold-state", { detail: authenticated ? state : null }));
    renderControls();
  });
  window.addEventListener("threshold-spatial-command", async (event) => {
    const { payload, complete } = event.detail || {};
    if (typeof complete !== "function") return;
    if (!authenticated || !state || busy || document.hidden || transportFault || performance.now() - lastResponseAt > TRANSPORT_STALE_MS) {
      complete({ ok: false, message: "A current paired server connection is required. Try again after the current command finishes." });
      return;
    }
    const requestEpoch = ++epoch;
    busy = true;
    clearPoll();
    renderControls();
    try {
      const data = await api("/api/spatial/control", { method: "POST", body: JSON.stringify(payload) });
      if (requestEpoch !== epoch || !authenticated) {
        complete({ ok: false, message: "Command completion was interrupted. Check the server state before retrying." });
        return;
      }
      applyState(data);
      complete({ ok: true });
    } catch (error) {
      if (requestEpoch === epoch && authenticated && (error.status === 401 || !error.status || error.status >= 500)) handleFailure(error);
      complete({ ok: false, message: error.message || "Spatial command was not confirmed." });
    } finally {
      busy = false;
      renderControls();
      schedulePoll();
    }
  });

  function stopVoices() {
    for (const voice of voices) { try { voice.stop(); } catch { /* already stopped */ } }
    voices.clear();
  }
  function stopAlarmSound() { clearTimeout(soundTimer); soundTimer = null; stopVoices(); }
  function renderSound() {
    text("sound-toggle", soundEnabled ? "Sound on" : "Sound off");
    $("sound-toggle").setAttribute("aria-pressed", String(soundEnabled));
    text("sound-detail", soundEnabled ? "Sound enabled on this device. A short tone repeats only for a latched alarm. Keep this page open; background audio is not guaranteed." : "Sound is off. Enable it here on this device. Keep this page open for browser alerts.");
  }
  function disableSound() {
    soundAttempt += 1;
    soundEnabled = false;
    stopAlarmSound();
    if (audioContext) { audioContext.close().catch(() => {}); audioContext = null; }
    renderSound();
  }
  async function enableAudio() {
    if (!authenticated) return false;
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) { text("action-message", "WebAudio is not available in this browser. Visual alerts remain available."); return false; }
    const attempt = ++soundAttempt;
    try {
      if (!audioContext || audioContext.state === "closed") audioContext = new Audio();
      await audioContext.resume();
      if (!authenticated || attempt !== soundAttempt || !audioContext) return false;
      if (audioContext.state !== "running") throw new Error("Audio is suspended");
      soundEnabled = true;
      mutedEvent = null;
      renderSound();
      return true;
    } catch {
      soundEnabled = false;
      renderSound();
      text("action-message", "Sound could not be enabled. Try again with this tab active. Visual alerts remain available.");
      return false;
    }
  }
  function tone() {
    if (!soundEnabled || !audioContext || audioContext.state !== "running" || voices.size > 4) return;
    const start = audioContext.currentTime;
    for (let i = 0; i < 2; i += 1) {
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = i === 0 ? 740 : 620;
      const at = start + i * 0.22;
      gain.gain.setValueAtTime(0, at);
      gain.gain.linearRampToValueAtTime(0.045, at + 0.025);
      gain.gain.linearRampToValueAtTime(0, at + 0.16);
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      voices.add(oscillator);
      oscillator.onended = () => { voices.delete(oscillator); oscillator.disconnect(); gain.disconnect(); };
      oscillator.start(at);
      oscillator.stop(at + 0.18);
    }
  }
  function syncAlarmSound() {
    const event = state?.active_event_id || `${state?.session_id}:alarm`;
    const alarm = authenticated && soundEnabled && state?.alarm_state === "ALARM" && mutedEvent !== event;
    if (!alarm) { if (soundTimer !== null) stopAlarmSound(); return; }
    if (soundTimer !== null) return;
    const repeat = () => {
      soundTimer = null;
      if (!authenticated || !soundEnabled || state?.alarm_state !== "ALARM" || mutedEvent === (state.active_event_id || `${state.session_id}:alarm`)) return;
      tone();
      soundTimer = setTimeout(repeat, 2000);
    };
    repeat();
  }

  $("pair-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (pairBusy) return;
    const token = $("token").value.trim();
    $("token").value = ""; // Clear immediately, including on login failure. Never store it.
    if (!token) return;
    const requestEpoch = ++epoch;
    pairBusy = true;
    $("pair-submit").disabled = true;
    $("token").disabled = true;
    $("pair-message").classList.remove("error");
    text("pair-message", "Pairing with the local server…");
    try {
      await api("/api/session", { method: "POST", body: JSON.stringify({ token }) });
      const data = await api("/api/state");
      if (requestEpoch !== epoch) return;
      applyState(data);
      schedulePoll();
      $("main").focus();
      announce("Browser paired. " + effectiveHealth().label + ". Source " + state.source_mode + ".");
    } catch (error) {
      if (requestEpoch !== epoch) return;
      text("pair-message", error.status === 401 || error.status === 403 ? "Token not accepted. Read the current token on the server and try again." : error.status === 429 ? "Too many pairing attempts. Wait before trying again." : "Cannot reach a compatible local server. Check that Threshold is running and try again.");
      $("pair-message").classList.add("error");
    } finally {
      pairBusy = false;
      $("pair-submit").disabled = false;
      $("token").disabled = false;
      $("token").value = "";
    }
  });
  $("logout").addEventListener("click", async () => {
    if (!authenticated) return;
    $("logout").disabled = true;
    ++epoch;
    clearPoll();
    stopAlarmSound();
    try {
      await api("/api/logout", { method: "POST" });
      showPair("Browser unpaired. The server’s monitoring state was not changed.");
      $("token").focus();
    } catch (error) {
      if (error.status === 401) showPair("Browser session is no longer active.");
      else {
        handleFailure(error);
        text("action-message", "Unpair could not be confirmed. The session cookie may still be active. Retry when the server is reachable; server monitoring was not changed.");
        schedulePoll();
        syncAlarmSound();
      }
    } finally { $("logout").disabled = false; }
  });
  for (const action of ["calibrate", "arm", "disarm", "acknowledge"]) $(action).addEventListener("click", () => control(action));
  $("test-motion").addEventListener("click", () => control("test_motion"));
  $("threshold-input").addEventListener("input", () => { $("threshold-form").dataset.dirty = "true"; });
  $("threshold-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const threshold = $("threshold-input").valueAsNumber;
    if (number(threshold) && threshold > 0 && $("threshold-form").reportValidity()) control("set_threshold", { threshold });
  });
  $("sound-toggle").addEventListener("click", async () => {
    if (soundEnabled) { disableSound(); return; }
    if (await enableAudio()) { text("action-message", "Sound enabled. Use Test sound to check your device volume."); syncAlarmSound(); }
  });
  $("sound-test").addEventListener("click", async () => {
    if (await enableAudio()) { tone(); text("action-message", "Sound test played. This does not create a detector event."); syncAlarmSound(); }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearPoll();
      ++epoch;
      for (const controller of requests) controller.abort();
      if (busy) text("action-message", "The tab became inactive while a command was pending. Its result is not confirmed; checking current state when this tab returns.");
      if (pairBusy) text("pair-message", "Pairing was interrupted while the tab was inactive. Try again when you return.");
      else if (!authenticated) text("pair-message", "Enter your token to open the local console.");
      if (authenticated) renderHealth();
    } else if (authenticated) {
      transportFault = "Checking the connection after this tab was inactive.";
      renderHealth();
      schedulePoll();
    }
  });
  window.addEventListener("pagehide", () => { clearPoll(); stopAlarmSound(); for (const controller of requests) controller.abort(); });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted && authenticated) {
      transportFault = "Checking the connection after restoring this page.";
      renderHealth();
      schedulePoll();
      syncAlarmSound();
    }
  });
  setInterval(() => {
    if (authenticated && !document.hidden && performance.now() - lastResponseAt > TRANSPORT_STALE_MS) renderHealth();
  }, 1000);

  const bootEpoch = epoch;
  api("/api/state").then((data) => {
    if (bootEpoch !== epoch) return;
    applyState(data);
    schedulePoll();
  }).catch((error) => {
    if (bootEpoch !== epoch) return;
    showPair(error.status === 401 ? "Enter your token to open the local console." : "Local server unavailable. Start Threshold, then pair this browser.");
  });
})();

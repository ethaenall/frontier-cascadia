/* A spatial adapter, not a second detector. Camera pixels never leave this page. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const svgNS = "http://www.w3.org/2000/svg";
  let state = null, ui = { authenticated: false, busy: false, api_fresh: false }, pending = false;
  let adapter = "plan", mediaStream = null, mediaPending = false, xrStarting = false, generation = 0, session = null, xrSpace = null, hitSource = null;
  let xrFrameHandle = null, xrHit = null, captureEnabled = false, gl = null, renderer = null, dragIndex = -1, selected = -1;
  let dirty = false, draftVersion = 0, lastDraftGate = null, vertexFingerprint = "", previousSession = null, loadedZoneKey = "";
  const markDirty = () => { dirty = true; draftVersion += 1; };
  const makeFrame = space => `${space}:${globalThis.crypto?.randomUUID?.() || Date.now().toString(36) + Math.random().toString(36).slice(2, 12)}`;
  const makeDraft = () => ({ name: "Entrance zone", vertices: [], coordinate_space: "test-plan", frame_id: makeFrame("test-plan") });
  let draft = makeDraft();
  const text = (id, value) => { const node = $(id); const next = String(value ?? ""); if (node.textContent !== next) node.textContent = next; };
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const safe = value => typeof value === "string" ? value.slice(0, 650) : "";
  const message = value => text("spatial-message", value);
  const adjustable = () => ui.authenticated && state && ["DISARMED", "READY"].includes(state.alarm_state) && !ui.busy && !pending;
  const canEdit = () => adjustable() && adapter === "plan";
  const numberText = value => finite(value) ? value.toFixed(2) : "—";

  function geometryProblem(vertices = draft.vertices) {
    if (vertices.length < 3) return "Add at least 3 corners.";
    if (vertices.length > 16) return "A zone can have at most 16 corners.";
    for (let i = 0; i < vertices.length; i += 1) {
      const p = vertices[i];
      if (!finite(p.x) || !finite(p.z) || Math.abs(p.x) > 100 || Math.abs(p.z) > 100) return "Coordinates must be finite and within ±100 draft units.";
      if (vertices.some((q, j) => i !== j && Math.hypot(p.x - q.x, p.z - q.z) < 0.001)) return "Each corner must be distinct.";
    }
    const cross = (a, b, c) => (b.x - a.x) * (c.z - a.z) - (b.z - a.z) * (c.x - a.x);
    const on = (a, b, p) => Math.abs(cross(a, b, p)) < 1e-7 && p.x >= Math.min(a.x, b.x) - 1e-7 && p.x <= Math.max(a.x, b.x) + 1e-7 && p.z >= Math.min(a.z, b.z) - 1e-7 && p.z <= Math.max(a.z, b.z) + 1e-7;
    const intersects = (a, b, c, d) => cross(a,b,c) * cross(a,b,d) < 0 && cross(c,d,a) * cross(c,d,b) < 0 || on(a,b,c) || on(a,b,d) || on(c,d,a) || on(c,d,b);
    let area = 0;
    for (let i = 0; i < vertices.length; i += 1) {
      const a = vertices[i], b = vertices[(i + 1) % vertices.length];
      area += a.x * b.z - b.x * a.z;
      for (let j = i + 1; j < vertices.length; j += 1) {
        if (j === i + 1 || (i === 0 && j === vertices.length - 1)) continue;
        if (intersects(a, b, vertices[j], vertices[(j + 1) % vertices.length])) return "The outline crosses or touches itself. Move or remove a corner.";
      }
    }
    return Math.abs(area) < 0.001 ? "The outline needs a non-zero area." : "";
  }
  function loadZone(zone) {
    draft = { name: zone.name, vertices: zone.vertices.slice(0,16).map(point => ({ x: point.x, z: point.z })), coordinate_space: zone.coordinate_space, frame_id: zone.frame_id };
    if (finite(zone.floor_y)) draft.floor_y = zone.floor_y;
    selected = draft.vertices.length ? 0 : -1; dirty = false;
    loadedZoneKey = `${zone.id}:${zone.revision}:${zone.frame_id}`;
    $("zone-name").value = draft.name;
  }
  function projection() {
    const extent = draft.coordinate_space === "test-plan" ? 4 : Math.max(4, ...draft.vertices.map(p => Math.max(Math.abs(p.x), Math.abs(p.z)) * 1.12));
    return { scale: 200 / extent, extent };
  }
  const mapPoint = point => { const { scale } = projection(); return `${(200 + point.x * scale).toFixed(2)},${(200 + point.z * scale).toFixed(2)}`; };
  function renderMap() {
    $("draft-zone").setAttribute("points", draft.vertices.map(mapPoint).join(" "));
    const saved = state?.spatial?.zone;
    const sameFrame = saved?.frame_id === draft.frame_id && saved?.coordinate_space === draft.coordinate_space;
    $("saved-zone").setAttribute("points", sameFrame ? saved.vertices.slice(0,16).map(mapPoint).join(" ") : "");
    const fingerprint = JSON.stringify([draft.vertices, selected, canEdit()]);
    if (fingerprint !== vertexFingerprint) {
      vertexFingerprint = fingerprint;
      const group = document.createDocumentFragment(), list = document.createDocumentFragment();
      for (let i = 0; i < draft.vertices.length; i += 1) {
        const [cx, cy] = mapPoint(draft.vertices[i]).split(",");
        const circle = document.createElementNS(svgNS, "circle");
        circle.setAttribute("cx", cx); circle.setAttribute("cy", cy); circle.setAttribute("r", i === selected ? "6" : "4.5");
        circle.setAttribute("class", "corner-ring" + (i === selected ? " selected" : ""));
        const label = document.createElementNS(svgNS, "text");
        label.setAttribute("x", Number(cx) + 10); label.setAttribute("y", Number(cy) - 10); label.setAttribute("class", "corner-label"); label.textContent = String(i + 1);
        group.append(circle, label);
        const button = document.createElement("button");
        button.type = "button"; button.className = "vertex-button"; button.textContent = String(i + 1);
        button.setAttribute("aria-label", `Edit corner ${i + 1}`); button.setAttribute("aria-pressed", String(i === selected)); button.disabled = !canEdit();
        button.addEventListener("click", () => { selected = i; render(); }); list.append(button);
      }
      const restoreCornerFocus = $("vertex-list").contains(document.activeElement);
      $("draft-points").replaceChildren(group); $("vertex-list").replaceChildren(list);
      if (restoreCornerFocus && selected >= 0) $("vertex-list").children[selected]?.focus();
    }
    const actor = state?.spatial?.position;
    const actorVisible = adapter === "plan" && sameFrame && state?.source_mode === "TEST" && state.spatial.localization?.status === "test-simulated" && actor?.source_mode === "TEST" && actor?.synthetic === true && actor?.frame_id === draft.frame_id && finite(actor?.x) && finite(actor?.z);
    $("actor-marker").toggleAttribute("hidden", !actorVisible);
    if (actorVisible) $("actor-marker").setAttribute("transform", `translate(${mapPoint(actor).replace(",", " ")})`);
    text("actor-label", actorVisible ? `TEST ACTOR · ${actor.inside ? "INSIDE" : "OUTSIDE"} · SYNTHETIC POSITION` : "NO REAL PERSON TRACKING");
    $("stage-empty").hidden = draft.vertices.length > 0 || adapter !== "plan";
    $("zone-map").toggleAttribute("hidden", adapter !== "plan");
    $("zone-map").style.pointerEvents = canEdit() ? "auto" : "none";
    text("zone-map-description", `${draft.coordinate_space === "test-plan" ? "Unmeasured TEST plan" : "Captured AR geometry diagram from its named frame, not registered to this view"}. ${draft.vertices.length} corners. ${geometryProblem() || "Valid local outline; the server validates it again."} No real person localization or radio coverage is established.`);
  }
  function render() {
    const available = Boolean(state?.spatial);
    const writable = adjustable() && ui.api_fresh && available;
    const editing = canEdit();
    const isPlan = draft.coordinate_space === "test-plan";
    const zone = state?.spatial?.zone;
    const issue = geometryProblem();
    const selectedPoint = draft.vertices[selected];
    $("zone-name").disabled = !adjustable();
    $("add-corner").disabled = !editing || draft.vertices.length >= 16;
    $("sample-zone").disabled = !editing;
    $("vertex-x").disabled = !editing || !selectedPoint;
    $("vertex-z").disabled = !editing || !selectedPoint;
    $("remove-corner").disabled = !editing || !selectedPoint;
    for (const axis of ["x", "z"]) if (document.activeElement !== $("vertex-" + axis)) $("vertex-" + axis).value = selectedPoint ? numberText(selectedPoint[axis]) : "";
    $("save-zone").disabled = !writable || adapter === "camera" || adapter === "xr" || Boolean(issue) || !draft.name.trim();
    $("clear-zone").disabled = !writable || !zone;
    $("target-radio").disabled = !writable;
    $("target-zone").disabled = !writable || state?.source_mode !== "TEST";
    $("target-radio").setAttribute("aria-pressed", String(state?.spatial?.target !== "zone-entry"));
    $("target-zone").setAttribute("aria-pressed", String(state?.spatial?.target === "zone-entry"));
    $("start-ar").disabled = !adjustable() || mediaPending || adapter === "xr";
    $("start-camera").disabled = !ui.authenticated || pending || mediaPending || adapter === "xr" || adapter === "camera";
    $("use-plan").disabled = !adjustable();
    $("stop-media").hidden = adapter === "plan" && !mediaStream && !session && !mediaPending;
    $("xr-add-point").hidden = adapter !== "xr";
    $("xr-capture-toggle").hidden = adapter !== "xr";
    $("xr-capture-toggle").setAttribute("aria-pressed", String(captureEnabled));
    text("xr-capture-toggle", captureEnabled ? "Pause corner capture" : "Enable corner capture");
    $("xr-add-point").disabled = !adjustable() || !captureEnabled || !xrHit || draft.vertices.length >= 16;
    $("spatial-test-tools").hidden = state?.source_mode !== "TEST" || !available;
    $("test-walk").disabled = !ui.authenticated || !ui.api_fresh || ui.busy || pending || state?.source_mode !== "TEST" || !state?.spatial?.test_actor?.can_start;
    $("reset-actor").disabled = !ui.authenticated || !ui.api_fresh || ui.busy || pending || state?.source_mode !== "TEST" || !state?.spatial?.test_actor?.can_reset;
    text("vertex-count", `${draft.vertices.length} / 16 corners`);
    text("coordinate-label", isPlan ? "X / Z · unmeasured" : "X / Z · estimated metres");
    text("draft-state", dirty ? "UNSAVED DRAFT" : zone?.frame_id === draft.frame_id ? `SAVED · R${zone.revision}` : "NOT SAVED");
    text("geometry-detail", issue || (isPlan ? "Outline valid locally. TEST units are unmeasured. The server checks the geometry again." : "Geometry draft only. These estimated metre coordinates belong to one AR frame, not a registered radio field."));
    text("save-zone", isPlan ? "Save TEST zone" : "Save AR draft");
    text("saved-zone-detail", zone ? `Saved: ${safe(zone.name)} · revision ${zone.revision} · ${zone.coordinate_space}. ${dirty ? "Alerts use this saved zone, not your unsaved draft." : zone.coordinate_space !== "test-plan" ? "No real exact-zone arming is available for this geometry." : "Synthetic position simulator only."}` : "No zone saved on this server.");
    text("persistent-source", state?.source_mode || "—");
    text("localization-status", state?.spatial?.localization?.status === "test-simulated" ? "TEST position simulator" : state?.spatial?.localization?.status === "stale" ? "Position evidence stale" : "Real localization unavailable");
    text("localization-detail", safe(state?.spatial?.localization?.detail) || "Drawing or scanning geometry does not establish radio coverage or person coordinates.");
    text("zone-guard", available ? `Entry gate: ${safe(state.spatial.guard?.reason) || "No valid synthetic entry."} Real exact-zone localization remains unverified.` : "Spatial API unavailable. The existing radio controls below remain separate from any geometry draft.");
    text("target-detail", state?.spatial?.target === "zone-entry" ? "TEST zone-entry target. Save a plan or AR outline, reset the synthetic actor outside, calibrate, then arm for simulation. Real exact-zone detection remains unavailable." : "Radio-motion target. It does not confirm entry into the drawn zone. One detector and one history are shared with the classic view.");
    text("test-actor-detail", `TEST actor: ${safe(state?.spatial?.test_actor?.phase) || "unavailable"}. Virtual positions, never a real person. Acknowledge does not invent a new crossing; reset outside before another walk.`);
    $("spatial-stage").dataset.adapter = adapter;
    text("adapter-label", adapter === "xr" ? "WEBXR HIT-TEST" : adapter === "camera" ? "CAMERA PREVIEW" : isPlan ? "TEST PLAN" : "AR DRAFT DIAGRAM");
    text("adapter-units", adapter === "camera" ? "NOT WORLD-TRACKED" : adapter === "xr" || !isPlan ? "PLATFORM-ESTIMATED METRES" : "UNMEASURED UNITS");
    text("stage-provenance", adapter === "camera" ? "Local video only. No scan, tracking, or zone alignment." : adapter === "xr" ? "Session floor hits only. Radio coverage is not registered." : isPlan ? "A drawn TEST draft. Not a measured room." : "Ended AR frame. Diagram only; not anchored to this view.");
    renderMap();
  }
  async function command(payload, success) {
    if (!ui.authenticated || pending || ui.busy || !ui.api_fresh) return;
    pending = true; message("Sending spatial command…"); render();
    const result = await new Promise(resolve => {
      const timeout = setTimeout(() => resolve({ ok: false, message: "Spatial command completion is unknown. Check current server state before retrying." }), 3500);
      window.dispatchEvent(new CustomEvent("threshold-spatial-command", { detail: { payload, complete: value => { clearTimeout(timeout); resolve(value); } } }));
    });
    pending = false;
    if (result.ok) {
      if (payload.action === "set_zone" && state?.spatial?.zone) loadZone(state.spatial.zone);
      if (payload.action === "clear_zone") { draft = makeDraft(); selected = -1; dirty = false; $("zone-name").value = draft.name; }
      message(success);
    } else message(result.message || "Spatial command was not confirmed.");
    render();
  }
  function addPoint(point) {
    if (draft.vertices.length >= 16) return;
    draft.vertices.push({ x: Math.round(point.x * 1000) / 1000, z: Math.round(point.z * 1000) / 1000 });
    selected = draft.vertices.length - 1; dirty = true; render();
  }
  function pointFromPointer(event) {
    const rect = $("zone-map").getBoundingClientRect(), { extent } = projection();
    return { x: Math.max(-extent + .05, Math.min(extent - .05, (event.clientX - rect.left) / rect.width * extent * 2 - extent)), z: Math.max(-extent + .05, Math.min(extent - .05, (event.clientY - rect.top) / rect.height * extent * 2 - extent)) };
  }
  $("zone-map").addEventListener("pointerdown", event => {
    if (!canEdit() || event.button !== 0) return;
    const point = pointFromPointer(event), { extent } = projection();
    let nearest = -1, distance = extent * .13;
    for (let i = 0; i < draft.vertices.length; i += 1) { const d = Math.hypot(draft.vertices[i].x - point.x, draft.vertices[i].z - point.z); if (d < distance) { distance = d; nearest = i; } }
    if (nearest < 0) { addPoint(point); dragIndex = -1; }
    else { selected = nearest; dragIndex = nearest; render(); }
    $("zone-map").setPointerCapture(event.pointerId);
  });
  $("zone-map").addEventListener("pointermove", event => {
    if (dragIndex < 0 || !canEdit()) return;
    const point = pointFromPointer(event); draft.vertices[dragIndex] = { x: Math.round(point.x * 100) / 100, z: Math.round(point.z * 100) / 100 }; dirty = true; render();
  });
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) $("zone-map").addEventListener(type, () => { dragIndex = -1; });
  $("zone-name").addEventListener("input", () => { draft.name = $("zone-name").value.slice(0,64); dirty = true; render(); });
  $("add-corner").addEventListener("click", () => {
    if (!canEdit()) return;
    const defaults = [{x:-1.5,z:-1.5},{x:1.5,z:-1.5},{x:1.5,z:1.5},{x:-1.5,z:1.5}];
    addPoint(defaults[draft.vertices.length] || {x:0,z:0});
  });
  $("sample-zone").addEventListener("click", () => {
    if (!canEdit()) return;
    if (draft.coordinate_space !== "test-plan") draft = makeDraft();
    draft.vertices = [{x:-1.5,z:-1.5},{x:1.5,z:-1.5},{x:1.5,z:1.5},{x:-1.5,z:1.5}]; selected = 0; dirty = true; render(); message("Sample TEST outline drawn. It is unmeasured and not saved until you choose Save TEST zone.");
  });
  for (const axis of ["x", "z"]) $("vertex-" + axis).addEventListener("change", () => {
    if (!canEdit() || !draft.vertices[selected]) return;
    const value = $("vertex-" + axis).valueAsNumber;
    if (!finite(value) || Math.abs(value) > 100) { message("Enter a finite coordinate within ±100 draft units."); render(); return; }
    draft.vertices[selected][axis] = value; dirty = true; render();
  });
  $("remove-corner").addEventListener("click", () => { if (!canEdit() || selected < 0) return; draft.vertices.splice(selected,1); selected = Math.min(selected,draft.vertices.length-1); dirty = true; render(); });
  $("save-zone").addEventListener("click", () => {
    if ($("save-zone").disabled) return;
    const payload = { action: "set_zone", name: draft.name.trim(), vertices: draft.vertices.map(p => ({...p})), coordinate_space: draft.coordinate_space, frame_id: draft.frame_id };
    if (finite(draft.floor_y)) payload.floor_y = draft.floor_y;
    command(payload, draft.coordinate_space === "test-plan" ? "TEST zone saved. Reset the virtual actor outside, then calibrate before arming zone entry." : "AR geometry draft saved. Real person localization and exact-zone arming remain unavailable.");
  });
  $("clear-zone").addEventListener("click", () => { if (!$("clear-zone").disabled) command({ action: "clear_zone" }, "Saved geometry cleared. Detection configuration must be calibrated again."); });
  $("target-radio").addEventListener("click", () => { if (!$("target-radio").disabled) command({action:"set_target",target:"radio-motion"}, "Shared target set to radio motion. This does not confirm zone entry. Calibrate again before arming."); });
  $("target-zone").addEventListener("click", () => { if (!$("target-zone").disabled) command({action:"set_target",target:"zone-entry"}, "Shared target set to TEST zone entry. Only the labelled position simulator can qualify an entry."); });
  $("test-walk").addEventListener("click", () => { if (!$("test-walk").disabled) command({action:"test_walk"}, "Synthetic TEST walk requested. No real person or phone position is being measured."); });
  $("reset-actor").addEventListener("click", () => { if (!$("reset-actor").disabled) command({action:"reset_test_actor"}, "TEST actor reset outside. This does not acknowledge or silence a latched alarm."); });

  async function stopMedia(reason = "Camera and AR stopped. The plan is not world-tracked.") {
    ++generation;
    mediaPending = false; xrStarting = false;
    const previous = session; session = null;
    if (hitSource) { try { hitSource.cancel(); } catch {} hitSource = null; }
    if (previous) { try { if (xrFrameHandle !== null) previous.cancelAnimationFrame(xrFrameHandle); await previous.end(); } catch {} }
    xrFrameHandle = null; xrSpace = null; xrHit = null; captureEnabled = false;
    if (mediaStream) { for (const track of mediaStream.getTracks()) track.stop(); mediaStream = null; }
    $("camera-preview").pause(); $("camera-preview").srcObject = null; $("camera-preview").hidden = true;
    $("xr-canvas").hidden = true; $("xr-instructions").hidden = true;
    if (gl && renderer) { gl.deleteBuffer(renderer.buffer); gl.deleteProgram(renderer.program); }
    gl = null; renderer = null; adapter = "plan"; document.body.classList.remove("xr-active");
    text("capability-detail", reason); render();
  }
  async function startCamera() {
    if (!ui.authenticated || $("start-camera").disabled) return;
    if (!window.isSecureContext) { text("capability-detail", "Camera preview is unavailable on insecure HTTP. A phone viewing the Mac’s LAN HTTP address is not a secure context. Use the TEST plan; do not treat it as AR."); return; }
    if (!navigator.mediaDevices?.getUserMedia) { text("capability-detail", "This browser does not expose camera access here. Use the TEST plan. No scan or tracking has occurred."); return; }
    await stopMedia("Requesting a local camera preview. No frames are recorded or uploaded.");
    if (!ui.authenticated || document.hidden) return;
    const attempt = ++generation;
    mediaPending = true; render();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
      if (attempt !== generation || !ui.authenticated || document.hidden) { for (const track of stream.getTracks()) track.stop(); return; }
      mediaPending = false; mediaStream = stream; $("camera-preview").srcObject = stream; $("camera-preview").hidden = false; adapter = "camera";
      await $("camera-preview").play();
      text("capability-detail", "Local camera preview only. NOT world-tracked AR; no floor scan, zone alignment, or person localization. Frames stay on this device. Stop preview at any time."); render();
    } catch {
      if (attempt === generation) await stopMedia("Camera permission was denied or preview could not start. No scan or tracking occurred. The TEST plan still works.");
    }
  }
  function createRenderer(context) {
    const compile = (type, source) => { const shader = context.createShader(type); context.shaderSource(shader,source); context.compileShader(shader); if (!context.getShaderParameter(shader,context.COMPILE_STATUS)) { context.deleteShader(shader); throw new Error("XRRendererUnavailable"); } return shader; };
    const vs = compile(context.VERTEX_SHADER,"attribute vec3 a;uniform mat4 projection;uniform mat4 view;void main(){gl_Position=projection*view*vec4(a,1.0);gl_PointSize=10.0;}");
    const fs = compile(context.FRAGMENT_SHADER,"precision mediump float;void main(){gl_FragColor=vec4(0.84,0.88,0.64,1.0);}");
    const program=context.createProgram();context.attachShader(program,vs);context.attachShader(program,fs);context.linkProgram(program);context.deleteShader(vs);context.deleteShader(fs);
    if(!context.getProgramParameter(program,context.LINK_STATUS))throw new Error("XRRendererUnavailable");
    return {program,buffer:context.createBuffer(),attribute:context.getAttribLocation(program,"a"),projection:context.getUniformLocation(program,"projection"),view:context.getUniformLocation(program,"view")};
  }
  function drawXR(frame) {
    const active=session;if(!active||!xrSpace||!gl||!renderer)return;
    xrFrameHandle=active.requestAnimationFrame((time,next)=>drawXR(next));
    const pose=frame.getViewerPose(xrSpace); if(!pose){xrHit=null;$("xr-add-point").disabled=true;return;}
    const hits=hitSource?frame.getHitTestResults(hitSource):[];
    const hitPose=hits[0]?.getPose(xrSpace);xrHit=hitPose?{x:hitPose.transform.position.x,y:hitPose.transform.position.y,z:hitPose.transform.position.z}:null;
    text("xr-hit-status",xrHit?`Floor hit available · ${draft.vertices.length}/16 corners. Place only corners on the same floor. Radio coverage is not registered.`:"Move slowly until a floor hit is available. Nothing has been scanned or placed yet.");
    $("xr-add-point").disabled=!adjustable()||!captureEnabled||!xrHit||draft.vertices.length>=16;
    const layer=active.renderState.baseLayer;gl.bindFramebuffer(gl.FRAMEBUFFER,layer.framebuffer);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.useProgram(renderer.program);gl.bindBuffer(gl.ARRAY_BUFFER,renderer.buffer);gl.enableVertexAttribArray(renderer.attribute);gl.vertexAttribPointer(renderer.attribute,3,gl.FLOAT,false,0,0);
    const line=draft.vertices.map(p=>[p.x,draft.floor_y||0,p.z]);if(line.length>2)line.push(line[0]);
    const cursor=xrHit?Array.from({length:25},(_,i)=>[xrHit.x+Math.cos(i/24*Math.PI*2)*.055,xrHit.y+.005,xrHit.z+Math.sin(i/24*Math.PI*2)*.055]):[];
    for(const view of pose.views){const viewport=layer.getViewport(view);gl.viewport(viewport.x,viewport.y,viewport.width,viewport.height);gl.uniformMatrix4fv(renderer.projection,false,view.projectionMatrix);gl.uniformMatrix4fv(renderer.view,false,view.transform.inverse.matrix);for(const points of [line,cursor]){if(!points.length)continue;gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(points.flat()),gl.DYNAMIC_DRAW);gl.drawArrays(gl.LINE_STRIP,0,points.length);if(points===line)gl.drawArrays(gl.POINTS,0,points.length);}}
  }
  function captureHit() {
    if(adapter!=="xr"||!session||!xrHit||!captureEnabled||!adjustable()||draft.vertices.length>=16)return;
    if(finite(draft.floor_y)&&Math.abs(xrHit.y-draft.floor_y)>.25){text("xr-hit-status","That hit is not on the same estimated floor. Choose a floor point near the existing height.");return;}
    if(!finite(draft.floor_y))draft.floor_y=xrHit.y;
    addPoint({x:xrHit.x,z:xrHit.z});
  }
  async function startAR() {
    if(!adjustable()||$("start-ar").disabled)return;
    if(!window.isSecureContext){text("capability-detail","World-tracked WebXR is unavailable on insecure HTTP. A phone’s ordinary Mac-LAN HTTP URL cannot grant this capability. Use the labelled TEST plan.");return;}
    if(!navigator.xr?.isSessionSupported){text("capability-detail","This browser has no immersive WebXR AR support. iPhone Safari cannot be assumed to support it. Use the TEST plan or a separately labelled camera preview; no scan occurred.");return;}
    const capabilityEpoch=generation;
    let supported=false;try{supported=await navigator.xr.isSessionSupported("immersive-ar");}catch{}
    if(capabilityEpoch!==generation||!ui.authenticated||document.hidden)return;
    if(!supported){text("capability-detail","Immersive AR is not supported on this device/browser. No camera or scan was started. TEST plan editing remains available.");return;}
    await stopMedia("Starting a capability-checked WebXR floor draft…");
    if(!ui.authenticated||document.hidden)return;
    const attempt=++generation;
    mediaPending=true;xrStarting=true;render();
    try{
      const active=await navigator.xr.requestSession("immersive-ar",{requiredFeatures:["hit-test"],optionalFeatures:["dom-overlay","local-floor"],domOverlay:{root:$("dashboard")}});
      if(attempt!==generation||!ui.authenticated||(document.hidden&&active.visibilityState!=="visible")){await active.end();return;}
      session=active;xrStarting=false;
      if(!active.domOverlayState){await stopMedia("This AR session cannot keep the on-screen safety controls visible. It was ended. Use the TEST plan or camera preview instead.");return;}
      draft={name:$("zone-name").value.trim()||"AR geometry draft",vertices:[],coordinate_space:"webxr-local",frame_id:makeFrame("webxr-local")};selected=-1;dirty=true;
      gl=$("xr-canvas").getContext("webgl",{xrCompatible:true,alpha:true,antialias:false});if(!gl)throw new Error("XRGraphicsUnavailable");
      await gl.makeXRCompatible();if(attempt!==generation||session!==active){await active.end();return;}
      active.updateRenderState({baseLayer:new XRWebGLLayer(active,gl)});renderer=createRenderer(gl);
      const localSpace=await active.requestReferenceSpace("local");const viewer=await active.requestReferenceSpace("viewer");const newHitSource=await active.requestHitTestSource({space:viewer});
      if(attempt!==generation||session!==active||!ui.authenticated||(document.hidden&&active.visibilityState!=="visible")){try{newHitSource.cancel();}catch{}await active.end();return;}
      xrSpace=localSpace;hitSource=newHitSource;mediaPending=false;
      xrSpace.addEventListener("reset",()=>{draft.vertices=[];draft.frame_id=makeFrame("webxr-local");delete draft.floor_y;selected=-1;dirty=true;xrHit=null;captureEnabled=false;message("AR origin changed. Previous corners were discarded. Capture a new draft; older saved geometry is not registered.");render();});
      active.addEventListener("select",captureHit);
      active.addEventListener("visibilitychange",()=>{if(session!==active)return;if(active.visibilityState==="hidden")void stopMedia("AR session became hidden and was ended for privacy.");else if(active.visibilityState==="visible-blurred"){captureEnabled=false;render();}});
      active.addEventListener("end",()=>{if(session===active){session=null;void stopMedia("AR session ended. Captured geometry is now a draft diagram, not anchored to a new camera or AR origin.");}});
      adapter="xr";document.body.classList.add("xr-active");$("xr-canvas").hidden=false;$("xr-instructions").hidden=false;
      text("capability-detail","WebXR floor hit testing is active in this session only. Geometry is estimated, radio coverage is not registered, and real exact-zone arming remains unavailable.");render();
      xrFrameHandle=active.requestAnimationFrame((time,frame)=>drawXR(frame));
    }catch{if(attempt===generation)await stopMedia("AR permission, hit testing, or graphics setup was unavailable. No verified room scan or radio registration occurred. Use the TEST plan.");}
  }
  // A HUD tap must never become a second floor-placement select event.
  $("dashboard").addEventListener("beforexrselect",event=>{if(event.target.closest("button,input,summary,a,label,form,.control-panel,.zone-editor"))event.preventDefault();});
  $("xr-capture-toggle").addEventListener("click",()=>{if(adapter!=="xr"||!adjustable())return;captureEnabled=!captureEnabled;render();});
  $("start-camera").addEventListener("click",startCamera);$("start-ar").addEventListener("click",startAR);$("stop-media").addEventListener("click",()=>void stopMedia());$("xr-add-point").addEventListener("click",captureHit);
  $("use-plan").addEventListener("click",async()=>{if(!adjustable())return;await stopMedia();draft=makeDraft();selected=-1;dirty=true;$("zone-name").value=draft.name;render();message("New unmeasured TEST draft. Any saved zone remains unchanged until you save or clear it.");});
  $("logout").addEventListener("click",()=>void stopMedia("Preview stopped while unpairing. Server disarm is a separate action."));
  document.addEventListener("visibilitychange",()=>{
    if(!document.hidden)return;
    // Immersive AR can own visible presentation while the document becomes hidden.
    // Pending XR resolves into its own visibility state; auth/epoch still gate it.
    if(xrStarting||session?.visibilityState==="visible")return;
    void stopMedia("Camera/AR stopped because the page became hidden. Restart only with an explicit gesture.");
  });
  window.addEventListener("pagehide",()=>void stopMedia());
  window.addEventListener("threshold-interface",event=>{ui=event.detail||{authenticated:false};render();});
  window.addEventListener("threshold-state",event=>{
    state=event.detail;
    if(!state){previousSession=null;loadedZoneKey="";draft=makeDraft();selected=-1;dirty=false;void stopMedia("Pair this browser before using camera, AR, or spatial controls.");render();return;}
    if(previousSession!==state.session_id){previousSession=state.session_id;loadedZoneKey="";draft=makeDraft();selected=-1;dirty=false;}
    const zone=state.spatial?.zone;
    // Saved world geometry is never re-anchored into a different live XR frame.
    if(zone&&!dirty&&adapter==="plan"&&loadedZoneKey!==`${zone.id}:${zone.revision}:${zone.frame_id}`)loadZone(zone);
    if(!zone&&!dirty&&loadedZoneKey&&adapter==="plan"){draft=makeDraft();selected=-1;loadedZoneKey="";}
    render();
  });
  render();window.dispatchEvent(new CustomEvent("threshold-request-state"));
})();

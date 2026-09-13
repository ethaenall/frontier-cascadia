/**
 * Threshold — local cinematic GPU engine.
 * All scenes are illustrative concepts, not measured sensing or RF coverage.
 * Dependency: vendored Three.js 0.180.0; no network or detector access.
 */
import * as THREE from './vendor/three.webgpu.js';
import * as TSL from './vendor/three.tsl.js';

const PALETTE = Object.freeze({
  void: 0x06090d, steel: 0x1a2532, wall: 0x28323d,
  silver: 0xadc1cf, ice: 0xa6dcff, white: 0xeaf5ff, amber: 0xeeb56f,
});
const QUALITY = Object.freeze({
  low: { dpr: 1, pixels: 1450000, fps: 30 },
  auto: { dpr: 1.5, pixels: 2800000, fps: 60 },
  high: { dpr: 2, pixels: 4800000, fps: 60 },
});
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const ease = (v) => { const t = clamp(v, 0, 1); return t * t * (3 - 2 * t); };
const qualityName = (v) => Object.hasOwn(QUALITY, v) ? v : 'auto';

/** Presenter-controlled. No input, detector, network, or chapter-advance side effects. */
export async function createScene(canvas, {
  reducedMotion = false, quality = 'auto', onStatus = () => {}, onCue = () => {},
} = {}) {
  let renderer = null;
  let disposed = false;
  let failed = false;
  let backend = 'Static';
  let detail = 'GPU initialization pending';
  let chapter = 0;
  let motionReduced = Boolean(reducedMotion);
  let qualityMode = qualityName(quality);
  let dynamicScale = 1;
  let width = 1, height = 1, pixelRatio = 1;
  let hidden = typeof document === 'undefined' || document.hidden;
  let chapterTime = 0, activeTime = 0, lastTime = 0, lastDraw = 0;
  let dirty = true, transition = 1;
  let cueActive = false;
  let displayChapter = 0, priorChapter = 0, priorTime = 0;
  let transitionDuration = 1.25, linkedTransition = false, transitionPose = null;
  const isRoomChapter = (index) => index === 3 || index === 4 || index === 5 || index === 8;
  const isCosmicChapter = (index) => index >= 0 && index <= 2;
  const crossingAt = 0.65 + 4.2 * (0.5 - Math.sin(Math.asin(1 - 2 * ((0.57 + 1.35) / 2.95)) / 3));
  let resizeObserver = null;
  let gpuDevice = null;
  let initialized = false;
  const resources = { geometry: new Set(), material: new Set(), texture: new Set() };
  const frame = { count: 0, averageMs: 0, renderMs: 0, maxRenderMs: 0, fps: 0,
    drawCalls: 0, triangles: 0, adaptiveReductions: 0, hiddenPauses: 0 };
  let statsTime = 0, statsFrames = 0, slowFrames = 0;
  const emitStatus = (nextBackend, nextDetail) => {
    backend = nextBackend;
    detail = String(nextDetail).slice(0, 240);
    if (canvas?.dataset) canvas.dataset.backend = backend;
    try { onStatus({ backend, detail }); } catch { /* Host UI cannot break the engine. */ }
  };
  const emitCue = (active, force = false) => {
    if (!force && cueActive === active) return;
    cueActive = active;
    try { onCue({ type: 'illustrated-entry', active }); } catch { /* Host cue UI cannot break GPU rendering. */ }
  };
  const trackGeometry = (g) => { resources.geometry.add(g); return g; };
  const trackMaterial = (m) => { resources.material.add(m); return m; };
  const trackTexture = (t) => { resources.texture.add(t); return t; };
  const safelyDisposeRenderer = () => {
    if (!renderer || !initialized) return;
    try { renderer.dispose(); } catch { /* A lost device can reject disposal. */ }
    initialized = false;
  };
  const fail = (reason) => {
    if (disposed || failed) return;
    failed = true;
    emitCue(false, true);
    if (canvas?.style) canvas.style.visibility = 'hidden';
    emitStatus('Static', `Static artwork active. ${reason || 'GPU rendering is unavailable.'}`);
    safelyDisposeRenderer();
  };
  const contextLost = (event) => {
    event.preventDefault?.();
    fail('Graphics context lost; reload to try GPU again.');
  };
  const uncapturedError = (event) => {
    event.preventDefault?.();
    fail(`GPU runtime error: ${event.error?.message || 'device validation failed'}`);
  };

  const scene = new THREE.Scene();
  scene.name = 'Threshold / illustrative cinematic concepts';
  scene.userData = { illustrative: true, measuredCoverage: false, personTracking: false };
  const camera = new THREE.OrthographicCamera(-7, 7, 3.9, -3.9, 0.1, 100);
  camera.position.set(7.6, 5.8, 11.5);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();
  const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
  const up = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 1);
  const forward = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 2).negate();
  const tempPosition = new THREE.Vector3();
  const clock = TSL.uniform(0);
  const cosmicAlpha = TSL.uniform(1);
  const cosmicReveal = TSL.uniform(0);
  const cosmicCollapse = TSL.uniform(0);
  const waveAlpha = TSL.uniform(0);
  const wavePerson = TSL.uniform(new THREE.Vector2(0, 0));
  const portalWarmth = TSL.uniform(0);
  const leftMask = TSL.uniform(1);
  const portraitMask = TSL.uniform(0);
  const irisCenter = TSL.uniform(0.745);
  const curtainAlpha = TSL.uniform(0);
  const alertAmount = TSL.uniform(0);

  function mesh(geometry, material, parent, x = 0, y = 0, z = 0) {
    const object = new THREE.Mesh(geometry, material);
    object.position.set(x, y, z);
    parent.add(object);
    return object;
  }
  function group(name, parent = scene) {
    const object = new THREE.Group(); object.name = name; parent.add(object); return object;
  }
  function physical(color, roughness = 0.45, metalness = 0.3, extra = {}) {
    return trackMaterial(new THREE.MeshStandardMaterial({ color, roughness, metalness, ...extra }));
  }
  function luminous(color, opacity = 1) {
    return trackMaterial(new THREE.MeshBasicMaterial({ color, transparent: opacity < 1,
      opacity, toneMapped: false, depthWrite: opacity >= 1 }));
  }
  const boxGeometry = trackGeometry(new THREE.BoxGeometry(1, 1, 1));
  const planeGeometry = trackGeometry(new THREE.PlaneGeometry(1, 1));
  const sphereGeometry = trackGeometry(new THREE.SphereGeometry(1, 20, 12));
  const cylinderGeometry = trackGeometry(new THREE.CylinderGeometry(1, 1, 1, 14));
  const capsuleGeometry = trackGeometry(new THREE.CapsuleGeometry(1, 1, 4, 12));
  function box(parent, material, w, h, d, x, y, z) {
    const object = mesh(boxGeometry, material, parent, x, y, z);
    object.scale.set(w, h, d); return object;
  }
  function cylinder(parent, material, r, h, x, y, z) {
    const object = mesh(cylinderGeometry, material, parent, x, y, z);
    object.scale.set(r, h, r); return object;
  }
  function sphere(parent, material, r, x, y, z) {
    const object = mesh(sphereGeometry, material, parent, x, y, z);
    object.scale.setScalar(r); return object;
  }
  function roundedGeometry(w, h, d, radius = 0.04) {
    const r = Math.min(radius, w * 0.25, h * 0.25, d * 0.35);
    const x = -w / 2 + r, y = -h / 2 + r;
    const ww = w - r * 2, hh = h - r * 2;
    const shape = new THREE.Shape();
    shape.moveTo(x + r, y); shape.lineTo(x + ww - r, y);
    shape.quadraticCurveTo(x + ww, y, x + ww, y + r);
    shape.lineTo(x + ww, y + hh - r);
    shape.quadraticCurveTo(x + ww, y + hh, x + ww - r, y + hh);
    shape.lineTo(x + r, y + hh);
    shape.quadraticCurveTo(x, y + hh, x, y + hh - r);
    shape.lineTo(x, y + r); shape.quadraticCurveTo(x, y, x + r, y);
    const geometry = new THREE.ExtrudeGeometry(shape, { depth: d - r * 2, bevelEnabled: true,
      bevelThickness: r, bevelSize: r, bevelSegments: 3, curveSegments: 5, steps: 1 });
    geometry.translate(0, 0, -d / 2 + r);
    return trackGeometry(geometry);
  }
  function roundedBox(parent, material, w, h, d, x, y, z, r = 0.04) {
    return mesh(roundedGeometry(w, h, d, r), material, parent, x, y, z);
  }

  // Local procedural studio environment. No image fetch or third-party asset.
  function makeEnvironment() {
    const ew = 128, eh = 64;
    const data = new Float32Array(ew * eh * 4);
    for (let y = 0; y < eh; y++) {
      for (let x = 0; x < ew; x++) {
        const u = x / ew, v = y / eh;
        const softbox = Math.exp(-(((u - 0.19) / 0.065) ** 2) - ((v - 0.37) / 0.22) ** 8);
        const rim = Math.exp(-(((u - 0.73) / 0.028) ** 2) - ((v - 0.43) / 0.3) ** 8);
        const warm = Math.exp(-(((u - 0.47) / 0.09) ** 2) - ((v - 0.64) / 0.10) ** 2);
        const i = (y * ew + x) * 4;
        data[i] = 0.024 + softbox * 2.3 + rim * 1.7 + warm * 0.45;
        data[i + 1] = 0.035 + softbox * 2.65 + rim * 2.9 + warm * 0.24;
        data[i + 2] = 0.054 + softbox * 3.0 + rim * 4.0 + warm * 0.1;
        data[i + 3] = 1;
      }
    }
    const halfData = new Uint16Array(data.length);
    for (let i = 0; i < data.length; i++) halfData[i] = THREE.DataUtils.toHalfFloat(data[i]);
    const texture = trackTexture(new THREE.DataTexture(halfData, ew, eh, THREE.RGBAFormat, THREE.HalfFloatType));
    texture.minFilter = THREE.LinearFilter;
    texture.magFilter = THREE.LinearFilter;
    texture.generateMipmaps = false;
    texture.mapping = THREE.EquirectangularReflectionMapping;
    texture.colorSpace = THREE.LinearSRGBColorSpace;
    texture.needsUpdate = true;
    return texture;
  }

  const metal = physical(PALETTE.steel, 0.28, 0.83);
  const edgeMetal = physical(0x85929d, 0.24, 0.86);
  const darkMetal = physical(0x0d151e, 0.35, 0.72);
  const warmMetal = physical(0x897053, 0.3, 0.78);
  const plaster = physical(PALETTE.wall, 0.74, 0.1);
  const floorMaterial = physical(0x0c151e, 0.38, 0.48, { envMapIntensity: 0.25 });
  const panelMaterial = physical(0x14212b, 0.3, 0.53);
  const porcelain = physical(0xc3cdd1, 0.27, 0.28);
  const slotMaterial = physical(0x0a1018, 0.59, 0.2);
  const iceLight = luminous(PALETTE.ice);
  const whiteLight = luminous(PALETTE.white);
  const amberLight = luminous(PALETTE.amber);
  const traceMaterial = luminous(0x5e87a1, 0.36);
  const blueTint = new THREE.Color(PALETTE.ice);
  const warmTint = new THREE.Color(PALETTE.amber);
  const alertTint = new THREE.Color(0xff423d);
  const wallTint = new THREE.Color(PALETTE.wall);
  const keyTint = new THREE.Color(0xd6e8ff);

  // Analytic, bounded glow. No full-resolution blur passes or unbounded particles.
  function glowMaterial(color, opacity = 0.3, hardness = 4) {
    const material = trackMaterial(new THREE.MeshBasicNodeMaterial({ transparent: true,
      depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending, toneMapped: false }));
    const p = TSL.uv().sub(0.5).mul(2);
    material.colorNode = TSL.color(color);
    material.opacityNode = TSL.exp(p.dot(p).mul(-hardness)).mul(
      TSL.float(1).sub(TSL.smoothstep(0.65, 1, p.length()))).mul(opacity);
    return material;
  }
  const blueGlow = glowMaterial(0x6ebaff, 0.29);
  const faintBlueGlow = glowMaterial(0x77bded, 0.085, 2.5);
  const warmGlow = glowMaterial(0xeebb79, 0.24);
  function billboard(parent, material, w, h, x = 0, y = 0, z = 0) {
    const object = mesh(planeGeometry, material, parent, x, y, z);
    object.quaternion.copy(camera.quaternion); object.scale.set(w, h, 1);
    return object;
  }

  const cosmic = group('01 / fictional gravitational lens');
  const cosmicMaterial = trackMaterial(new THREE.MeshBasicNodeMaterial({
    transparent: true, depthWrite: false, depthTest: false, toneMapped: false,
  }));
  cosmicMaterial.colorNode = TSL.Fn(() => {
    const p = TSL.uv().sub(0.5).mul(TSL.vec2(2.6, 2.0)).toVar();
    const x = p.x.div(TSL.float(1).sub(cosmicCollapse.mul(0.86)));
    const y = p.y.add(p.x.mul(0.115));
    const r = TSL.vec2(x, y).length().max(0.001);
    const angle = TSL.atan(y, x);
    const t = clock.mul(0.2);
    const radius = TSL.float(0.325).add(cosmicReveal.mul(0.025));
    const outside = TSL.smoothstep(radius.sub(0.012), radius.add(0.003), r);
    const photon = TSL.exp(r.sub(radius.add(0.012)).abs().mul(-240));
    const halo = TSL.exp(r.sub(radius.add(0.08)).pow2().mul(-32)).mul(0.16);
    const lensRadius = r.add(TSL.sin(angle.mul(2)).mul(0.008));
    const upper = TSL.smoothstep(-0.13, 0.2, y);
    const lens = TSL.exp(lensRadius.sub(0.405).abs().mul(-90)).mul(upper.mul(1.1).add(0.11));
    const fineLens = TSL.exp(lensRadius.sub(0.444).abs().mul(-210)).mul(upper).mul(0.12);
    const dr = TSL.vec2(x, y.mul(4.65)).length();
    // Two fixed noise octaves shear the hot disk into filaments, not concentric hoops.
    const flow = TSL.vec3(x.mul(5).add(TSL.sin(angle.add(t)).mul(0.24)),
      y.mul(41).add(TSL.cos(angle.sub(t)).mul(0.3)), t.mul(0.17));
    const turbulence = TSL.mx_noise_float(flow).mul(0.75).add(
      TSL.mx_noise_float(flow.mul(2.35).add(TSL.vec3(7.1, 3.2, 1.7))).mul(0.25));
    const diskBand = TSL.exp(dr.sub(0.63).pow2().mul(-13));
    const innerDisk = TSL.smoothstep(0.37, 0.51, dr);
    const spiral = TSL.sin(dr.mul(93).sub(angle.mul(7)).sub(t).add(turbulence.mul(9))).mul(
      TSL.sin(dr.mul(137).add(angle.mul(11)).add(t.mul(1.3)).add(turbulence.mul(6)))).mul(0.31).add(0.53);
    const broadStreak = turbulence.mul(0.42).add(0.68).mul(
      TSL.sin(dr.mul(33).add(angle.mul(3)).sub(t.mul(0.7))).mul(0.18).add(0.82));
    const beaming = TSL.float(1).sub(TSL.smoothstep(-0.9, 0.8, x)).mul(1.25).add(0.45);
    const cell = TSL.uv().mul(TSL.vec2(840, 590)).floor();
    const granular = TSL.fract(TSL.sin(cell.dot(TSL.vec2(127.1, 311.7))).mul(43758.5453));
    const disk = diskBand.mul(innerDisk).mul(spiral).mul(broadStreak).mul(beaming).mul(granular.mul(0.78).add(0.34));
    const fineOrbit = TSL.exp(dr.sub(0.98).abs().mul(-110)).mul(0.07);
    const jet = TSL.exp(y.abs().mul(-140)).mul(TSL.exp(x.abs().mul(-1.5))).mul(0.22);
    const heat = TSL.smoothstep(-0.2, 0.28, y.negate()).mul(0.24);
    const diskColor = TSL.mix(TSL.vec3(0.44, 0.73, 1.0), TSL.vec3(1.0, 0.66, 0.34), heat);
    const light = diskColor.mul(disk.mul(1.22).add(fineOrbit).add(jet));
    const arcs = TSL.vec3(0.63, 0.83, 1.0).mul(photon.mul(1.6).add(lens).add(fineLens));
    const haze = TSL.vec3(0.16, 0.34, 0.58).mul(halo);
    const vignette = TSL.float(1).sub(TSL.smoothstep(0.8, 1.35, r));
    return light.add(arcs).add(haze).mul(outside).mul(vignette).mul(cosmicAlpha);
  })();
  const cosmicUV = TSL.uv().sub(0.5).mul(TSL.vec2(2.6, 2.0));
  cosmicMaterial.opacityNode = TSL.float(1).sub(TSL.smoothstep(0.74, 1.30, cosmicUV.length())).mul(cosmicAlpha);
  const cosmicPlane = billboard(cosmic, cosmicMaterial, 9, 7);
  cosmicPlane.renderOrder = -10;
  const slitMaterial = trackMaterial(new THREE.MeshBasicNodeMaterial({ transparent: true,
    depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending, toneMapped: false }));
  const slitUV = TSL.uv().sub(0.5).abs();
  slitMaterial.colorNode = TSL.color(PALETTE.white);
  slitMaterial.opacityNode = TSL.exp(slitUV.x.mul(-245)).mul(
    TSL.float(1).sub(TSL.smoothstep(0.22, 0.29, slitUV.y))).mul(cosmicCollapse).mul(0.48);
  const thresholdSlit = billboard(cosmic, slitMaterial, 9, 7);
  thresholdSlit.renderOrder = -9;

  // A finite, GPU-instanced depth field. No per-frame instance writes or allocations.
  const starField = group('ambient depth / 192 local luminous fragments');
  const starActivity = TSL.uniform(0.56);
  const starMaterial = glowMaterial(0x8dbbe0, 0.62, 7);
  starMaterial.opacityNode = starMaterial.opacityNode.mul(starActivity);
  const starGeometry = trackGeometry(planeGeometry.clone());
  const starSeeds = new Float32Array(192 * 4);
  let randomState = 0x74687273;
  function random() {
    randomState = (Math.imul(randomState, 1664525) + 1013904223) >>> 0;
    return randomState / 4294967296;
  }
  const stars = new THREE.InstancedMesh(starGeometry, starMaterial, 192);
  stars.frustumCulled = false;
  const dummy = new THREE.Object3D();
  for (let i = 0; i < 192; i++) {
    const x = (random() - 0.46) * 15;
    const y = (random() - 0.5) * 9;
    const z = -2.5 - random() * 5;
    const size = 0.015 + random() ** 3 * 0.052;
    dummy.position.set(x, y, z);
    dummy.rotation.set(0, 0, random() * Math.PI);
    dummy.scale.set(size, size * (random() > 0.89 ? 3.5 : 1), 1);
    dummy.updateMatrix(); stars.setMatrixAt(i, dummy.matrix);
    starSeeds.set([random() * 6.283, random(), random(), random()], i * 4);
  }
  starGeometry.setAttribute('instanceSeed', new THREE.InstancedBufferAttribute(starSeeds, 4));
  const starSeed = TSL.attribute('instanceSeed', 'vec4');
  starMaterial.opacityNode = starMaterial.opacityNode.mul(
    TSL.sin(clock.mul(0.30).add(starSeed.x)).mul(0.18).add(0.82));
  stars.instanceMatrix.needsUpdate = true; starField.add(stars);
  starField.quaternion.copy(camera.quaternion);
  stars.renderOrder = -20;

  const ambient = group('ambient / low visual activity');
  const ambientPlane = billboard(ambient, faintBlueGlow, 8, 7);

  const roomRig = group('02 / entrance cutaway — illustrative');
  const room = group('architecture', roomRig);
  const slab = roundedBox(room, floorMaterial, 5.7, 4.55, 0.15, 0, -0.13, -0.15, 0.05);
  slab.rotation.x = -Math.PI / 2;
  box(room, darkMetal, 5.55, 0.075, 4.4, 0, -0.19, -0.15);
  box(room, edgeMetal, 5.55, 0.018, 0.018, 0, -0.055, 2.03);
  box(room, warmMetal, 0.023, 0.018, 4.4, 2.78, -0.055, -0.15);
  // Selective wall sections expose the room instead of enclosing it.
  box(room, plaster, 0.16, 2.98, 3.9, -2.6, 1.49, -0.22);
  box(room, darkMetal, 0.2, 0.09, 3.9, -2.6, 0.07, -0.22);
  box(room, plaster, 5.2, 0.64, 0.16, 0, 0.32, -2.12);
  box(room, edgeMetal, 5.23, 0.025, 0.19, 0, 0.652, -2.12);
  // Dark inset seams make the architectural scale legible, not a coverage grid.
  for (const x of [-1.8, -0.6, 0.6, 1.8]) box(room, slotMaterial, 0.009, 0.006, 4.16, x, -0.047, -0.12);
  for (const z of [-1.5, -0.3, 0.9]) box(room, slotMaterial, 5.24, 0.006, 0.009, 0, -0.046, z);

  const entrance = group('precision doorway', room);
  entrance.position.z = 0.57;
  box(entrance, plaster, 0.55, 3.25, 0.31, -1.23, 1.625, 0);
  box(entrance, plaster, 0.58, 3.25, 0.31, 1.22, 1.625, 0);
  box(entrance, plaster, 2.99, 0.23, 0.31, 0, 3.14, 0);
  box(entrance, metal, 0.125, 3.03, 0.37, -0.918, 1.515, 0.025);
  box(entrance, metal, 0.125, 3.03, 0.37, 0.918, 1.515, 0.025);
  box(entrance, metal, 1.96, 0.115, 0.37, 0, 3, 0.025);
  box(entrance, warmMetal, 1.83, 0.045, 0.42, 0, 0.017, 0.07);
  box(entrance, edgeMetal, 0.012, 2.92, 0.017, 0.846, 1.47, 0.217);
  box(entrance, edgeMetal, 0.012, 2.92, 0.017, -0.846, 1.47, 0.217);
  box(entrance, traceMaterial, 1.68, 0.015, 0.018, 0, 2.938, 0.22);
  const doorPivot = group('door / contact state illustration', entrance);
  doorPivot.position.set(-0.84, 0, 0.04);
  const doorWidth = 1.675;
  roundedBox(doorPivot, panelMaterial, doorWidth, 2.875, 0.095, doorWidth / 2, 1.475, 0, 0.015);
  for (const x of [0.028, doorWidth - 0.028]) box(doorPivot, edgeMetal, 0.025, 2.855, 0.106, x, 1.475, 0);
  for (const y of [0.05, 2.89]) box(doorPivot, metal, doorWidth, 0.035, 0.108, doorWidth / 2, y, 0);
  box(doorPivot, metal, 1.41, 0.008, 0.007, 0.84, 0.68, 0.052);
  box(doorPivot, metal, 1.41, 0.008, 0.007, 0.84, 2.22, 0.052);
  for (const y of [0.43, 2.54]) cylinder(entrance, edgeMetal, 0.04, 0.18, -0.85, y, 0.13);
  roundedBox(doorPivot, darkMetal, 0.16, 0.39, 0.035, 1.46, 1.36, 0.067, 0.01);
  cylinder(doorPivot, edgeMetal, 0.027, 0.30, 1.46, 1.36, 0.15);
  for (const y of [1.24, 1.48]) {
    const mount = cylinder(doorPivot, edgeMetal, 0.018, 0.09, 1.46, y, 0.105);
    mount.rotation.x = Math.PI / 2;
  }
  // A contact pair with real casing geometry, screw recesses, seams and an LED.
  const fixedSensor = group('fixed contact housing', entrance);
  fixedSensor.position.set(1.03, 2.50, 0.224);
  roundedBox(fixedSensor, porcelain, 0.205, 0.405, 0.1, 0, 0, 0, 0.024);
  roundedBox(fixedSensor, darkMetal, 0.207, 0.41, 0.014, 0, 0, -0.049, 0.004);
  box(fixedSensor, slotMaterial, 0.13, 0.005, 0.004, 0, -0.108, 0.052);
  const sensorLED = box(fixedSensor, iceLight, 0.016, 0.047, 0.006, 0, 0.078, 0.054);
  for (const y of [-0.16, 0.16]) {
    const screw = cylinder(fixedSensor, metal, 0.013, 0.005, 0, y, 0.052);
    screw.rotation.x = Math.PI / 2;
    box(fixedSensor, slotMaterial, 0.015, 0.003, 0.001, 0, y, 0.056);
  }
  const movingSensor = group('door contact magnet', doorPivot);
  movingSensor.position.set(1.60, 2.50, 0.094);
  roundedBox(movingSensor, porcelain, 0.105, 0.31, 0.075, 0, 0, 0, 0.018);
  box(movingSensor, slotMaterial, 0.05, 0.004, 0.002, 0, -0.093, 0.039);
  const sensorHalo = billboard(entrance, blueGlow, 0.65, 0.7, 1.025, 2.50, 0.28);

  const floorGlowMaterial = trackMaterial(new THREE.MeshBasicNodeMaterial({ transparent: true,
    depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false, side: THREE.DoubleSide }));
  floorGlowMaterial.colorNode = TSL.mix(
    TSL.mix(TSL.vec3(0.24, 0.57, 0.95), TSL.vec3(0.95, 0.51, 0.17), portalWarmth),
    TSL.vec3(1.0, 0.035, 0.018), alertAmount);
  const floorUV = TSL.uv().sub(0.5).mul(TSL.vec2(5.5, 4.3));
  const reflection = TSL.exp(floorUV.x.pow2().mul(-1.4)).mul(TSL.exp(floorUV.y.sub(0.7).abs().mul(-0.65)));
  const reflectionStrands = TSL.sin(floorUV.x.mul(43)).mul(0.10).add(0.9);
  floorGlowMaterial.opacityNode = reflection.mul(reflectionStrands).mul(0.17);
  const floorGlow = mesh(planeGeometry, floorGlowMaterial, room, 0, -0.036, -0.15);
  floorGlow.scale.set(5.5, 4.3, 1); floorGlow.rotation.x = -Math.PI / 2;

  const figureMaterial = physical(0x8eacba, 0.36, 0.42, { transparent: true, opacity: 1 });
  const figure = group('abstract person / illustrative, not tracked', room);
  function capsule(parent, material, r, totalHeight, x, y, z, flat = 1) {
    const object = mesh(capsuleGeometry, material, parent, x, y, z);
    object.scale.set(r, totalHeight / 3, r * flat); return object;
  }
  const torso = capsule(figure, figureMaterial, 0.21, 0.58, 0, 1.11, 0, 0.61);
  sphere(figure, figureMaterial, 0.125, 0, 1.59, 0);
  cylinder(figure, figureMaterial, 0.055, 0.10, 0, 1.43, 0);
  const hips = capsule(figure, figureMaterial, 0.12, 0.38, 0, 0.79, 0, 0.8);
  hips.rotation.z = Math.PI / 2;
  const leftLeg = group('left leg', figure); leftLeg.position.set(-0.102, 0.75, 0);
  const rightLeg = group('right leg', figure); rightLeg.position.set(0.102, 0.75, 0);
  for (const leg of [leftLeg, rightLeg]) {
    capsule(leg, figureMaterial, 0.078, 0.7, 0, -0.35, 0, 0.94);
    roundedBox(leg, figureMaterial, 0.13, 0.085, 0.23, 0, -0.707, 0.028, 0.017);
  }
  for (const sign of [-1, 1]) {
    const arm = capsule(figure, figureMaterial, 0.063, 0.59, sign * 0.27, 1.02, 0, 0.9);
    arm.rotation.z = sign * 0.13;
  }
  const contactShadowMaterial = glowMaterial(0x07111b, 0.68, 3);
  contactShadowMaterial.blending = THREE.NormalBlending;
  const contactShadow = mesh(planeGeometry, contactShadowMaterial, room, 0, -0.031, 0);
  contactShadow.rotation.x = -Math.PI / 2; contactShadow.scale.set(0.95, 0.7, 1);

  // Two stylized radio boards. Geometry is a concept, not a hardware schematic.
  const boardMaterial = physical(0x1b3639, 0.63, 0.23);
  const chipMaterial = physical(0x101719, 0.46, 0.47);
  const pinMaterial = physical(0x99866a, 0.29, 0.8);
  const boards = group('radio pair / illustrative', room);
  function board(x, z, yaw) {
    const root = group('radio board', boards); root.position.set(x, 0.5, z); root.rotation.y = yaw;
    roundedBox(root, boardMaterial, 0.28, 0.43, 0.025, 0, 0, 0, 0.008);
    box(root, edgeMetal, 0.16, 0.18, 0.035, 0, 0.025, 0.027);
    box(root, chipMaterial, 0.07, 0.07, 0.025, 0, -0.115, 0.025);
    roundedBox(root, metal, 0.105, 0.06, 0.065, 0, -0.2, 0.026, 0.008);
    for (let i = 0; i < 9; i++) {
      for (const sign of [-1, 1]) box(root, pinMaterial, 0.045, 0.009, 0.012, sign * 0.145, -0.15 + i * 0.037, 0.012);
    }
    for (let i = 0; i < 4; i++) box(root, pinMaterial, 0.15, 0.006, 0.002, 0, 0.133 + i * 0.018, 0.015);
    box(root, iceLight, 0.012, 0.012, 0.009, 0.087, -0.092, 0.019);
    box(root, darkMetal, 0.07, 0.30, 0.075, 0, -0.34, -0.028);
    box(root, metal, 0.35, 0.035, 0.29, 0, -0.482, -0.025);
    billboard(root, blueGlow, 1.25, 1.25, 0, 0, -0.06);
    return root;
  }
  board(-2.12, -1.6, 0.35); board(2.0, 0.99, -0.7);
  const field = group('wireless activity field / diagram, not RF coverage', room);
  const fieldMaterial = trackMaterial(new THREE.MeshBasicNodeMaterial({ transparent: true,
    depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, toneMapped: false }));
  const fieldNode = TSL.Fn(() => {
    const p = TSL.uv().sub(0.5).mul(TSL.vec2(5.2, 4.1));
    const fromTX = p.sub(TSL.vec2(-2.12, -1.45)).length();
    const fromRX = p.sub(TSL.vec2(2.0, 1.14)).length();
    const disturbance = TSL.exp(p.sub(wavePerson).dot(p.sub(wavePerson)).mul(-1.6));
    const phase = fromTX.mul(7.2).sub(clock.mul(0.8)).add(disturbance.mul(1.4));
    const wave = TSL.smoothstep(0.73, 1, TSL.sin(phase)).pow2();
    const echo = TSL.smoothstep(0.84, 1, TSL.sin(fromRX.mul(6.8).add(clock.mul(0.5)))).pow2().mul(0.14);
    const edgeUV = TSL.uv().sub(0.5).abs();
    const edge = TSL.float(1).sub(TSL.smoothstep(0.38, 0.50, TSL.max(edgeUV.x, edgeUV.y)));
    return TSL.vec4(TSL.mix(
      TSL.mix(TSL.vec3(0.24, 0.63, 1), TSL.vec3(1, 0.59, 0.25), disturbance.mul(0.38)),
      TSL.vec3(1, 0.035, 0.018), alertAmount),
      wave.add(echo).mul(edge).mul(waveAlpha).mul(0.25));
  })();
  fieldMaterial.colorNode = fieldNode.rgb;
  fieldMaterial.opacityNode = fieldNode.a;
  for (const y of [0.10, 0.70, 1.30]) {
    const surface = mesh(planeGeometry, fieldMaterial, field, 0, y, -0.15);
    surface.scale.set(5.2, 4.1, 1); surface.rotation.x = -Math.PI / 2;
  }

  const emberRoot = group('illustrated event / finite red light fragments', room);
  const emberMaterial = glowMaterial(0xff483a, 0.9, 6);
  emberMaterial.opacityNode = emberMaterial.opacityNode.mul(alertAmount);
  const emberGeometry = trackGeometry(planeGeometry.clone());
  const emberSeeds = new Float32Array(48 * 4);
  const emberMesh = new THREE.InstancedMesh(emberGeometry, emberMaterial, 48);
  emberMesh.frustumCulled = false;
  for (let i = 0; i < 48; i++) {
    dummy.position.set((random() - 0.5) * 2.4, 0.18 + random() * 3, 0.4 + random() * 1.0);
    dummy.quaternion.copy(camera.quaternion);
    const size = 0.022 + random() * 0.026;
    dummy.scale.set(size, size * (1.5 + random() * 2), 1);
    dummy.updateMatrix(); emberMesh.setMatrixAt(i, dummy.matrix);
    emberSeeds.set([random() * Math.PI * 2, random(), random(), random()], i * 4);
  }
  emberGeometry.setAttribute('instanceSeed', new THREE.InstancedBufferAttribute(emberSeeds, 4));
  const emberSeed = TSL.attribute('instanceSeed', 'vec4');
  emberMaterial.positionNode = TSL.positionLocal.add(TSL.vec3(
    TSL.sin(clock.mul(0.6).add(emberSeed.x)).mul(2),
    TSL.sin(clock.mul(0.48).add(emberSeed.x)).mul(3.8), 0));
  emberMesh.instanceMatrix.needsUpdate = true; emberRoot.add(emberMesh);
  const eventGlowMaterial = glowMaterial(0xff362d, 0.35, 3.2);
  eventGlowMaterial.opacityNode = eventGlowMaterial.opacityNode.mul(alertAmount);
  const eventGlow = billboard(room, eventGlowMaterial, 6.5, 6, 0, 1.3, 0.2);
  eventGlow.renderOrder = 8;

  const futureRing = group('future / quiet amber horizon', room);
  const ringGeometry = trackGeometry(new THREE.TorusGeometry(1, 0.008, 4, 80));
  const futureRingMesh = mesh(ringGeometry, luminous(PALETTE.amber, 0.45), futureRing, 0, 0.005, 0);
  futureRingMesh.rotation.x = -Math.PI / 2;
  futureRingMesh.scale.set(1.75, 1.1, 1);
  const futureGlow = mesh(planeGeometry, warmGlow, futureRing, 0.5, -0.028, 0.2);
  futureGlow.rotation.x = -Math.PI / 2; futureGlow.scale.set(4, 3, 1);

  const portalRig = group('03 / threshold portal');
  const portal = group('portal architecture', portalRig);
  const portalSlab = roundedBox(portal, floorMaterial, 5.8, 5.7, 0.13, 0, -0.13, -0.50, 0.04);
  portalSlab.rotation.x = -Math.PI / 2;
  for (let i = 0; i < 3; i++) {
    const inset = i * 0.07, z = -i * 0.18;
    const mat = i === 0 ? metal : darkMetal;
    box(portal, mat, 0.16, 3.7 - inset, 0.21, -1.02 + inset * 0.5, 1.85 - inset * 0.5, z);
    box(portal, mat, 0.16, 3.7 - inset, 0.21, 1.02 - inset * 0.5, 1.85 - inset * 0.5, z);
    box(portal, mat, 2.2 - inset, 0.16, 0.21, 0, 3.65 - inset, z);
  }
  for (const sign of [-1, 1]) {
    box(portal, whiteLight, 0.016, 3.46, 0.028, sign * 0.922, 1.82, 0.12);
    box(portal, warmMetal, 0.027, 3.65, 0.025, sign * 1.108, 1.83, 0.12);
    const halo = mesh(planeGeometry, blueGlow, portal, sign * 0.91, 1.83, 0.133);
    halo.scale.set(0.72, 4.9, 1);
  }
  box(portal, whiteLight, 1.85, 0.016, 0.025, 0, 3.544, 0.13);
  box(portal, edgeMetal, 2.23, 0.04, 0.36, 0, 0.014, 0.06);
  const portalSurfaceMaterial = trackMaterial(new THREE.MeshBasicNodeMaterial({ transparent: true,
    side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false }));
  const portalP = TSL.uv().sub(0.5).mul(2);
  const portalEdge = TSL.float(1).sub(TSL.max(portalP.x.abs(), portalP.y.abs()));
  const portalHaze = TSL.exp(portalEdge.mul(-11)).mul(0.45).add(
    TSL.exp(portalP.x.pow2().mul(-3)).mul(TSL.uv().y.oneMinus()).mul(0.055));
  const portalStrands = TSL.sin(portalP.x.mul(37).add(TSL.sin(portalP.y.mul(6).add(clock.mul(0.10))))).abs().pow(6).mul(0.06).add(0.94);
  portalSurfaceMaterial.colorNode = TSL.vec3(0.24, 0.57, 0.94);
  portalSurfaceMaterial.opacityNode = portalHaze.mul(portalStrands).mul(TSL.smoothstep(0, 0.035, portalEdge));
  const portalSurface = mesh(planeGeometry, portalSurfaceMaterial, portal, 0, 1.8, 0.11);
  portalSurface.scale.set(1.82, 3.49, 1);
  const portalFloorGlow = mesh(planeGeometry, floorGlowMaterial, portal, 0, -0.03, 0.55);
  portalFloorGlow.rotation.x = -Math.PI / 2; portalFloorGlow.scale.set(5.5, 4.3, 1);
  const portalBackGlow = billboard(portal, faintBlueGlow, 6, 7, 0, 2.1, -0.6);

  // A camera-space mask keeps the left 43% quiet, even in the contact macro shot.
  const maskMaterial = trackMaterial(new THREE.MeshBasicNodeMaterial({ transparent: true,
    depthTest: false, depthWrite: false, toneMapped: false }));
  const maskUV = TSL.uv();
  maskMaterial.colorNode = TSL.color(PALETTE.void);
  const portraitOcclusion = TSL.max(TSL.smoothstep(0.50, 0.67, maskUV.y),
    TSL.float(1).sub(TSL.smoothstep(0.10, 0.20, maskUV.y))).mul(portraitMask);
  maskMaterial.opacityNode = TSL.max(
    TSL.float(1).sub(TSL.smoothstep(0.39, 0.59, maskUV.x)).mul(leftMask), portraitOcclusion);
  const mask = mesh(planeGeometry, maskMaterial, scene);
  mask.quaternion.copy(camera.quaternion); mask.renderOrder = 990;
  const curtainMaterial = trackMaterial(new THREE.MeshBasicNodeMaterial({ transparent: true,
    depthTest: false, depthWrite: false, toneMapped: false }));
  const irisDistance = TSL.uv().x.sub(irisCenter).abs();
  const apertureWidth = TSL.mix(0.79, 0.003, curtainAlpha);
  const apertureEdge = TSL.exp(irisDistance.sub(apertureWidth).abs().mul(-260)).mul(curtainAlpha).mul(0.11);
  curtainMaterial.colorNode = TSL.color(PALETTE.void).add(TSL.vec3(0.45, 0.70, 1).mul(apertureEdge));
  curtainMaterial.opacityNode = TSL.smoothstep(apertureWidth.sub(0.007), apertureWidth.add(0.01), irisDistance)
    .mul(TSL.smoothstep(0, 0.25, curtainAlpha));
  const curtain = mesh(planeGeometry, curtainMaterial, scene);
  curtain.quaternion.copy(camera.quaternion); curtain.renderOrder = 999;

  scene.add(new THREE.HemisphereLight(0xb6d5f5, 0x19222f, 1.8));
  const keyLight = new THREE.DirectionalLight(0xd6e8ff, 3.0);
  keyLight.position.set(1, 7, 5); scene.add(keyLight);
  const rimLight = new THREE.DirectionalLight(0x85bbec, 2.8);
  rimLight.position.set(-4, 4, -6); scene.add(rimLight);
  const warmLight = new THREE.DirectionalLight(0xe9b176, 1.0);
  warmLight.position.set(6, 2, -3); scene.add(warmLight);
  const roomLight = new THREE.PointLight(0x86c6ff, 9, 7, 2);
  roomLight.position.set(0.4, 2.8, 0.6); room.add(roomLight);

  function place(object, horizontal, vertical) {
    object.position.copy(right).multiplyScalar((camera.right - camera.left) * horizontal);
    object.position.addScaledVector(up, vertical);
  }
  function capturePose() {
    const capture = (object) => ({ position: object.position.clone(), scale: object.scale.clone(), quaternion: object.quaternion.clone() });
    return { roomRig: capture(roomRig), room: capture(room), cosmic: capture(cosmic), portalRig: capture(portalRig),
      figure: capture(figure), figureVisible: figure.visible, doorAngle: doorPivot.rotation.y };
  }
  function blendPose(object, pose, amount) {
    object.position.lerpVectors(pose.position, object.position, amount);
    object.scale.lerpVectors(pose.scale, object.scale, amount);
    object.quaternion.slerpQuaternions(pose.quaternion, object.quaternion, amount);
  }
  function updatePose() {
    const activeTransition = !motionReduced && transition < 1;
    displayChapter = activeTransition && !linkedTransition && transition < 0.45 ? priorChapter : chapter;
    const visual = displayChapter;
    const sameRoom = linkedTransition && isRoomChapter(visual) && isRoomChapter(priorChapter);
    const sameCosmic = linkedTransition && isCosmicChapter(visual) && isCosmicChapter(priorChapter);
    const narrow = width / height < 1.1;
    const compactScale = narrow ? clamp(width / height / 0.98, 0.28, 0.9) : 1;
    const anchor = narrow ? 0 : 0.245;
    const vertical = narrow ? -1.05 : -0.02;
    cosmic.visible = visual <= 2;
    roomRig.visible = visual >= 3 && visual <= 5 || visual === 8;
    ambient.visible = visual === 6 || visual === 10;
    portalRig.visible = visual === 7 || visual === 9;
    boards.visible = visual === 5;
    figure.visible = visual === 4 || visual === 5 || visual === 8;
    futureRing.visible = visual === 8;
    const t = motionReduced ? 30 : visual === chapter ? Math.max(0, chapterTime - (linkedTransition ? 0 : transitionDuration * 0.45)) : priorTime + chapterTime;
    const walkTime = visual === 4 ? (motionReduced ? crossingAt + 0.9 : t % 8) : t;
    const eventActive = !failed && !disposed && chapter === 4 && visual === 4 && walkTime >= crossingAt && walkTime < crossingAt + 2;
    alertAmount.value = chapter !== 4 || visual !== 4 ? 0 : motionReduced ? 0.92 :
      ease((walkTime - crossingAt) / 0.16) * (1 - ease((walkTime - crossingAt - 1.6) / 0.4));
    emitCue(eventActive);
    field.visible = visual === 5 || visual === 4 && alertAmount.value > 0.005;
    emberRoot.visible = visual === 4 && alertAmount.value > 0.005;
    eventGlow.visible = emberRoot.visible;
    const settle = ease(t / 1.6);
    leftMask.value = narrow || visual === 1 ? 0 : visual === 9 ? 0.18 : 1;
    portraitMask.value = narrow && visual !== 1 ? 1 : 0;
    irisCenter.value = narrow || visual === 1 ? 0.5 : 0.745;
    portalWarmth.value = visual === 8 ? 0.84 : 0;
    cosmicAlpha.value = visual === 2 ? 0.28 : 1;
    cosmicReveal.value = visual === 1 ? settle : 0;
    cosmicCollapse.value = visual === 2 ? ease(t / 2.2) : 0;
    place(cosmic, visual === 1 ? 0 : anchor + (visual === 2 ? 0.04 : 0), visual === 1 && narrow ? 0 : vertical + 0.05);
    const revealScale = visual === 1 ? 0.72 + settle * 0.66 : visual === 2 ? 0.90 : 1;
    const breathe = motionReduced ? 1 : 1 + Math.sin(activeTime * 0.22) * 0.009;
    cosmic.scale.setScalar(revealScale * (narrow ? clamp(width / height * 1.3, 0.38, 0.95) : 1) * breathe);
    place(starField, narrow || visual === 1 ? 0 : 0.15, 0);
    starField.quaternion.copy(camera.quaternion);
    if (!motionReduced) starField.rotateZ(Math.sin(activeTime * 0.06) * 0.028);
    starField.position.addScaledVector(up, motionReduced ? 0 : Math.sin(activeTime * 0.10) * 0.07);
    starActivity.value = visual === 6 || visual === 10 ? 0.24 : visual >= 3 && visual <= 5 || visual === 8 ? 0.34 : 0.70;
    place(ambient, anchor, -0.5);
    ambientPlane.scale.set(8 * breathe, 7 * breathe, 1);
    place(roomRig, anchor, visual === 3 ? vertical + 0.15 : vertical - 0.25);
    roomRig.scale.setScalar((visual === 3 ? (narrow ? 1.95 : 2.6) : visual === 5 ? 0.99 : 1.02) * compactScale);
    roomRig.rotation.set(0, visual === 5 ? -0.055 : 0, 0);
    if (visual === 3) room.position.set(-0.82, -2.37, -0.78);
    else room.position.set(0, -1.28, 0);
    const contactOpen = ease((t - 0.65) / 2.2);
    doorPivot.rotation.y = visual === 3 ? -0.32 * contactOpen : -1.16;
    sensorLED.material = visual === 3 && contactOpen > 0.6 ? amberLight : iceLight;
    sensorHalo.visible = visual === 3;
    if (visual === 4 || visual === 5) {
      const progress = ease((walkTime - 0.65) / 4.2);
      figure.position.set(0.14, 0, -1.35 + progress * 2.95);
      figure.rotation.set(0, 0, 0);
      const walking = !motionReduced && walkTime > 0.65 && walkTime < 4.85;
      leftLeg.rotation.x = walking ? Math.sin(walkTime * 4.2) * 0.20 : 0;
      rightLeg.rotation.x = -leftLeg.rotation.x;
      figureMaterial.opacity = visual === 4 && !motionReduced ?
        ease(walkTime / 0.5) * (1 - ease((walkTime - 6.5) / 1.1)) : 1;
    } else if (visual === 8) {
      // One calm conceptual pose change, not an alarm, tracking result, or medical claim.
      const repose = ease((t - 0.9) / 2.7);
      figure.position.set(-0.43, repose * 0.17, 1.38);
      figure.rotation.set(0, 0.13 * (1 - repose), -Math.PI * 0.49 * repose);
      leftLeg.rotation.x = 0; rightLeg.rotation.x = 0;
      figureMaterial.opacity = 1;
    }
    figureMaterial.color.copy(visual === 8 ? warmTint : blueTint).lerp(alertTint, alertAmount.value * 0.62).multiplyScalar(0.60);
    plaster.color.copy(wallTint).lerp(alertTint, alertAmount.value * 0.075);
    keyLight.color.copy(keyTint).lerp(alertTint, alertAmount.value * 0.48);
    contactShadow.position.x = figure.position.x + (visual === 8 ? 0.7 : 0);
    contactShadow.position.z = figure.position.z;
    contactShadow.scale.set(visual === 8 ? 2.1 : 0.95, visual === 8 ? 0.9 : 0.7, 1);
    contactShadow.visible = figure.visible;
    wavePerson.value.set(figure.position.x, figure.position.z + 0.15);
    waveAlpha.value = visual === 5 ? 0.78 : visual === 4 ? alertAmount.value * 0.65 : 0;
    futureRing.position.z = 1.13;
    roomLight.color.copy(visual === 8 ? warmTint : blueTint).lerp(alertTint, alertAmount.value);
    roomLight.intensity = visual === 8 ? 12 : visual === 3 ? 7 : 9 + alertAmount.value * 8;
    roomLight.position.x = 0.4 + (motionReduced ? 0 : Math.sin(activeTime * 0.26) * 0.30);
    roomLight.position.z = 0.6 + (motionReduced ? 0 : Math.cos(activeTime * 0.21) * 0.15);
    place(portalRig, visual === 9 ? (narrow ? 0.02 : 0.16) : anchor, vertical - 0.05);
    portalRig.scale.setScalar((visual === 9 ? 1.04 : 0.98) * compactScale);
    portalRig.rotation.set(0, 0, 0);
    portal.position.set(0, -1.7, 0);
    portal.rotation.y = visual === 9 ? -0.16 : 0.07;
    // Connected camera-space moves for the same architecture; an iris hides family changes.
    if (activeTransition && transitionPose) {
      const amount = ease(transition);
      if (sameRoom) {
        blendPose(roomRig, transitionPose.roomRig, amount);
        blendPose(room, transitionPose.room, amount);
        doorPivot.rotation.y = THREE.MathUtils.lerp(transitionPose.doorAngle, doorPivot.rotation.y, amount);
        blendPose(figure, transitionPose.figure, amount);
        if (!transitionPose.figureVisible) figureMaterial.opacity *= ease((transition - 0.2) / 0.6);
        if (visual === 5) waveAlpha.value *= ease(transition);
      } else if (sameCosmic) {
        blendPose(cosmic, transitionPose.cosmic, amount);
        if (visual === 1) {
          cosmic.scale.multiplyScalar(1 - Math.sin(Math.PI * transition) * 0.20);
          cosmicCollapse.value = Math.max(cosmicCollapse.value, Math.sin(Math.PI * transition) * 0.28);
        }
      } else {
        const entering = transition >= 0.45;
        const depth = entering ? 1 - ease((transition - 0.45) / 0.55) : ease(transition / 0.45);
        const object = roomRig.visible ? roomRig : portalRig.visible ? portalRig : cosmic.visible ? cosmic : ambient;
        object.scale.multiplyScalar(entering ? 1 - depth * 0.13 : 1 + depth * 0.17);
        object.position.addScaledVector(forward, entering ? -depth * 0.6 : depth * 0.45);
        if (object === roomRig || object === portalRig) object.rotation.y += (entering ? -1 : 1) * depth * 0.035;
      }
    }
    curtainAlpha.value = activeTransition && !linkedTransition ?
      Math.min(ease(transition / 0.43), 1 - ease((transition - 0.47) / 0.53)) : 0;
  }

  function resize() {
    if (disposed || failed || !canvas) return;
    try {
      const bounds = canvas.getBoundingClientRect();
      width = Math.max(1, Math.round(bounds.width || canvas.clientWidth || window.innerWidth));
      height = Math.max(1, Math.round(bounds.height || canvas.clientHeight || window.innerHeight));
      const aspect = width / height;
      const halfHeight = 3.9;
      camera.left = -halfHeight * aspect; camera.right = halfHeight * aspect;
      camera.top = halfHeight; camera.bottom = -halfHeight; camera.updateProjectionMatrix();
      const preset = QUALITY[qualityMode];
      pixelRatio = Math.min(window.devicePixelRatio || 1, preset.dpr,
        Math.sqrt(preset.pixels / (width * height))) * dynamicScale;
      pixelRatio = Math.max(Number.EPSILON, pixelRatio);
      if (renderer && initialized) {
        renderer.setPixelRatio(pixelRatio);
        renderer.setSize(width, height, false);
      }
      for (const plane of [mask, curtain]) {
        plane.position.copy(camera.position).addScaledVector(forward, 1);
        plane.scale.set(halfHeight * 2 * aspect, halfHeight * 2, 1);
      }
      updatePose(); dirty = true;
    } catch (error) { fail(`Resize failed: ${error?.message || error}`); }
  }
  function setChapter(index, { immediate = false, from, duration } = {}) {
    if (disposed) return;
    const next = Number(index);
    if (!Number.isFinite(next)) return;
    emitCue(false, true);
    transitionPose = capturePose();
    priorChapter = displayChapter; // Actual visible state wins over a stale `from` during rapid input.
    priorTime = chapterTime;
    chapter = clamp(Math.round(next), 0, 10);
    linkedTransition = isRoomChapter(priorChapter) && isRoomChapter(chapter) ||
      isCosmicChapter(priorChapter) && isCosmicChapter(chapter);
    const requestedDuration = Number(duration);
    transitionDuration = Number.isFinite(requestedDuration) && requestedDuration > 0 ?
      clamp(requestedDuration > 10 ? requestedDuration / 1000 : requestedDuration, 0.55, 2.2) : chapter === 1 ? 1.8 : 1.25;
    chapterTime = (immediate || motionReduced) && chapter !== 4 ? 30 : 0;
    transition = immediate || motionReduced ? 1 : 0;
    updatePose(); dirty = true;
  }

  function setReducedMotion(value) {
    if (disposed) return;
    motionReduced = Boolean(value);
    if (motionReduced) { chapterTime = 30; transition = 1; }
    lastTime = 0; dirty = true; updatePose();
  }
  function setQuality(value) {
    if (disposed) return;
    qualityMode = qualityName(value); dynamicScale = 1; slowFrames = 0; resize();
  }
  function visibilityChanged() {
    hidden = document.hidden;
    if (hidden) frame.hiddenPauses++;
    lastTime = 0; lastDraw = 0; dirty = true;
  }
  function diagnostics() {
    return {
      backend, detail, chapter, reducedMotion: motionReduced, quality: qualityMode,
      effectiveScale: Number(dynamicScale.toFixed(2)), hidden, disposed, failed,
      illustrative: true, measuredCoverage: false, measuredPersonCoordinates: false,
      transition: { from: priorChapter, to: chapter, displayed: displayChapter, progress: Number(transition.toFixed(3)),
        durationMs: Math.round(transitionDuration * 1000), kind: linkedTransition ? 'continuous spatial' : 'threshold iris' },
      cue: { type: 'illustrated-entry', active: cueActive, loopSeconds: 8, crossingSeconds: Number(crossingAt.toFixed(3)),
        holdSeconds: 2, reducedMotion: 'stable illustrated detection pose', liveSensorData: false },
      frame: { ...frame, averageMs: Number(frame.averageMs.toFixed(2)),
        renderMs: Number(frame.renderMs.toFixed(2)), maxRenderMs: Number(frame.maxRenderMs.toFixed(2)),
        fps: Number(frame.fps.toFixed(1)) },
      viewport: { width, height, pixelRatio: Number(pixelRatio.toFixed(3)),
        pixels: Math.floor(width * height * pixelRatio * pixelRatio) },
      limits: { maxFPS: QUALITY[qualityMode].fps, pixelBudget: QUALITY[qualityMode].pixels,
        particles: 240, starFragments: 192, alertMotes: 48, fieldPlanes: 3, generatedEnvironment: '128 × 64', shadows: false,
        glow: 'bounded analytic GPU glow; no full-frame bloom chain' },
      resources: { geometries: resources.geometry.size, materials: resources.material.size,
        textures: resources.texture.size, activeGPU: renderer && initialized ? { ...renderer.info.memory } : null },
    };
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    emitCue(false, true);
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', visibilityChanged);
    if (typeof window !== 'undefined') window.removeEventListener('resize', resize);
    canvas?.removeEventListener('webglcontextlost', contextLost);
    gpuDevice?.removeEventListener?.('uncapturederror', uncapturedError);
    resizeObserver?.disconnect();
    safelyDisposeRenderer();
    for (const collection of Object.values(resources)) {
      for (const resource of collection) { try { resource.dispose(); } catch { /* Best effort on lost device. */ } }
      collection.clear();
    }
    scene.clear();
  }
  const api = { setChapter, setReducedMotion, setQuality, resize, dispose, diagnostics };

  function renderFrame(timestamp) {
    if (disposed || failed || hidden) return;
    if (motionReduced && !dirty) return;
    const interval = 1000 / QUALITY[qualityMode].fps;
    if (lastDraw && timestamp - lastDraw < interval - 0.6) return;
    const delta = lastTime ? Math.min((timestamp - lastTime) / 1000, 0.067) : 1 / 60;
    const actualMs = lastDraw ? timestamp - lastDraw : interval;
    lastTime = timestamp; lastDraw = timestamp;
    if (!motionReduced) {
      activeTime += delta; chapterTime += delta;
      transition = Math.min(1, transition + delta / transitionDuration);
    }
    clock.value = motionReduced ? 4.5 : activeTime;
    updatePose();
    try {
      const started = performance.now();
      renderer.info.reset();
      renderer.render(scene, camera);
      if (renderer.backend?.isWebGLBackend && (frame.count < 2 || frame.count % 60 === 0)) {
        const glError = renderer.getContext().getError();
        if (glError !== 0) throw new Error(`WebGL2 runtime error ${glError}`);
      }
      const duration = performance.now() - started;
      frame.count++;
      frame.renderMs = duration;
      frame.maxRenderMs = Math.max(frame.maxRenderMs, duration);
      frame.averageMs = motionReduced ? 0 : (frame.averageMs ? frame.averageMs * 0.95 + actualMs * 0.05 : actualMs);
      frame.drawCalls = renderer.info.render.drawCalls;
      frame.triangles = renderer.info.render.triangles;
      statsFrames++;
      if (!statsTime) statsTime = timestamp;
      if (timestamp - statsTime >= 1000) {
        frame.fps = motionReduced ? 0 : statsFrames * 1000 / (timestamp - statsTime);
        statsFrames = 0; statsTime = timestamp;
      }
      // Startup compilation is excluded. Adaptive auto quality only moves down.
      if (qualityMode === 'auto' && frame.count > 180 && !motionReduced) {
        slowFrames = frame.averageMs > 26 ? slowFrames + 1 : Math.max(0, slowFrames - 2);
        if (slowFrames > 150 && dynamicScale > 0.66) {
          dynamicScale = Math.max(0.65, dynamicScale * 0.84);
          frame.adaptiveReductions++; slowFrames = 0; resize();
        }
      }
      dirty = false;
    } catch (error) { fail(`Render failed: ${error?.message || error}`); }
  }

  try {
    if (!canvas || typeof window === 'undefined') throw new Error('No browser canvas available.');
    canvas.addEventListener('webglcontextlost', contextLost, false);
    renderer = new THREE.WebGPURenderer({ canvas, alpha: true, antialias: true,
      powerPreference: 'high-performance' });
    renderer.onDeviceLost = (info) => fail(`Graphics device lost: ${info?.message || info?.reason || 'unknown reason'}`);
    renderer.debug.onShaderError = (gl, program) => {
      throw new Error(`WebGL2 shader compilation failed: ${gl.getProgramInfoLog(program) || 'unknown compiler error'}`);
    };
    renderer.setClearColor(PALETTE.void, 1);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.86;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.info.autoReset = false;
    let timeout;
    const initialization = renderer.init().then(() => {
      initialized = true;
      if (failed || disposed) safelyDisposeRenderer();
    });
    try {
      await Promise.race([initialization, new Promise((_, reject) => {
        timeout = setTimeout(() => reject(new Error('GPU initialization timed out.')), 12000);
      })]);
    } finally { clearTimeout(timeout); }
    if (failed || disposed) return api;
    gpuDevice = renderer.backend?.device || null;
    gpuDevice?.addEventListener?.('uncapturederror', uncapturedError);
    scene.environment = makeEnvironment();
    scene.environmentIntensity = 0.72;
    resize();
    document.addEventListener('visibilitychange', visibilityChanged);
    window.addEventListener('resize', resize, { passive: true });
    if (typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(resize); resizeObserver.observe(canvas);
    }
    // Compile every visual family once, without drawing them or changing the chapter.
    cosmic.visible = roomRig.visible = portalRig.visible = ambient.visible = true;
    boards.visible = field.visible = futureRing.visible = figure.visible = true;
    await renderer.compileAsync(scene, camera);
    roomRig.visible = false;
    await renderer.compileAsync(scene, camera); // Portal lighting variant, without the room point light.
    if (failed || disposed) return api;
    updatePose();
    renderer.render(scene, camera);
    // Confirm submission before advertising readiness; native GPU errors can arrive asynchronously.
    if (gpuDevice?.queue) await gpuDevice.queue.onSubmittedWorkDone();
    if (failed || disposed) return api;
    canvas.style.visibility = '';
    const selectedBackend = renderer.backend?.isWebGPUBackend ? 'WebGPU' : 'WebGL2';
    emitStatus(selectedBackend, 'Local GPU scene ready. Concept visuals only; not sensing evidence.');
    await renderer.setAnimationLoop(renderFrame);
  } catch (error) {
    fail(error?.message || String(error));
  }
  return api;
}

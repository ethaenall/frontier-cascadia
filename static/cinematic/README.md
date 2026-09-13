# Threshold — cinematic presentation

A local, presenter-controlled experience for Ethan’s Frontier Cascadia project. It is separate from the sensor app. Nothing in this presentation calibrates, arms, controls, or impersonates the detector.

## Open locally

From the repository root:

```sh
uv run --locked python scripts/present.py
```

The launcher prints its loopback URL. The default is `http://127.0.0.1:8894/`; `--port 0` chooses a free port. This server serves only this presentation directory. It does not expose the project root, authentication tokens, recordings, `node_modules`, or detector APIs. A server already running on the selected port must not be replaced silently.

- **Space / Right**: next scene. **Left**: previous. **Home**: restart.
- **F**: full screen. **O**: scene overview.
- **P**: separate presenter window. Move it to the private display before speaking.
- **N**: on-screen rehearsal notes. These are NOT hidden from the audience.
- **S**: settings and optional local-console address.
- **M**: optional quiet transition sound (off by default).
- **Q**: cycle adaptive / high / cool-and-quiet graphics.
- **Escape**: close a dialog, or use the browser’s full-screen exit.

All eleven scenes wait for the presenter. Animated backgrounds, a simulated crossing and red cue can continue within the current scene. There is no automatic next slide. During development, the local preview refreshes when the top-level presentation files change; the current chapter is retained by id.

## Actual prototype handoff

Set the address of a separately selected local console in Settings. Only loopback HTTP URLs without credentials, query parameters or fragments are accepted. The deck never probes or controls that console. A saved URL does not establish availability, source mode, health or permission to control an alarm. Pair privately in the actual app before the talk.

The handoff includes an explicitly labelled recorded TEST screenshot when no console is selected. It is not an interactive detector. The image of the native app is an archived **iPhone Simulator** capture, not a newly verified physical iPhone session.

## Claims

- Opening black hole: fictional visual joke.
- Doorway, RF field and red detection: **illustrations**, not measurements.
- Software workflow: earlier project evidence verifies synthetic TEST input, not physical sensing. See the project README.
- Entry counting, crossing discrimination and falls: **future work / unverified**, not capabilities demonstrated by this animation.
- Comparison: a magnetic door-contact signal alone versus Threshold’s development direction, not a claim that all security systems lack motion sensors.

The full spoken script is maintained separately from this public source release.

## Rendering and source ownership

- `scene.js`: local Three.js WebGPU rendering; WebGL2 and static-art fallback paths.
- `main.js`: presentation state, local handoff, accessibility, optional audio, presenter synchronization.
- `transitions.js`: interruptible typography and aperture/field transitions. No chapter clock.
- `chapters.js`: the story and speaker notes; stable ids map to visual scenes.
- `styles.css` / `index.html`: the presentation shell and responsive layout.
- `assets/PROVENANCE.json`: source and hashes of the exact existing project captures.

Three.js **0.180.0** is pinned in `package-lock.json` and vendored for offline runtime use. MIT license: `vendor/LICENSE.three`. The sole vendor modification is the `three.tsl.js` import rewritten to `./three.webgpu.js`. No CDN, web font, analytics, external image service, microphone, camera, model call or runtime network dependency is used by the cinematic engine.

Art-direction references supplied by Ethan: [vgpu](https://github.com/vercel-labs/vgpu), [GOD’S EYE](https://www.godseye.world/home), [Nightfall](https://nightfallcres.vercel.app), [Habenula](https://habenula.ai). Their source/assets are not copied into this presentation. vgpu is a reference, not an installed rendering dependency.

## Verification boundary

`npm test` in this directory runs `tests/presenter.mjs` against the separately started static preview. It does not launch a server or control any detector. Test outputs go to unique `/tmp/threshold-presentation-qa-*` directories. Browser testing needs the installed Playwright package identified in that script. Rendering backend and functional behavior are reported separately. An HTML/CSS fallback pass is never WebGPU proof.

Current gate results are recorded separately after a stable final run. No public push, deployment or submission is performed by this presentation work.

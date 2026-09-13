# Threshold · Frontier Cascadia

A local-first entrance-monitoring prototype with a native iPhone camera demo, a synthetic radio-lab workflow, and a cinematic browser presentation.

Built for [Frontier Cascadia](https://frontiercascadia.org/). **Research/demo software—not a certified security system or emergency service.**

## What is here

- **iPhone camera demo:** SwiftUI interface with on-device person detection in a fixed doorway region. Keep the phone stationary, well lit, and in the foreground. The camera path is implemented; independent physical acceptance remains pending.
- **Rehearsal / TEST:** explicitly synthetic input for demonstrating calibration, arming, a latched alert, acknowledgement, and disarming. Synthetic results are not evidence of physical sensing.
- **Local radio backend:** Python/FastAPI, authenticated controls, stream-health checks, recording/replay, and a browser dashboard.
- **Cinematic presentation:** an offline-capable browser walkthrough with clearly labelled synthetic visuals and recorded TEST screenshots.
- **Experimental firmware:** Arduino Nano ESP32 / ESP32-S3 CSI sender and receiver sources, with upstream attribution.

## Important limits

**Real received Wi-Fi CSI and physical motion detection are not verified.** Transmit/ACK success does not establish reception, occupancy, exact-zone tracking, direction, or falls. The planned camera-free path requires compatible router and sensing hardware; arbitrary home-router compatibility is not claimed.

Calls are disabled/dry-run by default. No emergency delivery, background alert reliability, or production security certification is claimed. A stream fault is not an all-clear. The camera demo is separate from the shared radio alarm.

## Run the local TEST dashboard

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/ethaenall/frontier-cascadia.git
cd frontier-cascadia
uv sync --locked
uv run python -m threshold --mode test
```

Use the local address printed by the process. Pair using its locally stored control token; never put tokens in URLs, screenshots, or commits. Keep the server on localhost or a trusted private network. Do not start a second collector against an active hardware session.

Demo sequence: **Enable sound → Calibrate → Arm → labelled synthetic motion → Acknowledge → Disarm.** Browser audio needs a user gesture and is not guaranteed in background tabs.

```bash
uv run --locked pytest -q
```

Tests are included. This public packaging pass did not rerun the full test suite or physical-device checks.

## iPhone

Open `ios/Threshold.xcodeproj` in Xcode. Choose your own signing team for a physical device. The domain package is in `ios/Domain`; app-host and UI tests are also included. Camera permissions and real-device behavior require your own verification.

## Browser presentation

```bash
uv run python scripts/present.py
```

See [`static/cinematic/README.md`](static/cinematic/README.md) for options and labels. This presentation is not a live sensor feed.

## Privacy and safety

This source release excludes local `.env` credentials, recordings, control tokens, hardware backups, Xcode user state, and private test evidence. `.env.example` contains empty credential placeholders. Optional Browserbase/Daytona tools are developer utilities, not required sensing services; using cloud tools may create billable resources.

## Credits and disclosures

- User-directed Prime Agent and coding assistants helped with setup, implementation, debugging, tests, and documentation. Physical testing and release decisions remain with the project lead.
- Three.js is MIT licensed; see [`LICENSE.three`](static/cinematic/vendor/LICENSE.three). Recorded UI images are our synthetic TEST interface captures; provenance is in [`assets/PROVENANCE.json`](static/cinematic/assets/PROVENANCE.json).
- Espressif CSI reference material and license records are in [`firmware/provenance`](firmware/provenance), including Apache-2.0 notices.
- Browserbase, Daytona, and Mobbin were development tools/references. The historical Browserbase screenshot shows its public marketing site, not a product feature.

Public source availability does not grant a new license to third-party material. Retained dependency licenses govern their respective code.

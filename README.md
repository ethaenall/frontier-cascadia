# Suvidha hackathon (private)

Private workspace for the Suvidha AI Virtual Hackathon 2026.

- **Browserbase** runs real Chrome in the cloud. Drive it with the `browse` CLI and `BROWSERBASE_API_KEY`. There is no project-id step.
- **Daytona** runs isolated cloud computers (sandboxes) so you can execute code off this Mac. Drive it with the official Python SDK (`daytona`) and `DAYTONA_API_KEY`.

Never commit `.env`.

## Local setup

1. Copy `.env.example` to `.env`.
2. Fill `BROWSERBASE_API_KEY` and `DAYTONA_API_KEY`.
3. Leave `DAYTONA_API_URL=https://app.daytona.io/api` and `DAYTONA_TARGET=us` unless Daytona says otherwise.
4. Install Python deps: `uv sync` (Python 3.12, package `daytona`).
5. Optional CLI: `brew install daytonaio/cli/daytona`.
6. Check Browserbase: `browse cloud projects list`.
7. Check Daytona: `uv run python scripts/daytona_hello.py` then `daytona list`.

## Daytona smoke test

This is the official getting-started path: create a sandbox, run Hello World, delete it.

```bash
set -a && source .env && set +a
uv run python scripts/daytona_hello.py
daytona list
```

Dashboard: https://app.daytona.io/dashboard/sandboxes

The smoke test uses an **ephemeral** sandbox (`ephemeral=True`) so it is deleted when stopped.

## Browserbase first session

```bash
set -a && source .env && set +a
browse open https://www.browserbase.com --remote --session suvidha-onboard --wait load
browse screenshot --full-page --path evidence/browserbase-home-full.png --session suvidha-onboard --remote
browse status --session suvidha-onboard
browse stop --session suvidha-onboard
```

Watch live sessions at https://www.browserbase.com/sessions

## Claims

| Capability | Classification | Local evidence | Live evidence | Limitation |
|---|---|---|---|---|
| Browserbase CLI + API key | built | `browse --version` (`0.9.6`), `browse cloud projects list` | key listed the Production project | Developer plan: proxies + full Model Gateway. Verified is Scale-only |
| Full-page screenshot of browserbase.com | built | `evidence/browserbase-home-full.png` (1273x7882 PNG) | https://www.browserbase.com/sessions/1b72706a-bbe8-43fd-8561-7903122ac919 (COMPLETED) | One-off onboarding capture, not a product feature |
| Daytona SDK + API key | built | `uv run python scripts/daytona_hello.py` printed `Hello World` | sandbox `c95a099b-78cb-415d-a92f-bc560ff18f6c` started then deleted; `daytona list` empty | CLI v0.211.2 vs API v0.213.0 warning; SDK still ran |
| Product prototype | not in scope | — | — | Product concept not chosen yet |

## Disclosures (draft)

- Prime Agent / coding assistant: repo setup, CLI/SDK install, Browserbase and Daytona onboarding
- Browserbase: cloud Chrome sessions
- Daytona: ephemeral sandbox for Hello World
- Models via Browserbase Model Gateway: only if a later Stagehand/LLM step is added

## Prompt

See `docs/suvidha_hackathon_prompt_and_rubric.md`.

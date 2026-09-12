# Suvidha hackathon (private)

Private workspace for the Suvidha AI Virtual Hackathon 2026.

Browserbase runs real Chrome in the cloud. This repo talks to it with the `browse` CLI and the `BROWSERBASE_API_KEY` in `.env`. There is no project-id step.

## Local setup

1. Copy `.env.example` to `.env`.
2. Put your Browserbase API key in `.env`.
3. Confirm the CLI: `browse --version` (install with `npm install -g browse@latest` if needed).
4. Confirm the key: `browse cloud projects list`.

Never commit `.env`.

## First cloud session (full-page screenshot)

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
| Browserbase CLI + API key | built | `browse --version` (`0.9.6`), `browse cloud projects list` | key listed the Production project | Free plan: no Proxies, no Verified, Model Gateway capped at $5 |
| Full-page screenshot of browserbase.com | built | `evidence/browserbase-home-full.png` (1273x7882 PNG) | https://www.browserbase.com/sessions/1b72706a-bbe8-43fd-8561-7903122ac919 (COMPLETED) | One-off onboarding capture, not a product feature |
| Product prototype | not in scope | — | — | Product concept not chosen yet |

## Disclosures (draft)

- Prime Agent / coding assistant: repo setup, CLI install, Browserbase onboarding
- Browserbase: cloud Chrome sessions
- Models via Browserbase Model Gateway: only if a later Stagehand/LLM step is added

## Prompt

See `docs/suvidha_hackathon_prompt_and_rubric.md`.

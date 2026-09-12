# Frontier Cascadia (private)

Private workspace for **Frontier Cascadia**, September 12, 2026.

Not Suvidha. That was the wrong event.

Event: https://frontiercascadia.org/

12 hours. High school. In-person at UW Foster (Seattle) or remote. The public standard is whether the software works.

Allowed before hacking starts: plan, sketch, docs, empty repo, boilerplate, dependencies. Product logic is written on the day.

## Tools already wired

- **Browserbase** — cloud Chrome for agents (`browse` CLI, `BROWSERBASE_API_KEY`). Sponsor. Developer plan: proxies + full Model Gateway. Verified is Scale-only.
- **Daytona** — isolated sandboxes for running code (`daytona` SDK/CLI, `DAYTONA_API_KEY`). Sponsor.
- **Mobbin Pro** — UI reference via MCP (connected in Prime Agent, not in this repo).

Never commit `.env`.

## Local setup

```bash
cd /Users/Ethan/Developer/frontier-cascadia
cp .env.example .env   # then add keys
uv sync
set -a && source .env && set +a
browse cloud projects list
uv run python scripts/daytona_hello.py
daytona list
```

## Claims

| Capability | Classification | Local evidence | Live evidence | Limitation |
|---|---|---|---|---|
| Browserbase CLI + API key | built | `browse --version` (`0.9.6`) | session https://www.browserbase.com/sessions/1b72706a-bbe8-43fd-8561-7903122ac919 | Developer plan; Verified is Scale-only |
| Daytona SDK + API key | built | `uv run python scripts/daytona_hello.py` printed Hello World | sandbox created then deleted | CLI 0.211.2 vs API 0.213.0 warning |
| Product prototype | not in scope | — | — | Logic waits until hacking; concept not chosen |

## Disclosures (draft)

- Prime Agent / coding assistant: repo setup, Browserbase and Daytona onboarding
- Browserbase, Daytona, Mobbin: event partners / tools
- No product feature logic yet

## Event notes

See `docs/frontier-cascadia.md`.

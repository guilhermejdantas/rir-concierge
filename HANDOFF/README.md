# AI-to-AI handoff — Rock in Rio 2026 Concierge

This folder holds task briefs for the other AI agents Guilherme is orchestrating.
Each agent owns a slice of the deployment that it — and only it — can actually do.

| Agent | Scope | Brief |
|---|---|---|
| **Gemini (Google Workspace)** | Audit & normalise the festival show events already on `guilherme.dantas.sp@gmail.com`'s calendar so the concierge's parser recognises every one. | [`GEMINI.md`](GEMINI.md) |
| **Gordon (Docker Desktop)** | Build the image, run the `app` + `redis` stack, smoke-test the endpoints, optionally run a `cloudflared` tunnel container. | [`GORDON.md`](GORDON.md) |
| **Claude Code (this repo)** | Wire everything together, integration-test with `scripts/simulate_webhook.py`, fix bugs once credentials exist. | — |
| **Guilherme (human — not automatable)** | GCP Console: create project, enable Calendar API + Distance Matrix API, OAuth consent screen, download OAuth **Desktop** client, create Maps API key. Meta for Developers: WhatsApp app, phone-number id, access token. Run `scripts/google_oauth_bootstrap.py` once (interactive browser login). | [`../README.md`](../README.md) |

## Why the split

- **Provisioning cloud credentials is a human task.** No agent here can create a
  GCP project, run the Google OAuth consent screen, or mint a Meta access token.
  Those four artefacts (`oauth_client.json`, `oauth_token.json`,
  `GOOGLE_MAPS_API_KEY`, `META_ACCESS_TOKEN` + `META_PHONE_NUMBER_ID`) must be
  produced by Guilherme and dropped into `.env` / `secrets/`.
- **Gemini already has the calendar.** It created the events; it can read and fix
  them. It cannot authenticate the microservice — that needs its own OAuth token.
- **Gordon already has Docker.** It can do the entire container lifecycle locally
  but has no browser session for Google/Meta logins.

## The contract between agents

Everyone reads/writes these shared files so the work composes:

- `HANDOFF/CALENDAR_AUDIT.md` — Gemini writes its findings + the final event
  table here. Claude Code reads it to confirm the parser will match.
- `.env` — Guilherme fills the secrets. Gordon and Claude Code consume it.
- `scripts/simulate_webhook.py` — anyone can drive the inbound flow with this
  once `app` is running (no WhatsApp needed).
- `HANDOFF/STATUS.md` — running checklist; each agent ticks its lines.

## Definition of done

1. `docker compose up -d` → `GET /healthz` returns `{"status":"ok"}`.
2. `python scripts/simulate_webhook.py list-reply --id checkin_palco_sunset`
   posts a walk-time message to the group (or logs the outbound payload if
   WhatsApp creds are still placeholders).
3. A calendar event 30 min in the future triggers the `-30 min` list alert.
4. `simulate_webhook.py button --id delay_15` patches that event `+15 min` and
   confirms in the group.

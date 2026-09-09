# Deployment status checklist

Tick a line when done. Each agent maintains its own section.

## Guilherme (human)
- [ ] GCP project created (`rir-concierge-2026`)
- [ ] Google Calendar API enabled
- [ ] Distance Matrix API enabled
- [ ] OAuth consent screen configured (External, test user = owner, scope calendar)
- [ ] OAuth **Desktop** client downloaded → `secrets/oauth_client.json`
- [ ] `python scripts/google_oauth_bootstrap.py --client secrets/oauth_client.json --token secrets/oauth_token.json` run → `secrets/oauth_token.json` exists
- [ ] Maps API key → `.env` `GOOGLE_MAPS_API_KEY`
- [ ] Meta WhatsApp app → `.env` `META_PHONE_NUMBER_ID`, `META_ACCESS_TOKEN`
- [ ] `.env` `WHATSAPP_GROUP_ID` set (destination number / group JID)
- [ ] `.env` `WHATSAPP_VERIFY_TOKEN` set to a long random string
- [ ] `.env` `PUBLIC_BASE_URL` set to the tunnel/HTTPS URL
- [ ] Meta webhook callback registered + subscribed to `messages`

## Gemini (calendar)
- [ ] Audited events 2026-09-11..13
- [ ] All titles → `Artist — Stage` with a canonical stage name
- [ ] All events timed (no all-day), tz America/Sao_Paulo
- [ ] `Rock in Rio 2026` present in each event's location
- [ ] FINAL EVENT TABLE pasted into `CALENDAR_AUDIT.md`

## Gordon (docker)
- [ ] `docker compose build app` OK (image size: ___)
- [ ] `redis-cli ping` → PONG
- [ ] `GET /healthz` → `redis: true`
- [ ] Logs show `Concierge up.` + scheduler start
- [ ] `simulate_webhook.py list-reply` → HTTP 200 (+ outbound log line)
- [ ] `/checkin?u=test123` renders HTML
- [ ] (optional) cloudflared tunnel URL: ___

## Claude Code (integration)
- [ ] Parser dry-run against `CALENDAR_AUDIT.md` — every show resolves
- [ ] End-to-end: future event → `-30 min` alert fires
- [ ] `delay_15` → `events.patch` `+15 min` + confirmation
- [ ] Web check-in (`/api/checkin`) → walk estimate posted
- [ ] Haversine fallback verified (Maps key removed)

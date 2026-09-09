# Rock in Rio 2026 — WhatsApp Festival Concierge

A self-hosted **FastAPI + Redis** microservice that watches Guilherme's Google
Calendar and drives a WhatsApp group through the festival:

1. **−30 min proactive alert** for every scheduled show, with one-tap check-in
   buttons for the busiest hotspots.
2. **Walk-time computation** from a location pin, a hotspot button, or a browser
   check-in — Google Distance Matrix with a **Haversine @ 4.5 km/h fallback**.
3. **Live +15 min rescheduling** via `events.patch` when the group says
   "Atrasar 15m".

Built for the **WhatsApp Companion Mode** constraint on the on-site Galaxy S26:
it never expects a Live Location stream — only static pins, the interactive
list menu, or the `/checkin` web page.

The −30 min alert is a single WhatsApp **interactive list** (menu) message, so
all hotspot options and "Pular Show" fit in one clean prompt rather than
multiple button messages.

---

## Architecture

```
Google Calendar ──poll 60s──▶ APScheduler ──▶ Concierge ──▶ WhatsApp group
(guilherme.dantas.sp)                              │ ▲
                                                   ▼ │
WhatsApp webhook ─▶ FastAPI /webhook/whatsapp ─────┘ │
Galaxy S26 browser ─▶ /checkin ─▶ /api/checkin ──────┘
                                                   │
                            Redis (active show, alert dedupe, skip markers)
                                                   │
                            Google Maps Distance Matrix (+ Haversine fallback)
```

| File | Role |
|---|---|
| `main.py` | FastAPI app, endpoints, APScheduler wiring |
| `concierge.py` | All business logic (alerts, walk-time, reschedule) |
| `config.py` | `pydantic-settings` config + static festival geography |
| `models.py` | Shared Pydantic models |
| `services/google_calendar.py` | Calendar reads + `events.patch` (SA or OAuth) |
| `services/maps.py` | Distance Matrix client + Haversine fallback |
| `services/whatsapp.py` | Meta Cloud API **and** Evolution API, unified |
| `docker-compose.yml` | `app` + `redis` (+ optional `evolution-api`) |
| `n8n-workflow.json` | Same logic as an importable N8N workflow |
| `ZAPIER_SETUP.md` | Optional low-volume Zapier alternative |

---

## 5-minute setup

### 0. Prerequisites
- Docker + Docker Compose
- A GCP project owned by **guilherme.dantas.sp@gmail.com** with **Google
  Calendar API** and **Distance Matrix API** enabled
- A WhatsApp channel — either Meta Cloud API (recommended) or the bundled
  Evolution API container

### 1. Clone & configure
```bash
cp .env.example .env
mkdir -p secrets
$EDITOR .env
```

### 2. Google credentials → `./secrets/`

**Service account (recommended, headless):**
```bash
# In GCP: IAM & Admin ▸ Service Accounts ▸ Create ▸ add JSON key
mv ~/Downloads/rir-sa-*.json secrets/service_account.json
```
Then in Google Calendar (as guilherme.dantas.sp@gmail.com):
**Settings ▸ Settings for my calendars ▸ [primary] ▸ Share with specific
people ▸ add the service account email ▸ "Make changes to events".**
Set in `.env`: `GOOGLE_AUTH_MODE=service_account`.

**OAuth (alternative):**
```bash
# GCP: APIs & Services ▸ Credentials ▸ OAuth client ID ▸ Desktop app
mv ~/Downloads/client_secret_*.json secrets/oauth_client.json
pip install -r requirements.txt
python scripts/google_oauth_bootstrap.py \
  --client secrets/oauth_client.json --token secrets/oauth_token.json
```
Set in `.env`: `GOOGLE_AUTH_MODE=oauth`.

### 3. Maps key
Add the Distance Matrix API key to `.env` as `GOOGLE_MAPS_API_KEY`.
(If omitted, the service still works using the Haversine fallback.)

### 4. WhatsApp

**Meta Cloud API:**
- Set `WHATSAPP_PROVIDER=meta`, `META_PHONE_NUMBER_ID`, `META_ACCESS_TOKEN`.
- `WHATSAPP_GROUP_ID` = destination number in E.164 without `+`.
- `WHATSAPP_VERIFY_TOKEN` = any long random string.

**Evolution API (self-hosted, QR pairing):**
- Set `WHATSAPP_PROVIDER=evolution`, `EVOLUTION_API_KEY`.
- Start with the profile: `docker compose --profile evolution up -d`
- Pair: open `http://<host>:8080/manager`, create instance `rir-concierge`,
  scan the QR from the S26.
- `WHATSAPP_GROUP_ID` = the group JID (e.g. `120363...@g.us`).

### 5. Launch
```bash
docker compose up -d --build
curl -s localhost:8000/healthz | jq
```

### 6. Register the webhook
Expose port 8000 over HTTPS (Caddy, nginx, Cloudflare Tunnel, `ngrok`) at
`PUBLIC_BASE_URL`. Then:

- **Meta:** App ▸ WhatsApp ▸ Configuration ▸ Callback URL
  `https://<PUBLIC_BASE_URL>/webhook/whatsapp`, Verify Token = your
  `WHATSAPP_VERIFY_TOKEN`, subscribe to **messages**.
- **Evolution:** already wired via `WEBHOOK_GLOBAL_URL` in compose.

---

## Calendar event conventions

An event is treated as an in-scope show when **either**:
- its title/location/description contains `Rock in Rio` (configurable via
  `FESTIVAL_EVENT_KEYWORD`), **and** a stage name is present; or
- a stage name alone is present (`Palco Mundo`, `Palco Sunset`, `Espaço
  Favela`, `New Dance Order`, `Gourmet Square`).

Recommended title format:
```
Foo Fighters — Palco Mundo
```
`start.dateTime` / `end.dateTime` must be set (timed events, not all-day).

---

## Interaction flow

| Group input | Result |
|---|---|
| −30 min before a show | One alert message with an interactive **list menu**: 📍 Palco Sunset · 🍔 Gourmet Square · 🍺 Espaço Favela · 🚪 Entrada / BRT · ⏩ Pular Show |
| Location pin | Walk estimate to the active stage + `[Manter Horário] [Atrasar 15m]` |
| `📍 Palco Sunset` etc. | Same, using that hotspot's fixed coordinates |
| `Atrasar 15m` button / "atrasar 15m" text | `events.patch` +15 min + confirmation with new time |
| `Manter Horário` | Acknowledgement |
| `⏩ Pular Show` / "pular" | Suppresses further nudges for that event |
| "status" / "agenda" | Prints the next upcoming show |
| Open `PUBLIC_BASE_URL/checkin?u=<id>` | Browser high-accuracy GPS → same walk-estimate flow |

---

## Local development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export $(grep -v '^#' .env | xargs)   # or use a .env loader
uvicorn main:app --reload --port 8000
```

Run a fake alert without waiting for the clock:
```bash
python - <<'PY'
import asyncio, redis.asyncio as r
from config import get_settings
from services.google_calendar import GoogleCalendarService
async def main():
    s = get_settings()
    cal = GoogleCalendarService(s)
    for show in cal.list_shows():
        print(show.start, show.artist, "→", show.stage)
asyncio.run(main())
PY
```

---

## Operational notes

- **Idempotent alerts:** a Redis `SET NX` lock per `event_id` guarantees one
  alert per show even with overlapping polls or multiple `app` replicas.
- **State TTL:** all Redis keys expire after `STATE_TTL_SECONDS` (default 6h),
  so nothing lingers past the festival.
- **Maps outage:** every Distance Matrix failure path (no key, HTTP error,
  non-OK status, malformed body) degrades to Haversine automatically; the
  WhatsApp reply is tagged `(estimativa offline)`.
- **Companion Mode:** the service only ever reads `message.location.latitude/
  longitude` (static pins). No Live Location subscription is attempted.
- **Timezone:** all scheduling is done in `America/Sao_Paulo` (UTC−3).

---

## N8N alternative

Import `n8n-workflow.json` (Workflows ▸ Import from File). Set the credentials
placeholder `GCAL_CRED` to a Google Calendar OAuth2 credential for
guilherme.dantas.sp@gmail.com, and define the env vars `META_PHONE_NUMBER_ID`,
`META_ACCESS_TOKEN`, `WHATSAPP_GROUP_ID`, `GOOGLE_MAPS_API_KEY` on the N8N host.
The webhook path is `/webhook/whatsapp`.

## Zapier alternative

See [`ZAPIER_SETUP.md`](ZAPIER_SETUP.md). Recommended only for a short,
low-volume trial — calendar polling alone burns the task quota quickly.

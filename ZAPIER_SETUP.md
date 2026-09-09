# ZAPIER_SETUP.md — Optional low-volume Zapier architecture

> **Status:** secondary / testing path only. The production path is the
> self-hosted FastAPI + Redis microservice (see `README.md`). Use Zapier only
> for a quick trial or a very low-volume weekend where you do not want to run
> infrastructure.

## Why this is *not* the primary path

| Concern | Zapier reality |
|---|---|
| Calendar polling every 1–2 min | "Every 1 minute" needs a **paid** plan; each poll = 1 task even when nothing changed. A 3-day festival ≈ **4,300+ tasks** just for polling. |
| Inbound WhatsApp webhooks | **Webhooks by Zapier** is a Premium app (paid). |
| Multi-step routing (location vs button vs text) | Each Path + action step is a separate billed task. |
| Distance Matrix call | Needs a Webhooks (Premium) GET action → another billed task. |
| `events.patch` for +15 min | Google Calendar "Update Event" works, but requires a search step first. |
| State / dedupe (don't alert twice) | No native store; needs Zapier Tables or Storage by Zapier (extra steps). |

Estimated: **Starter (750 tasks/mo) is exhausted in well under a day.** Budget
for the **Professional** plan if you insist on Zapier.

---

## Zap 1 — Proactive −30 min alert

1. **Trigger:** *Schedule by Zapier* → **Every Hour** (Starter) or **Every
   Minute** (Professional). Minute granularity is what you actually want.
2. **Action — Google Calendar → Find Event(s):**
   - Calendar: `guilherme.dantas.sp@gmail.com`
   - Search term: `Rock in Rio`
   - Start time: `in 28 minutes`
   - End time: `in 32 minutes`
   - (This 4-minute window matched against a 1-minute trigger gives each show
     one alert; add a dedupe check in step 4.)
3. **Action — Filter by Zapier:** only continue if an event was found.
4. **Action — Storage by Zapier → Get Value** for key
   `alerted_{{event_id}}`; then **Filter** to stop if it already equals `1`;
   then **Storage → Set Value** `alerted_{{event_id}} = 1`.
5. **Action — Webhooks by Zapier (Premium) → POST**
   - URL: `https://graph.facebook.com/v20.0/<META_PHONE_NUMBER_ID>/messages`
   - Headers: `Authorization: Bearer <META_ACCESS_TOKEN>`,
     `Content-Type: application/json`
   - Data (JSON):
     ```json
     {
       "messaging_product": "whatsapp",
       "to": "<WHATSAPP_GROUP_ID>",
       "type": "interactive",
       "interactive": {
         "type": "list",
         "header": { "type": "text", "text": "Rock in Rio 2026" },
         "body": { "text": "🔔 Próximo Show: {{artist}} no {{stage}} às {{time}}.\nEnviem a localização atual ou escolham um ponto no menu abaixo." },
         "action": {
           "button": "Fazer check-in",
           "sections": [ { "title": "Onde vocês estão?", "rows": [
             { "id": "checkin_palco_sunset", "title": "📍 Palco Sunset" },
             { "id": "checkin_gourmet_square", "title": "🍔 Gourmet Square" },
             { "id": "checkin_espaco_favela", "title": "🍺 Espaço Favela" },
             { "id": "checkin_entrada_brt", "title": "🚪 Entrada / BRT" },
             { "id": "skip_show", "title": "⏩ Pular Show" }
           ] } ]
         }
       }
     }
     ```
   - Derive `{{artist}}` / `{{stage}}` / `{{time}}` with a **Formatter → Text
     (Split / Extract)** step on the event title and start time.

---

## Zap 2 — Inbound location / button → walk time

1. **Trigger — Webhooks by Zapier (Premium) → Catch Hook.**
   Register the resulting URL as your Meta webhook callback (and pass the
   `hub.challenge` handshake manually once via a temporary *Catch Raw Hook*).
2. **Action — Code by Zapier (JavaScript):** paste the "Normalise inbound"
   snippet from `n8n-workflow.json` to extract `kind`, `lat`, `lng`,
   `button_id`, `text`.
3. **Paths by Zapier:**
   - **Path A — Location pin:** `kind = location`.
   - **Path B — Quick check-in button:** `button_id` starts with `checkin_`.
     Add a **Formatter → Utilities → Lookup Table** mapping button id → `lat,lng`:
     ```
     checkin_palco_sunset   -> -22.973810,-43.397020
     checkin_gourmet_square -> -22.976100,-43.394800
     checkin_espaco_favela  -> -22.974910,-43.393540
     checkin_entrada_brt    -> -22.979500,-43.392000
     ```
   - **Path C — Delay text:** `text` contains `atrasar 15` → jump to Zap 3 logic.
4. **Action — Storage by Zapier → Get Value** `active_stage` (set by Zap 1).
5. **Action — Webhooks → GET**
   `https://maps.googleapis.com/maps/api/distancematrix/json`
   with query `origins`, `destinations` (stage coords via another Lookup
   Table), `mode=walking`, `units=metric`, `key=<GOOGLE_MAPS_API_KEY>`.
6. **Action — Code by Zapier:** paste the "Parse / fallback" snippet
   (Haversine @ 4.5 km/h) from `n8n-workflow.json`.
7. **Action — Webhooks → POST** the `interactive` "walk estimate" message with
   `Manter Horário` / `Atrasar 15m` buttons (payload identical to the
   microservice's `_post_estimate`).

---

## Zap 3 — `[Atrasar 15m]` → Google Calendar patch

1. **Trigger — Webhooks → Catch Hook** (same hook as Zap 2; use a **Filter**:
   `button_id = delay_15` OR `text` contains `atrasar 15`).
2. **Action — Storage → Get Value** `active_event_id`.
3. **Action — Google Calendar → Find Event** by id (or Search).
4. **Action — Formatter → Date/Time:** add `15 minutes` to both start and end.
5. **Action — Google Calendar → Update Event:** new start/end,
   calendar `guilherme.dantas.sp@gmail.com`.
6. **Action — Webhooks → POST** confirmation text:
   `✅ Agenda ajustada: {{artist}} agora começa às {{new_time}} (+15 min).`

---

## Web check-in fallback with Zapier

Zapier cannot serve the HTML5 geolocation page. Options:

- Host the static `/checkin` page anywhere (GitHub Pages, Netlify) and have its
  JS `POST` to a **Catch Hook** URL instead of `/api/checkin`. Then route that
  hook through the Zap 2 logic (skip the normalise step; body is already
  `{user_id, latitude, longitude}`).

---

## Cost sanity check

- Zap 1 at 1-minute polling: **1,440 tasks/day** minimum, before any alerts.
- Zaps 2 + 3: ~4–8 tasks per check-in interaction.
- 3-day festival realistic total: **5,000–7,000 tasks** → Professional plan.

The self-hosted microservice does all of the above for **$0 marginal cost**.

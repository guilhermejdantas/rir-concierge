# What's next — sequenced prompts

State as of commit `c4e5cf7`: image builds, stack boots healthy, inbound routing
+ `/checkin` verified, scheduler degrades gracefully. Blocked only on credentials.

Critical path: **Step 1 (you) → Step 2 (you) → Step 4 (Claude Code)**.
Step 3 (Gemini) can run any time in parallel.

---

## Step 1 — Guilherme: provision credentials (~15 min, browser)

Nothing here is automatable — it needs your Google + Meta logins. Work through
`HANDOFF/STATUS.md` "## Guilherme" and tick each box. Condensed:

### Google Cloud (console.cloud.google.com, logged in as guilherme.dantas.sp@gmail.com)
1. Create project `rir-concierge-2026`.
2. APIs & Services ▸ Library → enable **Google Calendar API** and
   **Distance Matrix API**.
3. APIs & Services ▸ OAuth consent screen → **External** → app name + your email
   ×2 → add scope `.../auth/calendar` → add test user
   `guilherme.dantas.sp@gmail.com` → Save (leave in "Testing").
4. APIs & Services ▸ Credentials ▸ Create ▸ **OAuth client ID** ▸ **Desktop app**
   → Download JSON → save as `secrets/oauth_client.json`.
5. APIs & Services ▸ Credentials ▸ Create ▸ **API key** → copy → paste into
   `.env` as `GOOGLE_MAPS_API_KEY`. (Optionally restrict it to Distance Matrix.)

### Meta (developers.facebook.com)
6. Your WhatsApp app ▸ WhatsApp ▸ API Setup:
   - copy **Phone number ID** → `.env` `META_PHONE_NUMBER_ID`
   - generate a **permanent** access token (System User, `whatsapp_business_messaging`
     + `whatsapp_business_management`) → `.env` `META_ACCESS_TOKEN`
   - `.env` `WHATSAPP_GROUP_ID` = the destination number in E.164 without `+`
     (for a real group you need Evolution — see README).
7. `.env` `WHATSAPP_VERIFY_TOKEN` = any long random string (save it for Step 5).

---

## Step 2 — Guilherme: run the OAuth bootstrap (1 command)

```
cd C:\Users\guilh\rir-concierge
.venv\Scripts\python.exe scripts\google_oauth_bootstrap.py --client secrets\oauth_client.json --token secrets\oauth_token.json
```

Browser opens → log in as guilherme.dantas.sp@gmail.com → "Advanced ▸ go to
rir-concierge-2026 (unsafe)" → Allow. It writes `secrets/oauth_token.json`.

Sanity check:
```
.venv\Scripts\python.exe -c "from config import get_settings; from services.google_calendar import GoogleCalendarService; [print(s.start.isoformat(),'|',s.artist,'->',s.stage) for s in GoogleCalendarService(get_settings()).list_shows()]"
```

---

## Step 3 — Gemini (parallel): calendar audit

Paste the block from `HANDOFF/GEMINI.md` into Gemini. When it replies, paste its
FINAL EVENT TABLE into `HANDOFF/CALENDAR_AUDIT.md`.

---

## Step 4 — Claude Code: resume (paste this into Claude Code)

```
Secrets are in place now (secrets/oauth_token.json exists, .env has META_* and
GOOGLE_MAPS_API_KEY). Resume the RIR concierge deployment:

1. docker compose up -d  — confirm all healthy.
2. Run the calendar parser dry-run; cross-check every resolved show against
   HANDOFF/CALENDAR_AUDIT.md (if Gemini has filled it) and report mismatches.
3. Pick the earliest upcoming show. Temporarily create a throwaway test event
   ~31 min in the future on a real stage, wait for the -30 min poll, confirm the
   interactive list alert is sent (check logs + my WhatsApp).
4. simulate_webhook.py button --id delay_15 → confirm events.patch moved the
   test event +15 min and the group got the confirmation with the new time.
5. simulate_webhook.py location --lat <in-park> --lng <in-park> → confirm a real
   Distance Matrix walk estimate (method=distance_matrix, not fallback).
6. Delete the throwaway test event. Update HANDOFF/STATUS.md "## Claude Code".
7. Then walk me through Step 5.
```

---

## Step 5 — Guilherme: register the webhook + go live

1. Start a public HTTPS tunnel to `localhost:8000`. Either:
   - host: `cloudflared tunnel --url http://localhost:8000`, or
   - ask Gordon: *"run cloudflare/cloudflared as a container against
     http://app:8000 on the rir-concierge network and give me the https URL"*
2. Put that URL in `.env` as `PUBLIC_BASE_URL`, `docker compose up -d` to reload.
3. Meta ▸ WhatsApp ▸ Configuration → Callback URL
   `https://<tunnel>/webhook/whatsapp`, Verify Token = your `WHATSAPP_VERIFY_TOKEN`
   → Verify and save → Subscribe to **messages**.
4. Send a real location pin from the Galaxy S26 to the chat → confirm a reply.

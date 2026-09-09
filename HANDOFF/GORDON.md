# Handoff brief → Gordon (Docker Desktop AI agent)

Gordon runs with the working directory at the repo root
(`C:\Users\guilh\rir-concierge`). Copy the fenced block into Gordon.

---

```
You are working in the project at C:\Users\guilh\rir-concierge. It is a FastAPI +
Redis microservice with a docker-compose.yml (services: app, redis, and an
optional "evolution" profile). Do the following, stopping to report if any step
fails.

CONTEXT
- .env already exists with non-secret defaults. Some secrets are still blank
  placeholders (GOOGLE_MAPS_API_KEY, META_ACCESS_TOKEN, META_PHONE_NUMBER_ID,
  and secrets/oauth_token.json is not present yet). The service is designed to
  BOOT ANYWAY: with no Maps key it uses a Haversine fallback; calendar polling
  will log an auth error every 60s until secrets/oauth_token.json exists. That
  is expected at this stage — do not treat those calendar-auth log lines as a
  build failure.

TASKS
1. Validate the build:
   docker compose build app
   Report image size and any warnings.

2. Start only Redis first and confirm health:
   docker compose up -d redis
   docker compose ps
   docker exec rir-redis redis-cli ping   # expect: PONG

3. Start the app:
   docker compose up -d app
   Wait for health, then:
   curl -s http://localhost:8000/healthz
   Expect JSON with "redis": true. "status" may be "ok".

4. Tail the app logs for 20 lines and report:
   docker compose logs --tail=20 app
   Confirm you see "Concierge up." and the APScheduler start line.

5. Exercise the inbound path WITHOUT WhatsApp (the repo ships a simulator).
   Run it from inside a throwaway python container on the compose network, or
   from the host if python is available:
   python scripts/simulate_webhook.py --url http://localhost:8000 list-reply --id checkin_palco_sunset
   python scripts/simulate_webhook.py --url http://localhost:8000 text --body "status"
   Report the HTTP status returned (expect 200) and the matching app log lines.
   NOTE: if WhatsApp creds are still placeholders the outbound send will raise a
   WhatsAppError that is logged (not crash) — capture that log line and report it.

6. Check the /checkin page renders:
   curl -s "http://localhost:8000/checkin?u=test123" | head -c 400

7. OPTIONAL — public tunnel for the Meta webhook. If I ask for it, run:
   docker run --rm --network rir-concierge_default -p 0:0 cloudflare/cloudflared:latest tunnel --url http://app:8000
   and give me the generated https URL. Do not make this permanent.

REPORT BACK
- Build result + image size.
- Output of steps 3, 4, 5, 6 verbatim.
- Any error log lines, with your read on whether they are "expected until
  secrets are added" or a real bug.
- Write your checklist results into HANDOFF/STATUS.md under "## Gordon".

DO NOT
- Do not edit application source files (*.py). If something looks like a code
  bug, report it for Claude Code to fix.
- Do not put real secrets into any file you commit.
- Do not run the "evolution" profile unless I ask.
```

---

## What Gordon CANNOT do

- Google/Meta browser logins → no OAuth token, no Meta access token.
- Provision GCP resources.

## Division of labour recap

| | Gemini | Gordon | Guilherme | Claude Code |
|---|---|---|---|---|
| Calendar events correct | ✅ | | | verifies |
| GCP project / APIs / OAuth client / Maps key | | | ✅ | |
| `oauth_token.json` (browser login) | | | ✅ | |
| Meta app + tokens | | | ✅ | |
| Build image, run stack, smoke-test | | ✅ | | |
| Public tunnel | | ✅ (on request) | or host | |
| Integration test + bug fixes | | | | ✅ |

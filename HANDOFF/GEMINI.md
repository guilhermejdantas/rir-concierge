# Handoff brief → Gemini (Google Workspace)

Copy everything in the fenced block below into Gemini (the one with Calendar
access on `guilherme.dantas.sp@gmail.com`).

---

```
You have access to my Google Calendar (guilherme.dantas.sp@gmail.com). You
previously created the Rock in Rio 2026 show events on it. A separate webhook
microservice will now read that calendar to send WhatsApp alerts, so every show
event must match the exact format its parser expects.

TASK: Audit every event between 2026-09-11 00:00 and 2026-09-13 23:59
(America/Sao_Paulo) and fix each one so it conforms to ALL rules below. Then
give me a markdown table of the final state.

FORMAT RULES
1. Title MUST be:  "<Artist> — <Stage>"
   - Use an em dash " — " OR a hyphen " - " between artist and stage. Nothing else.
   - Example: "Foo Fighters — Palco Mundo"
2. <Stage> MUST be exactly one of these strings (accents included):
   - Palco Mundo
   - Palco Sunset
   - Espaço Favela
   - New Dance Order
   - Gourmet Square
   If a show is on a stage not in this list, use the closest match and tell me.
3. The event MUST be a TIMED event with a real start and end time
   (start.dateTime / end.dateTime). No all-day events. If any show is all-day,
   set a plausible duration (default 60 min) and flag it for me to confirm.
4. Timezone MUST be America/Sao_Paulo (UTC-3).
5. Put the literal text "Rock in Rio 2026" in the event LOCATION field (this is
   a redundant keyword the parser also checks). Keep any existing address after
   it, e.g. "Rock in Rio 2026 — Parque Olímpico, Rio de Janeiro".
6. Do NOT change the start times unless a rule above forces it. If you must
   change a time, list it under "CHANGES I MADE".

DO NOT
- Do not create new events unless I gave you a show that is missing.
- Do not delete events.
- Do not touch events outside the 2026-09-11..13 window.
- Do not invent artists or set times you are unsure about — flag those instead.

OUTPUT (in this order)
A) "CHANGES I MADE" — bullet list of every edit, per event.
B) "FLAGGED FOR CONFIRMATION" — anything ambiguous (unknown stage, all-day fix,
   guessed end time).
C) "FINAL EVENT TABLE" — columns: Day | Start | End | Artist | Stage | Title (verbatim) | Location (verbatim)
D) Confirm the count of events per day.

After you show me the output I will paste your FINAL EVENT TABLE into the
project's HANDOFF/CALENDAR_AUDIT.md so the other tools can verify against it.
```

---

## What Gemini CANNOT do (do not ask it to)

- Create the GCP project / OAuth client / API key.
- Produce `secrets/oauth_token.json` — that needs the interactive
  `scripts/google_oauth_bootstrap.py` run.
- Anything with WhatsApp / Meta.

## After Gemini responds

Paste its **FINAL EVENT TABLE** into
[`CALENDAR_AUDIT.md`](CALENDAR_AUDIT.md). Claude Code will then dry-run the
parser (`GoogleCalendarService.list_shows`) against those titles/stages and
confirm each show resolves.

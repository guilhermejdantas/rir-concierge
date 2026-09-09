# Calendar audit — paste Gemini's FINAL EVENT TABLE here

Gemini (Workspace) fills this in after auditing
`guilherme.dantas.sp@gmail.com` for 2026-09-11..13. Claude Code then dry-runs
`GoogleCalendarService.list_shows()` against it to confirm every show resolves
to a canonical stage.

## Canonical stage names the parser accepts
`Palco Mundo` · `Palco Sunset` · `Espaço Favela` · `New Dance Order` · `Gourmet Square`

(`Entrada / BRT` and the hotel are POIs for routing only — not show stages.)

## FINAL EVENT TABLE

_pending Gemini_

| Day | Start | End | Artist | Stage | Title (verbatim) | Location (verbatim) |
|-----|-------|-----|--------|-------|------------------|---------------------|
|     |       |     |        |       |                  |                     |

## Claude Code verification

_pending — run after the table is filled:_

```
.venv/Scripts/python.exe -c "from config import get_settings; from services.google_calendar import GoogleCalendarService; [print(s.start.isoformat(), '|', s.artist, '->', s.stage) for s in GoogleCalendarService(get_settings()).list_shows()]"
```

"""FastAPI application for the Rock in Rio 2026 WhatsApp Festival Concierge.

Endpoints
---------
``GET  /health``               – liveness/readiness probe.
                                (NOT ``/healthz`` — Google Front End on Cloud
                                Run intercepts that exact path.)
``GET  /webhook/whatsapp``     – Meta verification handshake (hub.challenge).
``POST /webhook/whatsapp``     – inbound message + button router.
``GET  /checkin``              – HTML5 geolocation fallback page.
``POST /api/checkin``          – receives browser coordinates and routes them.

An APScheduler ``AsyncIOScheduler`` polls Google Calendar every
``SCHEDULER_POLL_SECONDS`` and dispatches the -30 minute proactive alerts.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from concierge import Concierge
from config import Settings, get_settings
from models import CheckinPayload, Coordinates
from services.google_calendar import GoogleCalendarService
from services.maps import MapsService
from services.reasoning import GeminiReasoner
from services.state import StateStore, open_state_store
from services.whatsapp import WhatsAppService

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("concierge.main")


class AppState:
    """Container for singletons created on startup and closed on shutdown."""

    settings: Settings
    http: httpx.AsyncClient
    redis: StateStore
    calendar: GoogleCalendarService
    reasoner: GeminiReasoner
    scheduler: AsyncIOScheduler

    def build_concierge(self) -> tuple[Concierge, MapsService, WhatsAppService]:
        """Create a request-scoped Concierge with fresh service wrappers.

        The Maps/WhatsApp wrappers share the long-lived ``httpx`` client, so
        there is nothing to close per request.
        """
        maps = MapsService(self.settings, client=self.http)
        whatsapp = WhatsAppService(self.settings, client=self.http)
        concierge = Concierge(
            self.settings, self.calendar, maps, whatsapp, self.redis, self.reasoner
        )
        return concierge, maps, whatsapp


state = AppState()


async def _poll_alerts() -> None:
    """Scheduler job: dispatch any due -30 min alerts."""
    concierge, _, _ = state.build_concierge()
    try:
        await concierge.fire_due_alerts()
    except Exception:  # noqa: BLE001 - never let the scheduler thread die
        logger.exception("Alert poll iteration failed.")


async def _send_announcement(path: str) -> None:
    """One-off scheduler job: post a static message file to the group."""
    _, _, whatsapp = state.build_concierge()
    try:
        with open(path, encoding="utf-8") as fh:
            body = fh.read().strip()
        async with whatsapp:
            await whatsapp.send_text(body)
        logger.info("Announcement sent: %s", path)
    except Exception:  # noqa: BLE001
        logger.exception("Announcement %s failed to send.", path)


def _schedule_announcements(scheduler: "AsyncIOScheduler", settings: Settings) -> None:
    """Register a one-off ``date`` job for each future-dated announcement file.

    Files live in ``announcements/`` and are named
    ``YYYY-MM-DD_HHMM_slug.txt`` (time in ``FESTIVAL_TIMEZONE``). Past-dated
    files are skipped; the job id is the filename so a restart never double-books.
    """
    import re
    from datetime import datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(settings.festival_timezone)
    now = datetime.now(tz)
    folder = Path(__file__).parent / "announcements"
    if not folder.is_dir():
        return
    pattern = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})_.+\.txt$")
    for file in sorted(folder.glob("*.txt")):
        m = pattern.match(file.name)
        if not m:
            logger.warning("Announcement file ignored (bad name): %s", file.name)
            continue
        y, mo, d, hh, mm = (int(x) for x in m.groups())
        when = datetime(y, mo, d, hh, mm, tzinfo=tz)
        if when <= now:
            logger.info("Announcement %s is in the past; skipping.", file.name)
            continue
        scheduler.add_job(
            _send_announcement,
            "date",
            run_date=when,
            args=[str(file)],
            id=f"announce:{file.name}",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info("Announcement scheduled: %s at %s", file.name, when.isoformat())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create and tear down shared resources."""
    settings = get_settings()
    state.settings = settings
    state.http = httpx.AsyncClient(timeout=15.0)
    state.redis = await open_state_store(settings.redis_url)
    state.calendar = GoogleCalendarService(settings)
    state.reasoner = GeminiReasoner(settings)

    state.scheduler = AsyncIOScheduler(timezone=settings.festival_timezone)
    state.scheduler.add_job(
        _poll_alerts,
        "interval",
        seconds=settings.scheduler_poll_seconds,
        id="poll_alerts",
        max_instances=1,
        coalesce=True,
    )
    _schedule_announcements(state.scheduler, settings)
    state.scheduler.start()
    logger.info(
        "Concierge up. provider=%s calendar=%s poll=%ss lead=%smin "
        "state=%s reasoning=%s",
        settings.whatsapp_provider,
        settings.target_calendar_id,
        settings.scheduler_poll_seconds,
        settings.alert_lead_minutes,
        type(state.redis).__name__,
        "gemini" if state.reasoner.enabled else "keyword-only",
    )
    try:
        yield
    finally:
        state.scheduler.shutdown(wait=False)
        await state.redis.aclose()
        await state.http.aclose()
        logger.info("Concierge shut down cleanly.")


app = FastAPI(
    title="Rock in Rio 2026 – WhatsApp Festival Concierge",
    version="1.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Health                                                                       #
# --------------------------------------------------------------------------- #
@app.get("/health", response_class=JSONResponse)
async def health() -> dict[str, Any]:
    """Report process health and state-store connectivity."""
    store_ok = False
    try:
        store_ok = bool(await state.redis.ping())
    except Exception:  # noqa: BLE001
        store_ok = False
    return {
        "status": "ok" if store_ok else "degraded",
        "redis": store_ok,
        "state_store": type(state.redis).__name__,
        "provider": state.settings.whatsapp_provider,
        "reasoning": "gemini" if state.reasoner.enabled else "keyword-only",
        "scheduler_running": state.scheduler.running,
    }


# --------------------------------------------------------------------------- #
# WhatsApp webhook – verification handshake                                    #
# --------------------------------------------------------------------------- #
@app.get("/webhook/whatsapp", response_class=PlainTextResponse)
async def verify_webhook(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
) -> Response:
    """Meta Cloud API webhook verification (GET handshake)."""
    if mode == "subscribe" and token == state.settings.whatsapp_verify_token:
        return PlainTextResponse(challenge, status_code=status.HTTP_200_OK)
    logger.warning("Webhook verification rejected (mode=%s).", mode)
    return PlainTextResponse("forbidden", status_code=status.HTTP_403_FORBIDDEN)


# --------------------------------------------------------------------------- #
# WhatsApp webhook – inbound router                                            #
# --------------------------------------------------------------------------- #
@app.post("/webhook/whatsapp")
async def inbound_webhook(request: Request) -> JSONResponse:
    """Route inbound messages / button clicks to the concierge.

    Always returns HTTP 200 quickly so the provider does not retry; processing
    errors are logged, not surfaced.
    """
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=200)

    concierge, _, whatsapp = state.build_concierge()

    try:
        messages = whatsapp.parse_inbound(payload)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to parse inbound payload.")
        return JSONResponse({"ok": True, "handled": 0}, status_code=200)

    handled = 0
    for msg in messages:
        try:
            await _dispatch(concierge, msg)
            handled += 1
        except Exception:  # noqa: BLE001
            logger.exception("Dispatch failed for message kind=%s", msg.kind)
    return JSONResponse({"ok": True, "handled": handled}, status_code=200)


async def _dispatch(concierge: Concierge, msg: Any) -> None:
    """Apply routing rules for a single normalised inbound message."""
    if msg.kind == "location" and msg.latitude is not None and msg.longitude is not None:
        await concierge.handle_coordinates(
            Coordinates(
                latitude=msg.latitude, longitude=msg.longitude, source="location_pin"
            )
        )
        return

    if msg.kind == "button" and msg.button_id:
        bid = msg.button_id
        if bid.startswith("checkin_"):
            await concierge.handle_button_checkin(bid)
        elif bid == "delay_15":
            await concierge.handle_delay()
        elif bid == "keep_schedule":
            await concierge.handle_keep()
        elif bid == "skip_show":
            await concierge.handle_skip()
        else:
            logger.info("Unhandled button id: %s", bid)
        return

    if msg.kind == "text" and msg.text:
        await concierge.handle_text(msg.text)
        return

    logger.debug("Ignoring message kind=%s", msg.kind)


# --------------------------------------------------------------------------- #
# Browser geolocation fallback                                                 #
# --------------------------------------------------------------------------- #
# NOTE: this is a plain string with a single ``__USER_ID_JSON__`` placeholder
# substituted via ``str.replace`` — NOT ``str.format`` — so braces are literal.
_CHECKIN_HTML = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Check-in Rock in Rio</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; margin: 0;
         background: #0b0b12; color: #f4f4f7; display: flex; min-height: 100vh;
         align-items: center; justify-content: center; }
  main { max-width: 380px; padding: 28px; text-align: center; }
  h1 { font-size: 1.3rem; margin: 0 0 8px; }
  p { opacity: .8; line-height: 1.45; }
  button { margin-top: 18px; padding: 14px 22px; font-size: 1rem; border: 0;
           border-radius: 999px; background: #e4002b; color: #fff; font-weight: 700; }
  #out { margin-top: 16px; font-size: .92rem; min-height: 1.4em; }
  .ok { color: #59d98a; } .err { color: #ff6b6b; }
</style>
</head>
<body>
<main>
  <h1>📍 Check-in do Festival</h1>
  <p>Toque no botão para enviar sua localização atual de alta precisão ao
     concierge. Nada é armazenado além das coordenadas do check-in.</p>
  <button id="go">Enviar minha localização</button>
  <div id="out"></div>
</main>
<script>
  var USER_ID = __USER_ID_JSON__;
  var out = document.getElementById('out');
  document.getElementById('go').addEventListener('click', function () {
    if (!navigator.geolocation) {
      out.textContent = 'Geolocalização não suportada neste navegador.';
      out.className = 'err';
      return;
    }
    out.textContent = 'Obtendo posição…';
    out.className = '';
    navigator.geolocation.getCurrentPosition(function (pos) {
      var body = {
        user_id: USER_ID,
        latitude: pos.coords.latitude,
        longitude: pos.coords.longitude,
        accuracy_m: pos.coords.accuracy
      };
      fetch('/api/checkin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(function (r) {
        if (r.ok) {
          out.textContent = 'Localização enviada! Pode voltar ao WhatsApp. ✅';
          out.className = 'ok';
        } else {
          out.textContent = 'Falha ao enviar (HTTP ' + r.status + ').';
          out.className = 'err';
        }
      }).catch(function () {
        out.textContent = 'Erro de rede ao enviar.';
        out.className = 'err';
      });
    }, function (err) {
      out.textContent = 'Não foi possível obter a localização: ' + err.message;
      out.className = 'err';
    }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
  });
</script>
</body>
</html>
"""


@app.get("/checkin", response_class=HTMLResponse)
async def checkin_page(u: str = Query(..., min_length=1, max_length=128)) -> HTMLResponse:
    """Serve the minimal HTML5 geolocation check-in page for user ``u``."""
    import json as _json

    html = _CHECKIN_HTML.replace("__USER_ID_JSON__", _json.dumps(u))
    return HTMLResponse(html)


@app.post("/api/checkin")
async def api_checkin(payload: CheckinPayload) -> JSONResponse:
    """Receive browser coordinates and run the standard walk-time flow."""
    concierge, _, _ = state.build_concierge()
    origin = Coordinates(
        latitude=payload.latitude,
        longitude=payload.longitude,
        accuracy_m=payload.accuracy_m,
        source="web_checkin",
    )
    try:
        estimate = await concierge.handle_coordinates(origin)
    except Exception:  # noqa: BLE001
        logger.exception("Web check-in processing failed.")
        return JSONResponse({"ok": False}, status_code=500)
    return JSONResponse(
        {
            "ok": True,
            "estimate": estimate.model_dump() if estimate else None,
        }
    )


if __name__ == "__main__":  # pragma: no cover
    import os

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",  # noqa: S104 - container binding
        port=int(os.environ.get("PORT", "8000")),  # Cloud Run injects PORT
        reload=get_settings().app_env == "dev",
    )

"""Core orchestration for the Rock in Rio 2026 WhatsApp Festival Concierge.

The :class:`Concierge` ties the three services together and owns all business
logic:

* :meth:`fire_due_alerts`      – called by the scheduler; sends the -30 min alert.
* :meth:`handle_coordinates`   – walk-time computation + "keep / delay" prompt.
* :meth:`handle_delay`         – +15 min ``events.patch`` and confirmation.
* :meth:`handle_skip`          – marks the active show as skipped for this session.

Ephemeral state (which show an alert is currently open for, dedupe locks) lives
in a :class:`~services.state.StateStore` — Redis when configured (so multiple
app replicas share the alert lock), otherwise an in-process fallback.

Free-text group messages are routed by a deterministic keyword matcher. When a
:class:`~services.reasoning.Reasoner` is supplied and enabled (Claude or
Gemini, per :func:`services.reasoning.build_reasoner`) it is consulted first
as a fuzzy intent classifier, with the keyword matcher as the guaranteed
fallback.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from config import BUTTON_TO_POI, FESTIVAL_POIS, QUICK_CHECKIN_BUTTONS, Settings
from models import Coordinates, FestivalShow, WalkEstimate
from quiz_service import QuizService
from services.google_calendar import CalendarError, GoogleCalendarService
from services.maps import MapsService
from services.reasoning import Reasoner
from services.state import StateStore
from services.whatsapp import WhatsAppService

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"@(\d{10,13})\b")


def _mentions_in(text: str) -> list[str]:
    """Phone numbers referenced as ``@<digits>`` in an outbound message."""
    return _MENTION_RE.findall(text)


# Redis keys.
_KEY_ACTIVE_SHOW = "rir:active_show"          # JSON blob of the show an alert is open for
_KEY_ALERTED_PREFIX = "rir:alerted:"          # per-event dedupe lock for alerts
_KEY_SKIPPED_PREFIX = "rir:skipped:"          # per-event skip marker


class Concierge:
    """Stateful coordinator for one festival deployment."""

    def __init__(
        self,
        settings: Settings,
        calendar: GoogleCalendarService,
        maps: MapsService,
        whatsapp: WhatsAppService,
        redis: StateStore,
        reasoner: Optional[Reasoner] = None,
        quiz: Optional[QuizService] = None,
    ) -> None:
        self._s = settings
        self._cal = calendar
        self._maps = maps
        self._wa = whatsapp
        self._redis = redis
        self._reasoner = reasoner
        self._quiz = quiz or QuizService(redis)
        self._tz = ZoneInfo(settings.festival_timezone)

    # ================================================================== #
    # State helpers                                                       #
    # ================================================================== #
    async def _set_active_show(self, show: FestivalShow) -> None:
        await self._redis.set(
            _KEY_ACTIVE_SHOW,
            show.model_dump_json(),
            ex=self._s.state_ttl_seconds,
        )

    async def get_active_show(self) -> Optional[FestivalShow]:
        """Return the show a -30 min alert is currently open for, if any."""
        blob = await self._redis.get(_KEY_ACTIVE_SHOW)
        if not blob:
            return None
        try:
            return FestivalShow.model_validate_json(blob)
        except ValueError:  # pragma: no cover - corrupt cache
            logger.warning("Corrupt active-show cache; clearing.")
            await self._redis.delete(_KEY_ACTIVE_SHOW)
            return None

    async def _resolve_target_show(self) -> Optional[FestivalShow]:
        """The show a check-in should route to: active alert, else next upcoming."""
        active = await self.get_active_show()
        if active is not None:
            return active
        try:
            return self._cal.next_show()
        except CalendarError as exc:  # pragma: no cover - network error
            logger.error("Cannot resolve next show: %s", exc)
            return None

    # ================================================================== #
    # A. Proactive alert (-30 min)                                        #
    # ================================================================== #
    async def fire_due_alerts(self, *, now: Optional[datetime] = None) -> None:
        """Send the -30 min alert for any show entering the alert window.

        Idempotent: a per-event Redis lock guarantees one alert per show even
        with overlapping scheduler ticks or multiple replicas.
        """
        now = now or datetime.now(self._tz)
        lead = timedelta(minutes=self._s.alert_lead_minutes)
        try:
            shows = self._cal.list_shows()
        except CalendarError as exc:  # pragma: no cover - network error
            logger.error("Alert poll skipped, calendar error: %s", exc)
            return

        for show in shows:
            delta = show.start - now
            if not (timedelta(0) < delta <= lead):
                continue
            if await self._redis.get(_KEY_SKIPPED_PREFIX + show.event_id):
                continue
            lock_key = _KEY_ALERTED_PREFIX + show.event_id
            # SET NX with TTL == atomic "claim this alert".
            claimed = await self._redis.set(
                lock_key, now.isoformat(), nx=True, ex=self._s.state_ttl_seconds
            )
            if not claimed:
                continue
            try:
                await self._send_alert(show)
                await self._set_active_show(show)
            except Exception:  # noqa: BLE001 - release lock, let next tick retry
                logger.exception("Alert send failed for %s; releasing lock.", show.event_id)
                await self._redis.delete(lock_key)

    async def _send_alert(self, show: FestivalShow) -> None:
        text = (
            f"🔔 *Próximo Show:* *{show.artist}* no *{show.stage}* "
            f"às {show.local_time_str}.\n"
            "Como vocês estão no festival? Enviem a localização atual ou cliquem "
            "no botão abaixo para calcularmos o tempo de deslocamento."
        )
        rows: list[tuple[str, str, Optional[str]]] = [
            ("📍 Palco Sunset", "checkin_palco_sunset", "Check-in a partir do Palco Sunset"),
            ("🍔 Gourmet Square", "checkin_gourmet_square", "Check-in a partir da praça gourmet"),
            ("🍺 Espaço Favela", "checkin_espaco_favela", "Check-in a partir do Espaço Favela"),
            ("🚪 Entrada / BRT", "checkin_entrada_brt", "Check-in a partir da entrada principal"),
            ("⏩ Pular Show", "skip_show", "Não avisar sobre este show"),
        ]
        await self._wa.send_list(
            text,
            rows,
            header="Rock in Rio 2026",
            button_label="Fazer check-in",
            section_title="Onde vocês estão?",
        )
        logger.info("Alert sent for %s @ %s (%s).", show.artist, show.stage, show.local_time_str)

    # ================================================================== #
    # B. Geolocation -> walk time                                         #
    # ================================================================== #
    async def handle_coordinates(self, origin: Coordinates) -> Optional[WalkEstimate]:
        """Compute and post the walk estimate for a received location."""
        show = await self._resolve_target_show()
        if show is None:
            await self._wa.send_text(
                "📍 Recebi a localização, mas não há nenhum show ativo na agenda "
                "agora. Guardando para o próximo aviso."
            )
            return None

        estimate = await self._maps.walk_estimate(origin, show.stage)
        await self._post_estimate(show, estimate)
        return estimate

    async def handle_button_checkin(self, button_id: str) -> Optional[WalkEstimate]:
        """Handle a quick check-in button whose id maps to a fixed POI."""
        poi = BUTTON_TO_POI.get(button_id)
        if poi is None:
            return None
        coord = FESTIVAL_POIS[poi]
        origin = Coordinates(
            latitude=coord.lat, longitude=coord.lng, source="button"
        )
        return await self.handle_coordinates(origin)

    async def _post_estimate(self, show: FestivalShow, est: WalkEstimate) -> None:
        note = "" if est.method == "distance_matrix" else " _(estimativa offline)_"
        text = (
            f"📍 Vocês estão a {est.distance_m}m (~{est.duration_min} min de "
            f"caminhada) do {show.stage}.{note} Passagem livre por pontos de "
            "hidratação e Bob's no caminho. Desejam manter o horário ou atrasar "
            "a agenda em 15 minutos?"
        )
        await self._wa.send_buttons(
            text,
            [("Manter Horário", "keep_schedule"), ("Atrasar 15m", "delay_15")],
        )

    # ================================================================== #
    # C. Live rescheduling                                                #
    # ================================================================== #
    async def handle_delay(self) -> Optional[FestivalShow]:
        """Advance the active (or next) show by +15 min and confirm."""
        show = await self._resolve_target_show()
        if show is None:
            await self._wa.send_text(
                "Não encontrei um show para atrasar. A agenda parece livre. 🎸"
            )
            return None
        try:
            updated = self._cal.shift_event(
                show.event_id,
                timedelta(minutes=self._s.reschedule_delta_minutes),
            )
        except CalendarError as exc:
            logger.error("Reschedule failed: %s", exc)
            await self._wa.send_text(
                "⚠️ Não consegui atualizar o Google Calendar agora. "
                "Tentem novamente em instantes."
            )
            return None

        await self._set_active_show(updated)
        await self._wa.send_text(
            f"✅ Agenda ajustada: *{updated.artist}* no *{updated.stage}* agora "
            f"começa às *{updated.local_time_str}* "
            f"(+{self._s.reschedule_delta_minutes} min). Curtam sem pressa!"
        )
        return updated

    async def handle_keep(self) -> None:
        """Acknowledge a 'keep schedule' decision."""
        show = await self.get_active_show()
        suffix = f" Nos vemos no *{show.stage}*!" if show else ""
        await self._wa.send_text(f"👍 Horário mantido.{suffix}")

    async def handle_skip(self) -> None:
        """Mark the active show as skipped so no further nudges fire for it."""
        show = await self.get_active_show()
        if show is None:
            await self._wa.send_text("Não há show ativo para pular.")
            return
        await self._redis.set(
            _KEY_SKIPPED_PREFIX + show.event_id,
            "1",
            ex=self._s.state_ttl_seconds,
        )
        await self._redis.delete(_KEY_ACTIVE_SHOW)
        await self._wa.send_text(
            f"⏩ Ok, pulando *{show.artist}*. Aviso vocês no próximo show."
        )

    # ================================================================== #
    # Trivia quiz                                                         #
    # ================================================================== #
    async def run_quiz_round(self) -> None:
        """Scheduler job: open the next trivia question in the group."""
        try:
            opened = await self._quiz.start_round()
        except Exception:  # noqa: BLE001
            logger.exception("Quiz round failed to open.")
            return
        if opened is None:
            logger.info("Quiz bank exhausted; no round sent.")
            return
        text, buttons = opened
        await self._wa.send_buttons(text, buttons)

    async def handle_quiz_answer(
        self, sender_jid: Optional[str], push_name: Optional[str], raw_choice: str
    ) -> bool:
        """Route a possible quiz answer. Returns True if it was consumed."""
        reply = await self._quiz.submit_answer(sender_jid, push_name, raw_choice)
        if reply is None:
            return False
        mentions = _mentions_in(reply)
        await self._wa.send_text(reply, mentions=mentions or None)
        return True

    async def send_leaderboard(self) -> None:
        await self._wa.send_text(await self._quiz.leaderboard_text())

    # ================================================================== #
    # Text intent routing                                                 #
    # ================================================================== #
    async def handle_text(self, text: str) -> None:
        """Interpret a free-text group message and act on a recognised intent.

        Resolution order:
          1. LLM classifier (Claude or Gemini, if enabled and confident) →
          2. deterministic keyword matcher →
          3. stay silent (it is a group chat).
        """
        intent = await self._resolve_intent(text)
        if intent == "delay_15":
            await self.handle_delay()
        elif intent == "keep_schedule":
            await self.handle_keep()
        elif intent == "skip_show":
            await self.handle_skip()
        elif intent == "status":
            await self._send_status()

    async def _resolve_intent(self, text: str) -> str:
        """Return one of the concierge intent labels, or ``"none"``."""
        if self._reasoner is not None and self._reasoner.enabled:
            try:
                result = await self._reasoner.classify_intent(text)
            except Exception:  # noqa: BLE001 - never let the model break routing
                logger.warning("Reasoner errored; using keyword router.", exc_info=True)
            else:
                if self._reasoner.is_confident(result):
                    logger.info(
                        "%s intent=%s conf=%.2f for %r",
                        self._reasoner.label, result.intent, result.confidence, text[:80],
                    )
                    return result.intent
        return self._keyword_intent(text)

    @staticmethod
    def _keyword_intent(text: str) -> str:
        """Deterministic fallback classifier."""
        low = text.strip().lower()
        if "atrasar 15m" in low or "atrasar 15 minutos" in low or "atrasar 15" in low:
            return "delay_15"
        if low in {"manter horario", "manter horário", "manter"}:
            return "keep_schedule"
        if low in {"pular show", "pular"}:
            return "skip_show"
        if low in {"status", "agenda"}:
            return "status"
        return "none"

    async def _send_status(self) -> None:
        try:
            show = self._cal.next_show()
        except CalendarError:  # pragma: no cover - network error
            show = None
        if show is None:
            await self._wa.send_text("Sem próximos shows na agenda. 🎉")
            return
        await self._wa.send_text(
            f"🗓️ Próximo: *{show.artist}* — *{show.stage}* às {show.local_time_str}."
        )

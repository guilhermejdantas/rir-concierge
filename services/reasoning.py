"""LLM-backed natural-language reasoning for the festival concierge.

Two interchangeable engines — :class:`ClaudeReasoner` (Anthropic API) and
:class:`GeminiReasoner` (Google AI Studio, ``google-genai`` SDK) — sit in
front of the deterministic keyword router in
:meth:`concierge.Concierge.handle_text`. :func:`build_reasoner` picks one
per :attr:`config.Settings.reasoning_provider`.

Design rules (both engines):

* **Optional.** No API key → :attr:`enabled` is ``False`` and callers fall
  back to the keyword logic. The concierge never depends on the model being
  reachable.
* **Constrained.** The model only ever returns one of a fixed set of intent
  labels plus a confidence score (JSON schema enforced). It cannot free-type a
  reply to the group — message wording stays owned by the concierge templates.
* **Fail safe.** Any SDK/network/parse error yields ``intent="none"`` so the
  deterministic path takes over.
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol

from config import Settings
from models import IntentResult

logger = logging.getLogger(__name__)


class Reasoner(Protocol):
    """Interface shared by :class:`ClaudeReasoner` and :class:`GeminiReasoner`."""

    @property
    def enabled(self) -> bool: ...

    @property
    def label(self) -> str: ...

    async def classify_intent(self, text: str) -> IntentResult: ...

    def is_confident(self, result: IntentResult) -> bool: ...

_SYSTEM_INSTRUCTION = (
    "You classify a single WhatsApp group message from friends at the Rock in "
    "Rio 2026 music festival. The group uses a concierge bot that can delay the "
    "next show on their shared calendar by 15 minutes, keep the current "
    "schedule, skip the upcoming show, or report the next show. Classify the "
    "user's INTENT. Portuguese (BR) slang is expected. Only emit an intent when "
    "the message clearly expresses it; otherwise use 'none'. Never guess.\n\n"
    "Intents:\n"
    "- delay_15: they want to push the next show / arrive later / need more "
    "time (e.g. 'bora atrasar', 'cheguem mais tarde', 'me dá 15 min', "
    "'ainda tô no bar').\n"
    "- keep_schedule: they want to keep the current time / are on their way / "
    "'pode manter', 'tamo chegando', 'no horário'.\n"
    "- skip_show: they want to skip / not watch the upcoming show ('pula esse', "
    "'esse não', 'vamo direto pro próximo').\n"
    "- status: they ask what's next / the schedule ('qual o próximo?', "
    "'que horas começa?', 'cadê a agenda').\n"
    "- none: small talk, unrelated, ambiguous, or about food/bathrooms/meeting "
    "points.\n\n"
    "confidence is your calibrated probability (0..1) that the intent is right."
)


class GeminiReasoner:
    """Thin async wrapper over ``google-genai`` for intent classification."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._model = settings.gemini_model
        self._min_conf = settings.gemini_min_confidence
        self._client = None
        if not settings.gemini_api_key:
            logger.info("GEMINI_API_KEY not set; reasoning engine disabled.")
            return
        try:
            from google import genai  # imported lazily so the dep stays optional

            self._client = genai.Client(api_key=settings.gemini_api_key)
            logger.info("Gemini reasoning engine ready (model=%s).", self._model)
        except Exception:  # noqa: BLE001 - never fail startup over an optional dep
            logger.exception("Could not initialise google-genai client; disabling.")
            self._client = None

    @property
    def enabled(self) -> bool:
        """True when a client is configured and usable."""
        return self._client is not None

    @property
    def label(self) -> str:
        return "gemini"

    async def classify_intent(self, text: str) -> IntentResult:
        """Return the best intent label + confidence for ``text``.

        Always returns an :class:`IntentResult`; on any failure the result is
        ``intent="none", confidence=0.0`` so the caller uses keyword matching.
        """
        if not self.enabled or not text.strip():
            return IntentResult(intent="none", confidence=0.0)

        try:
            from google.genai import types

            resp = await self._client.aio.models.generate_content(  # type: ignore[union-attr]
                model=self._model,
                contents=text.strip()[:2000],
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=IntentResult,
                    temperature=0.0,
                    max_output_tokens=800,
                ),
            )
        except Exception:  # noqa: BLE001 - fall back to deterministic routing
            logger.warning("Gemini classify_intent failed; falling back.", exc_info=True)
            return IntentResult(intent="none", confidence=0.0)

        parsed: Optional[IntentResult] = getattr(resp, "parsed", None)
        if isinstance(parsed, IntentResult):
            return parsed
        if isinstance(parsed, list) and parsed:
            try:
                return IntentResult.model_validate(parsed[0])
            except (ValueError, TypeError):
                pass
        if isinstance(parsed, dict):
            try:
                return IntentResult.model_validate(parsed)
            except (ValueError, TypeError):
                pass
        return self._salvage_json(getattr(resp, "text", None))

    @staticmethod
    def _salvage_json(raw: Optional[str]) -> IntentResult:
        """Best-effort recovery when the model wraps JSON in prose / fences."""
        if not raw:
            return IntentResult(intent="none", confidence=0.0)
        import json as _json
        import re

        candidates: list[str] = []
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
        if fence:
            candidates.append(fence.group(1))
        brace = re.search(r"\{.*\}", raw, re.S)
        if brace:
            candidates.append(brace.group(0))
        for cand in candidates:
            try:
                return IntentResult.model_validate(_json.loads(cand))
            except (ValueError, TypeError):
                continue
        logger.warning("Unparseable Gemini response: %r", raw[:200])
        return IntentResult(intent="none", confidence=0.0)

    def is_confident(self, result: IntentResult) -> bool:
        """Whether ``result`` clears the configured confidence threshold."""
        return result.intent != "none" and result.confidence >= self._min_conf


class ClaudeReasoner:
    """Thin async wrapper over the ``anthropic`` SDK for intent classification."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._model = settings.claude_model
        self._min_conf = settings.claude_min_confidence
        self._client = None
        if not settings.anthropic_api_key:
            logger.info("ANTHROPIC_API_KEY not set; Claude reasoning engine disabled.")
            return
        try:
            from anthropic import AsyncAnthropic  # lazy import; optional dep

            self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
            logger.info("Claude reasoning engine ready (model=%s).", self._model)
        except Exception:  # noqa: BLE001 - never fail startup over an optional dep
            logger.exception("Could not initialise anthropic client; disabling.")
            self._client = None

    @property
    def enabled(self) -> bool:
        """True when a client is configured and usable."""
        return self._client is not None

    @property
    def label(self) -> str:
        return "claude"

    async def classify_intent(self, text: str) -> IntentResult:
        """Return the best intent label + confidence for ``text``.

        Always returns an :class:`IntentResult`; on any failure the result is
        ``intent="none", confidence=0.0`` so the caller uses keyword matching.
        """
        if not self.enabled or not text.strip():
            return IntentResult(intent="none", confidence=0.0)

        try:
            response = await self._client.messages.parse(  # type: ignore[union-attr]
                model=self._model,
                max_tokens=256,
                system=_SYSTEM_INSTRUCTION,
                messages=[{"role": "user", "content": text.strip()[:2000]}],
                output_format=IntentResult,
            )
        except Exception:  # noqa: BLE001 - fall back to deterministic routing
            logger.warning("Claude classify_intent failed; falling back.", exc_info=True)
            return IntentResult(intent="none", confidence=0.0)

        parsed = getattr(response, "parsed_output", None)
        if isinstance(parsed, IntentResult):
            return parsed
        logger.warning("Unparseable Claude response: %r", str(parsed)[:200])
        return IntentResult(intent="none", confidence=0.0)

    def is_confident(self, result: IntentResult) -> bool:
        """Whether ``result`` clears the configured confidence threshold."""
        return result.intent != "none" and result.confidence >= self._min_conf


class _NullReasoner:
    """No-op reasoner used when no engine is selected; keyword router decides."""

    enabled = False
    label = "keyword-only"

    async def classify_intent(self, text: str) -> IntentResult:
        return IntentResult(intent="none", confidence=0.0)

    def is_confident(self, result: IntentResult) -> bool:
        return False


def build_reasoner(settings: Settings) -> Reasoner:
    """Pick the reasoning engine per :attr:`Settings.reasoning_provider`.

    ``"auto"`` prefers Claude (if ``ANTHROPIC_API_KEY`` is set), falls back to
    Gemini (if ``GEMINI_API_KEY`` is set), and otherwise disables model-based
    classification — the keyword router always still works.
    """
    provider = settings.reasoning_provider
    if provider == "claude":
        return ClaudeReasoner(settings)
    if provider == "gemini":
        return GeminiReasoner(settings)
    if provider == "none":
        return _NullReasoner()

    # "auto"
    if settings.anthropic_api_key:
        return ClaudeReasoner(settings)
    if settings.gemini_api_key:
        return GeminiReasoner(settings)
    return _NullReasoner()

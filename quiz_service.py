"""Hourly trivia quiz + leaderboard for the Rock in Rio concierge.

State (all JSON blobs in the shared :class:`~services.state.StateStore`):

* ``quiz:cursor``  – index of the next question in :data:`quiz_data.QUESTIONS`.
* ``quiz:active``  – the open round: ``{qid, correct, answered, winner}``.
* ``quiz:scores``  – ``{display_name: points}``.

The service is pure logic: it returns the text/buttons to send and the points
to award; :class:`concierge.Concierge` owns the actual WhatsApp calls.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from quiz_data import LEADERBOARD_SEED, QUESTIONS, resolve_participant, tag
from services.state import StateStore

logger = logging.getLogger(__name__)

_KEY_CURSOR = "quiz:cursor"
_KEY_ACTIVE = "quiz:active"
_KEY_SCORES = "quiz:scores"
_TTL = 7 * 24 * 3600  # keep the tournament state for the whole festival week

_ROUND_CLOSED_MSG = (
    "⏳ Essa rodada já foi encerrada! Próxima pergunta na virada da hora."
)


class QuizService:
    """Question rotation, answer verification and leaderboard state."""

    def __init__(self, store: StateStore) -> None:
        self._store = store

    # ------------------------------------------------------------------ #
    # State helpers                                                       #
    # ------------------------------------------------------------------ #
    async def _get_json(self, key: str, default):
        raw = await self._store.get(key)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except ValueError:  # pragma: no cover - corrupt cache
            logger.warning("Corrupt quiz state at %s; resetting.", key)
            return default

    async def _set_json(self, key: str, value) -> None:
        await self._store.set(key, json.dumps(value), ex=_TTL)

    async def _scores(self) -> dict[str, int]:
        scores = await self._get_json(_KEY_SCORES, {})
        for name in LEADERBOARD_SEED:
            scores.setdefault(name, 0)
        return scores

    # ------------------------------------------------------------------ #
    # Rounds                                                              #
    # ------------------------------------------------------------------ #
    async def start_round(self) -> Optional[tuple[str, list[tuple[str, str]]]]:
        """Open the next question. Returns ``(text, buttons)`` or ``None`` when
        the bank is exhausted."""
        cursor = int(await self._get_json(_KEY_CURSOR, 0))
        if cursor >= len(QUESTIONS):
            logger.info("Quiz bank exhausted (cursor=%d).", cursor)
            return None
        question = QUESTIONS[cursor]

        await self._set_json(
            _KEY_ACTIVE,
            {
                "qid": question.id,
                "correct": question.correct,
                "answered": False,
                "winner": None,
            },
        )
        await self._set_json(_KEY_CURSOR, cursor + 1)

        round_no = cursor + 1
        text = f"⏰ *Rodada {round_no}/{len(QUESTIONS)}*\n\n{question.render()}"
        buttons = [
            (f"A) {question.options['A']}"[:20], "quiz_A"),
            (f"B) {question.options['B']}"[:20], "quiz_B"),
            (f"C) {question.options['C']}"[:20], "quiz_C"),
        ]
        logger.info("Quiz round %d opened (qid=%d).", round_no, question.id)
        return text, buttons

    # ------------------------------------------------------------------ #
    # Answers                                                             #
    # ------------------------------------------------------------------ #
    @staticmethod
    def parse_choice(raw: str | None) -> Optional[str]:
        """Extract an 'A'/'B'/'C' answer from text or a ``quiz_X`` button id."""
        if not raw:
            return None
        token = raw.strip().upper()
        if token in {"A", "B", "C"}:
            return token
        if token in {"QUIZ_A", "QUIZ_B", "QUIZ_C"}:
            return token[-1]
        # allow "letra a", "resposta b", "alternativa c"
        for prefix in ("LETRA ", "RESPOSTA ", "ALTERNATIVA ", "OPCAO ", "OPÇÃO "):
            if token.startswith(prefix) and token[len(prefix):].strip() in {"A", "B", "C"}:
                return token[len(prefix):].strip()
        return None

    async def submit_answer(
        self, sender_jid: str | None, push_name: str | None, raw_choice: str
    ) -> Optional[str]:
        """Process an answer attempt. Returns the reply text, or ``None`` to
        stay silent (no active round / not an answer)."""
        choice = self.parse_choice(raw_choice)
        if choice is None:
            return None

        active = await self._get_json(_KEY_ACTIVE, None)
        if not active:
            return None  # no round running
        if active.get("answered"):
            return _ROUND_CLOSED_MSG

        name = resolve_participant(sender_jid, push_name)

        if choice != active["correct"]:
            return f"❌ Ops, {tag(name)}! Resposta errada. Quem mais arrisca?"

        # Correct — award and close the round.
        active["answered"] = True
        active["winner"] = name
        await self._set_json(_KEY_ACTIVE, active)

        scores = await self._scores()
        scores[name] = scores.get(name, 0) + 1
        await self._set_json(_KEY_SCORES, scores)
        logger.info("Quiz: %s answered qid=%s correctly.", name, active.get("qid"))

        return (
            f"🎉 *RESPOSTA CORRETA!* {tag(name)} acertou a questão!\n\n"
            + await self.leaderboard_text(header="🏆 *Placar Atual:*")
        )

    # ------------------------------------------------------------------ #
    # Leaderboard                                                         #
    # ------------------------------------------------------------------ #
    async def leaderboard_text(self, *, header: str = "🏆 *Placar do Torneio:*") -> str:
        scores = await self._scores()
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        medals = ["🥇", "🥈", "🥉"]
        lines = [header]
        for i, (name, pts) in enumerate(ranked):
            badge = medals[i] if i < len(medals) else f"{i + 1}º"
            unit = "pt" if pts == 1 else "pts"
            lines.append(f"{badge} {name}: {pts} {unit}")
        return "\n".join(lines)

    @staticmethod
    def is_leaderboard_command(text: str | None) -> bool:
        return bool(text) and text.strip().lower() in {
            "placar", "ranking", "pontos", "quiz", "leaderboard",
        }

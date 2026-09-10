"""Unified WhatsApp messaging provider.

Two back ends are supported behind one interface:

* ``meta``      – WhatsApp Cloud API (Facebook Graph API v20.0).
* ``evolution`` – self-hosted Evolution API (WhatsApp Web multi-device).

The provider only needs three outbound capabilities for this concierge:

* :meth:`WhatsAppService.send_text`               – plain text.
* :meth:`WhatsAppService.send_buttons`            – text + up to 3 reply buttons.
* :meth:`WhatsAppService.parse_inbound`           – normalise an inbound webhook
  payload into a small, provider-agnostic :class:`InboundMessage`.

WhatsApp reply buttons are capped at 3 by Meta.  Callers that need more options
(e.g. the four quick check-in hotspots) should send two button messages or fall
back to a list; :meth:`send_buttons` will automatically split into batches of 3.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import httpx

from config import Settings

logger = logging.getLogger(__name__)

InboundKind = Literal["text", "location", "button", "unknown"]


@dataclass(slots=True)
class InboundMessage:
    """Provider-agnostic view of a single inbound WhatsApp message."""

    kind: InboundKind
    sender: str
    chat_id: str
    text: Optional[str] = None
    button_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    push_name: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


class WhatsAppError(RuntimeError):
    """Raised when an outbound WhatsApp send fails."""


class WhatsAppService:
    """Send messages and parse webhooks for the configured provider."""

    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None):
        self._settings = settings
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "WhatsAppService":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:  # pragma: no cover - defensive
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    # ================================================================== #
    # Outbound                                                            #
    # ================================================================== #
    async def send_text(
        self,
        text: str,
        *,
        chat_id: Optional[str] = None,
        mentions: Optional[list[str]] = None,
    ) -> None:
        """Send a plain text message to ``chat_id`` (defaults to the group).

        ``mentions`` is a list of bare phone numbers (or full JIDs); for the
        Evolution provider they become WhatsApp @-mentions (the text must
        already contain ``@<number>`` for the client to render them). Meta text
        messages do not support mentions, so the list is ignored there.
        """
        target = chat_id or self._settings.whatsapp_group_id
        if self._settings.whatsapp_provider == "meta":
            await self._meta_post(
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": target,
                    "type": "text",
                    "text": {"body": text, "preview_url": True},
                }
            )
        else:
            payload: dict[str, Any] = {"number": target, "text": text}
            if mentions:
                payload["mentioned"] = [
                    m if "@" in m else f"{m}@s.whatsapp.net" for m in mentions
                ]
            await self._evolution_post("message/sendText", payload)

    async def send_buttons(
        self,
        text: str,
        buttons: list[tuple[str, str]],
        *,
        chat_id: Optional[str] = None,
        header: Optional[str] = None,
    ) -> None:
        """Send an interactive message with reply buttons.

        Args:
            text: Body text.
            buttons: list of ``(title, button_id)`` tuples. Automatically
                chunked into groups of 3 (the Meta maximum). Follow-up chunks
                are sent with an elided body so the thread stays readable.
            chat_id: Destination; defaults to the configured group.
            header: Optional short header text (Meta only).
        """
        target = chat_id or self._settings.whatsapp_group_id
        if not buttons:
            await self.send_text(text, chat_id=target)
            return

        chunks = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
        for idx, chunk in enumerate(chunks):
            body = text if idx == 0 else "⤷ Mais opções:"
            if self._settings.whatsapp_provider == "meta":
                await self._meta_send_buttons(target, body, chunk, header if idx == 0 else None)
            else:
                await self._evolution_send_buttons(target, body, chunk)

    async def send_list(
        self,
        text: str,
        rows: list[tuple[str, str, Optional[str]]],
        *,
        chat_id: Optional[str] = None,
        header: Optional[str] = None,
        button_label: str = "Escolher",
        section_title: str = "Opções",
    ) -> None:
        """Send a single interactive **list** (menu) message.

        Unlike :meth:`send_buttons` this fits up to 10 options in one message,
        which keeps the WhatsApp thread clean.

        Args:
            text: Body text.
            rows: list of ``(title, row_id, description)`` tuples; ``description``
                may be ``None``. Titles are truncated to 24 chars (Meta limit).
            chat_id: Destination; defaults to the configured group.
            header: Optional short header text.
            button_label: The label of the button that opens the menu.
            section_title: The single section's header.
        """
        target = chat_id or self._settings.whatsapp_group_id
        if not rows:
            await self.send_text(text, chat_id=target)
            return

        if self._settings.whatsapp_provider == "meta":
            section_rows = []
            for title, row_id, desc in rows[:10]:
                row: dict[str, Any] = {"id": row_id, "title": title[:24]}
                if desc:
                    row["description"] = desc[:72]
                section_rows.append(row)
            interactive: dict[str, Any] = {
                "type": "list",
                "body": {"text": text},
                "action": {
                    "button": button_label[:20],
                    "sections": [{"title": section_title[:24], "rows": section_rows}],
                },
            }
            if header:
                interactive["header"] = {"type": "text", "text": header[:60]}
            await self._meta_post(
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": target,
                    "type": "interactive",
                    "interactive": interactive,
                }
            )
        else:
            # Evolution v2.3 rejects empty row descriptions and empty footer.
            payload = {
                "number": target,
                "title": (header or "Rock in Rio Concierge")[:60],
                "description": text,
                "buttonText": button_label,
                "footerText": "Rock in Rio Concierge",
                "sections": [
                    {
                        "title": section_title,
                        "rows": [
                            {
                                "title": title[:24],
                                "description": (desc or title)[:72],
                                "rowId": row_id,
                            }
                            for title, row_id, desc in rows[:10]
                        ],
                    }
                ],
            }
            await self._evolution_post("message/sendList", payload)

    # ---- Meta Cloud API ---------------------------------------------- #
    def _meta_url(self) -> str:
        s = self._settings
        return (
            f"https://graph.facebook.com/{s.meta_graph_version}"
            f"/{s.meta_phone_number_id}/messages"
        )

    async def _meta_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        s = self._settings
        if not (s.meta_access_token and s.meta_phone_number_id):
            raise WhatsAppError("META_ACCESS_TOKEN / META_PHONE_NUMBER_ID not configured.")
        headers = {
            "Authorization": f"Bearer {s.meta_access_token}",
            "Content-Type": "application/json",
        }
        try:
            resp = await self._http().post(self._meta_url(), json=payload, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WhatsAppError(
                f"Meta send failed {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise WhatsAppError(f"Meta send transport error: {exc}") from exc
        return resp.json()

    async def _meta_send_buttons(
        self,
        target: str,
        body: str,
        chunk: list[tuple[str, str]],
        header: Optional[str],
    ) -> None:
        action_buttons = [
            {
                "type": "reply",
                "reply": {"id": btn_id, "title": title[:20]},
            }
            for title, btn_id in chunk
        ]
        interactive: dict[str, Any] = {
            "type": "button",
            "body": {"text": body},
            "action": {"buttons": action_buttons},
        }
        if header:
            interactive["header"] = {"type": "text", "text": header[:60]}
        await self._meta_post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": target,
                "type": "interactive",
                "interactive": interactive,
            }
        )

    # ---- Evolution API --------------------------------------------- #
    async def _evolution_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        s = self._settings
        if not s.evolution_api_key:
            raise WhatsAppError("EVOLUTION_API_KEY not configured.")
        url = f"{s.evolution_base_url.rstrip('/')}/{path}/{s.evolution_instance}"
        headers = {"apikey": s.evolution_api_key, "Content-Type": "application/json"}
        try:
            resp = await self._http().post(url, json=payload, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WhatsAppError(
                f"Evolution send failed {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise WhatsAppError(f"Evolution send transport error: {exc}") from exc
        return resp.json()

    async def _evolution_send_buttons(
        self, target: str, body: str, chunk: list[tuple[str, str]]
    ) -> None:
        # Evolution "buttons" payload (WhatsApp Web templated buttons).
        payload = {
            "number": target,
            "title": "Rock in Rio Concierge",
            "description": body,
            "footer": "",
            "buttons": [
                {"type": "reply", "displayText": title, "id": btn_id}
                for title, btn_id in chunk
            ],
        }
        await self._evolution_post("message/sendButtons", payload)

    # ================================================================== #
    # Inbound parsing                                                     #
    # ================================================================== #
    def parse_inbound(self, payload: dict[str, Any]) -> list[InboundMessage]:
        """Normalise a raw inbound webhook body into :class:`InboundMessage` list."""
        if self._settings.whatsapp_provider == "meta":
            return self._parse_meta(payload)
        return self._parse_evolution(payload)

    def _parse_meta(self, payload: dict[str, Any]) -> list[InboundMessage]:
        out: list[InboundMessage] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                metadata = value.get("metadata", {})
                chat_id = metadata.get("phone_number_id", "")
                contacts = {
                    c.get("wa_id"): (c.get("profile") or {}).get("name")
                    for c in value.get("contacts", [])
                }
                for msg in value.get("messages", []):
                    sender = msg.get("from", "")
                    push_name = contacts.get(sender)
                    mtype = msg.get("type")
                    if mtype == "text":
                        out.append(
                            InboundMessage(
                                kind="text",
                                sender=sender,
                                chat_id=chat_id,
                                text=(msg.get("text") or {}).get("body", ""),
                                raw=msg,
                            )
                        )
                    elif mtype == "location":
                        loc = msg.get("location") or {}
                        out.append(
                            InboundMessage(
                                kind="location",
                                sender=sender,
                                chat_id=chat_id,
                                latitude=_to_float(loc.get("latitude")),
                                longitude=_to_float(loc.get("longitude")),
                                raw=msg,
                            )
                        )
                    elif mtype == "interactive":
                        inter = msg.get("interactive") or {}
                        reply = (
                            inter.get("button_reply")
                            or inter.get("list_reply")
                            or {}
                        )
                        out.append(
                            InboundMessage(
                                kind="button",
                                sender=sender,
                                chat_id=chat_id,
                                button_id=reply.get("id"),
                                text=reply.get("title"),
                                raw=msg,
                            )
                        )
                    elif mtype == "button":  # template quick-reply
                        btn = msg.get("button") or {}
                        out.append(
                            InboundMessage(
                                kind="button",
                                sender=sender,
                                chat_id=chat_id,
                                button_id=btn.get("payload") or btn.get("text"),
                                text=btn.get("text"),
                                raw=msg,
                            )
                        )
                    else:
                        out.append(
                            InboundMessage(
                                kind="unknown", sender=sender, chat_id=chat_id, raw=msg
                            )
                        )
        return out

    def _parse_evolution(self, payload: dict[str, Any]) -> list[InboundMessage]:
        """Normalise an Evolution API webhook body.

        Evolution posts ``{"event": "...", "instance": "...", "data": ...}``.
        Only ``messages.upsert`` events carry user input; ``data`` may be a
        single message object, a list of them, or ``{"messages": [...]}``.
        Non-message events (connection.update, qrcode.updated, ...) and our own
        outbound echoes (``key.fromMe``) yield no :class:`InboundMessage`.
        """
        event = str(payload.get("event", "")).lower().replace("_", ".")
        if event and event not in {"messages.upsert", "messages.update", "send.message"}:
            return []

        data = payload.get("data", payload)
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            records = data["messages"]
        elif isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            records = [data]
        else:
            return []

        out: list[InboundMessage] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            parsed = self._evolution_one(record)
            if parsed is not None:
                out.append(parsed)
        return out

    @staticmethod
    def _evolution_one(record: dict[str, Any]) -> Optional[InboundMessage]:
        """Convert one Evolution message record to an :class:`InboundMessage`."""
        key = record.get("key") or {}
        if key.get("fromMe"):
            return None  # our own outbound, echoed back
        chat_id = key.get("remoteJid", "") or ""
        sender = key.get("participant") or key.get("participantAlt") or chat_id
        push_name = record.get("pushName")
        message = record.get("message") or {}
        if not isinstance(message, dict):
            return None

        def _mk(**kw: Any) -> InboundMessage:
            return InboundMessage(sender=sender, chat_id=chat_id, push_name=push_name, **kw)

        if "locationMessage" in message:
            loc = message["locationMessage"] or {}
            return _mk(
                kind="location",
                latitude=_to_float(loc.get("degreesLatitude")),
                longitude=_to_float(loc.get("degreesLongitude")),
                raw=record,
            )
        if "buttonsResponseMessage" in message:
            br = message["buttonsResponseMessage"] or {}
            return _mk(
                kind="button", button_id=br.get("selectedButtonId"),
                text=br.get("selectedDisplayText"), raw=record,
            )
        if "templateButtonReplyMessage" in message:
            tr = message["templateButtonReplyMessage"] or {}
            return _mk(
                kind="button", button_id=tr.get("selectedId"),
                text=tr.get("selectedDisplayText"), raw=record,
            )
        if "listResponseMessage" in message:
            lr = message["listResponseMessage"] or {}
            row = lr.get("singleSelectReply") or {}
            return _mk(
                kind="button", button_id=row.get("selectedRowId"),
                text=lr.get("title"), raw=record,
            )
        text = (
            message.get("conversation")
            or (message.get("extendedTextMessage") or {}).get("text")
        )
        if text:
            return _mk(kind="text", text=text, raw=record)
        return None


def _to_float(value: Any) -> Optional[float]:
    """Best-effort float conversion returning ``None`` on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

"""Google Calendar access for the festival concierge.

Supports two authentication strategies, selected by ``settings.google_auth_mode``:

* ``service_account`` – a GCP service account JSON key, optionally with
  domain-wide delegation impersonating ``guilherme.dantas.sp@gmail.com``.
* ``oauth`` – an installed-app OAuth2 client plus a cached user token for
  ``guilherme.dantas.sp@gmail.com`` (obtained once, offline).

The client can:

* search a time window for in-scope festival shows
  (:meth:`GoogleCalendarService.list_shows`),
* fetch the next upcoming show (:meth:`GoogleCalendarService.next_show`),
* patch a show's start/end by a delta (:meth:`GoogleCalendarService.shift_event`).
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import FESTIVAL_POIS, Settings
from models import FestivalShow

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Alias table: map free-text stage mentions in an event title/location to the
# canonical POI key. Longest / most specific patterns first.
_STAGE_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"palco\s*mundo", re.I), "Palco Mundo"),
    (re.compile(r"palco\s*sunset|sunset\s*stage", re.I), "Palco Sunset"),
    (re.compile(r"espa[çc]o\s*favela|favela", re.I), "Espaço Favela"),
    (re.compile(r"new\s*dance\s*order|\bndo\b|supernova", re.I), "New Dance Order"),
    (re.compile(r"gourmet\s*square", re.I), "Gourmet Square"),
]


class CalendarError(RuntimeError):
    """Raised when the Calendar API cannot satisfy a request."""


class GoogleCalendarService:
    """Thin, typed wrapper around the Google Calendar v3 API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._tz = ZoneInfo(settings.festival_timezone)
        self._lock = threading.Lock()
        self._service: Optional[Any] = None

    # ------------------------------------------------------------------ #
    # Authentication                                                      #
    # ------------------------------------------------------------------ #
    def _build_credentials(self) -> Any:
        """Create Google credentials according to the configured auth mode."""
        s = self._settings
        if s.google_auth_mode == "service_account":
            try:
                creds = service_account.Credentials.from_service_account_file(
                    s.google_service_account_file, scopes=_SCOPES
                )
            except (OSError, ValueError) as exc:  # pragma: no cover - config error
                raise CalendarError(
                    f"Cannot load service account file "
                    f"{s.google_service_account_file!r}: {exc}"
                ) from exc
            if s.google_delegated_subject:
                creds = creds.with_subject(s.google_delegated_subject)
            return creds

        # oauth mode
        try:
            creds = UserCredentials.from_authorized_user_file(
                s.google_oauth_token_file, _SCOPES
            )
        except (OSError, ValueError) as exc:  # pragma: no cover - config error
            raise CalendarError(
                f"Cannot load OAuth token file {s.google_oauth_token_file!r}. "
                f"Run scripts/google_oauth_bootstrap.py once for "
                f"{s.owner_email}: {exc}"
            ) from exc
        if not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            try:
                with open(s.google_oauth_token_file, "w", encoding="utf-8") as fh:
                    fh.write(creds.to_json())
            except OSError:  # pragma: no cover - non fatal
                logger.warning("Could not persist refreshed OAuth token.")
        return creds

    def _client(self) -> Any:
        """Return a cached, thread-safe Calendar API service object."""
        with self._lock:
            if self._service is None:
                creds = self._build_credentials()
                self._service = build(
                    "calendar", "v3", credentials=creds, cache_discovery=False
                )
                logger.info(
                    "Google Calendar client ready (mode=%s, calendar=%s)",
                    self._settings.google_auth_mode,
                    self._settings.target_calendar_id,
                )
            return self._service

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolve_stage(*texts: Optional[str]) -> Optional[str]:
        """Return the canonical stage name found in any of ``texts``."""
        blob = " ".join(t for t in texts if t)
        for pattern, canonical in _STAGE_ALIASES:
            if pattern.search(blob):
                return canonical
        # direct match against POI keys
        for key in FESTIVAL_POIS:
            if key.lower() in blob.lower():
                return key
        return None

    @staticmethod
    def _parse_dt(node: dict[str, Any], fallback_tz: ZoneInfo) -> datetime:
        """Parse a Calendar ``start``/``end`` node into an aware datetime."""
        raw = node.get("dateTime") or node.get("date")
        if raw is None:
            raise CalendarError(f"Event time node has neither dateTime nor date: {node}")
        if "T" not in raw:  # all-day event -> midnight local
            dt = datetime.fromisoformat(raw).replace(tzinfo=fallback_tz)
            return dt
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=fallback_tz)
        return dt

    def _event_to_show(self, event: dict[str, Any]) -> Optional[FestivalShow]:
        """Convert a raw Calendar event into a :class:`FestivalShow` or ``None``."""
        summary = event.get("summary", "") or ""
        location = event.get("location", "") or ""
        description = event.get("description", "") or ""

        keyword = self._settings.festival_event_keyword.lower()
        haystack = f"{summary}\n{location}\n{description}".lower()
        if keyword and keyword not in haystack:
            # Not tagged as a festival show; skip unless a stage is explicit.
            if self._resolve_stage(summary, location, description) is None:
                return None

        stage = self._resolve_stage(summary, location, description)
        if stage is None:
            logger.debug("Event %s has no resolvable stage; skipping.", event.get("id"))
            return None

        # Artist: prefer text before a separator, else the whole summary.
        artist = re.split(r"\s*[-–—@|:]\s*", summary, maxsplit=1)[0].strip() or summary
        artist = re.sub(
            r"(?i)\brock in rio\b[\s:–-]*", "", artist
        ).strip() or summary.strip()

        try:
            start = self._parse_dt(event["start"], self._tz).astimezone(self._tz)
            end = self._parse_dt(event["end"], self._tz).astimezone(self._tz)
        except (KeyError, CalendarError) as exc:
            logger.warning("Skipping event %s: %s", event.get("id"), exc)
            return None

        return FestivalShow(
            event_id=event["id"],
            artist=artist,
            stage=stage,
            start=start,
            end=end,
            calendar_id=self._settings.target_calendar_id,
        )

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #
    def list_shows(
        self,
        window_start: Optional[datetime] = None,
        window_end: Optional[datetime] = None,
    ) -> list[FestivalShow]:
        """Return in-scope festival shows within ``[window_start, window_end]``.

        Defaults to the full configured festival window.
        """
        s = self._settings
        if window_start is None:
            window_start = datetime.fromisoformat(s.festival_start_date).replace(
                tzinfo=self._tz
            )
        if window_end is None:
            window_end = datetime.fromisoformat(s.festival_end_date).replace(
                tzinfo=self._tz
            ) + timedelta(days=1)

        try:
            resp = (
                self._client()
                .events()
                .list(
                    calendarId=s.target_calendar_id,
                    timeMin=window_start.astimezone(timezone.utc).isoformat(),
                    timeMax=window_end.astimezone(timezone.utc).isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=250,
                )
                .execute()
            )
        except HttpError as exc:  # pragma: no cover - network error
            raise CalendarError(f"Calendar list failed: {exc}") from exc

        shows: list[FestivalShow] = []
        for event in resp.get("items", []):
            show = self._event_to_show(event)
            if show is not None:
                shows.append(show)
        shows.sort(key=lambda sh: sh.start)
        logger.debug("Resolved %d festival shows in window.", len(shows))
        return shows

    def next_show(self, *, now: Optional[datetime] = None) -> Optional[FestivalShow]:
        """Return the earliest show whose start is still in the future."""
        now = now or datetime.now(self._tz)
        for show in self.list_shows():
            if show.start > now:
                return show
        return None

    def get_show(self, event_id: str) -> Optional[FestivalShow]:
        """Fetch a single event by id and convert it to a show."""
        try:
            event = (
                self._client()
                .events()
                .get(calendarId=self._settings.target_calendar_id, eventId=event_id)
                .execute()
            )
        except HttpError as exc:  # pragma: no cover - network error
            raise CalendarError(f"Calendar get {event_id} failed: {exc}") from exc
        return self._event_to_show(event)

    def shift_event(
        self, event_id: str, delta: timedelta
    ) -> FestivalShow:
        """Advance an event's start and end by ``delta`` via ``events.patch``.

        Returns the updated :class:`FestivalShow`.
        """
        service = self._client()
        cal_id = self._settings.target_calendar_id
        try:
            event = service.events().get(calendarId=cal_id, eventId=event_id).execute()
        except HttpError as exc:  # pragma: no cover - network error
            raise CalendarError(f"Calendar get {event_id} failed: {exc}") from exc

        new_start = self._parse_dt(event["start"], self._tz) + delta
        new_end = self._parse_dt(event["end"], self._tz) + delta
        tz_name = event["start"].get("timeZone", self._settings.festival_timezone)

        body = {
            "start": {"dateTime": new_start.isoformat(), "timeZone": tz_name},
            "end": {"dateTime": new_end.isoformat(), "timeZone": tz_name},
        }
        try:
            updated = (
                service.events()
                .patch(calendarId=cal_id, eventId=event_id, body=body)
                .execute()
            )
        except HttpError as exc:  # pragma: no cover - network error
            raise CalendarError(f"Calendar patch {event_id} failed: {exc}") from exc

        show = self._event_to_show(updated)
        if show is None:  # pragma: no cover - should not happen after a valid patch
            raise CalendarError(
                f"Patched event {event_id} could not be re-parsed as a show."
            )
        logger.info(
            "Shifted event %s by %s -> new start %s",
            event_id, delta, show.start.isoformat(),
        )
        return show

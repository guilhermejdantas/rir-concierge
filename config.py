"""Centralized configuration for the Rock in Rio 2026 WhatsApp Festival Concierge.

All settings are read from environment variables (or a local ``.env`` file) via
``pydantic_settings``.  Every value that ties the deployment to the festival
owner's Google account defaults to ``guilherme.dantas.sp@gmail.com``.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Coordinate(tuple):
    """A simple ``(lat, lng)`` pair with helpful ``__repr__``."""

    __slots__ = ()

    def __new__(cls, lat: float, lng: float) -> "Coordinate":
        return super().__new__(cls, (float(lat), float(lng)))

    @property
    def lat(self) -> float:
        return self[0]

    @property
    def lng(self) -> float:
        return self[1]

    def __repr__(self) -> str:  # pragma: no cover - cosmetic only
        return f"Coordinate(lat={self.lat}, lng={self.lng})"


# --------------------------------------------------------------------------- #
# Static festival geography (Parque Olímpico / Cidade do Rock).               #
# These are hard-coded because they never change during the event window.     #
# --------------------------------------------------------------------------- #
FESTIVAL_POIS: dict[str, Coordinate] = {
    "Palco Mundo": Coordinate(-22.975420, -43.395110),
    "Palco Sunset": Coordinate(-22.973810, -43.397020),
    "Espaço Favela": Coordinate(-22.974910, -43.393540),
    "New Dance Order": Coordinate(-22.977230, -43.396250),
    "Gourmet Square": Coordinate(-22.976100, -43.394800),
    "Entrada / BRT": Coordinate(-22.979500, -43.392000),
}

HOTEL_BASE: Coordinate = Coordinate(-22.980148, -43.409869)

# Map the interactive button ids exchanged with WhatsApp to a POI name.
BUTTON_TO_POI: dict[str, str] = {
    "checkin_palco_sunset": "Palco Sunset",
    "checkin_gourmet_square": "Gourmet Square",
    "checkin_espaco_favela": "Espaço Favela",
    "checkin_entrada_brt": "Entrada / BRT",
}

# Human readable label -> button id (used when composing outbound messages).
QUICK_CHECKIN_BUTTONS: list[tuple[str, str]] = [
    ("📍 Palco Sunset", "checkin_palco_sunset"),
    ("🍔 Gourmet Square", "checkin_gourmet_square"),
    ("🍺 Espaço Favela", "checkin_espaco_favela"),
    ("🚪 Entrada / BRT", "checkin_entrada_brt"),
]


class Settings(BaseSettings):
    """Runtime configuration.

    Attributes are grouped by concern: general, Google, Maps, WhatsApp, Redis.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- General ---------------------------------------------------------- #
    app_env: Literal["dev", "prod"] = "prod"
    log_level: str = "INFO"
    public_base_url: str = Field(
        default="https://concierge.example.com",
        description="Externally reachable base URL, used to build /checkin links.",
    )
    festival_timezone: str = "America/Sao_Paulo"
    festival_start_date: str = "2026-09-11"
    festival_end_date: str = "2026-09-13"
    alert_lead_minutes: int = Field(
        default=30, ge=1, le=180,
        description="How many minutes before a show the proactive alert fires.",
    )
    reschedule_delta_minutes: int = Field(default=15, ge=1, le=120)
    scheduler_poll_seconds: int = Field(default=60, ge=15, le=600)
    walking_speed_kmh: float = Field(default=4.5, gt=0)

    # -- Owner identity -------------------------------------------------- #
    owner_email: str = "guilherme.dantas.sp@gmail.com"
    target_calendar_id: str = "guilherme.dantas.sp@gmail.com"

    # -- Google Calendar ----------------------------------------------- #
    google_auth_mode: Literal["service_account", "oauth"] = "service_account"
    google_service_account_file: str = "/secrets/service_account.json"
    google_oauth_client_file: str = "/secrets/oauth_client.json"
    google_oauth_token_file: str = "/secrets/oauth_token.json"
    # Optional domain-wide-delegation subject; defaults to the owner.
    google_delegated_subject: str = "guilherme.dantas.sp@gmail.com"
    festival_event_keyword: str = Field(
        default="rock in rio",
        description="Case-insensitive substring that marks a calendar event "
        "as an in-scope festival show.",
    )

    # -- Google Maps -------------------------------------------------- #
    google_maps_api_key: str = ""
    maps_distance_matrix_url: str = (
        "https://maps.googleapis.com/maps/api/distancematrix/json"
    )
    maps_timeout_seconds: float = Field(default=6.0, gt=0)

    # -- WhatsApp ---------------------------------------------------- #
    whatsapp_provider: Literal["meta", "evolution"] = "meta"
    whatsapp_group_id: str = Field(
        default="",
        description="Destination chat/group id (Meta: phone number id target "
        "JID; Evolution: group JID like 1203630xxxx@g.us).",
    )
    whatsapp_verify_token: str = "change-me-verify-token"

    # Meta Cloud API (Graph API v20.0)
    meta_graph_version: str = "v20.0"
    meta_phone_number_id: str = ""
    meta_access_token: str = ""

    # Evolution API
    evolution_base_url: str = "http://evolution-api:8080"
    evolution_instance: str = "rir-concierge"
    evolution_api_key: str = ""

    # -- Gemini reasoning engine (Google AI Studio) --------------- #
    # Optional. Empty key => the deterministic keyword router is used as-is.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_min_confidence: float = Field(
        default=0.75, ge=0.0, le=1.0,
        description="Minimum classifier confidence before an intent is acted on; "
        "below this the keyword router decides.",
    )

    # -- Redis ----------------------------------------------------- #
    # Empty string => in-process state store (fine for a single instance).
    redis_url: str = "redis://redis:6379/0"
    state_ttl_seconds: int = Field(default=6 * 3600, ge=60)

    @field_validator("festival_start_date", "festival_end_date")
    @classmethod
    def _validate_iso_date(cls, value: str) -> str:
        from datetime import date

        date.fromisoformat(value)  # raises ValueError on bad input
        return value

    # -- Convenience -------------------------------------------------- #
    @property
    def checkin_url_template(self) -> str:
        """Return an ``str.format``-ready template for the web check-in page."""
        return f"{self.public_base_url.rstrip('/')}/checkin?u={{user_id}}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()

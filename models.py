"""Shared Pydantic data models used across the concierge services and API."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class FestivalShow(BaseModel):
    """A single scheduled festival show, derived from a Google Calendar event."""

    event_id: str = Field(..., description="Google Calendar event id.")
    artist: str = Field(..., description="Performing artist / act name.")
    stage: str = Field(..., description="Canonical stage name (a FESTIVAL_POIS key).")
    start: datetime = Field(..., description="Timezone-aware show start.")
    end: datetime = Field(..., description="Timezone-aware show end.")
    calendar_id: str

    @property
    def local_time_str(self) -> str:
        """Return the ``HH:MM`` start time in the event's own timezone."""
        return self.start.strftime("%H:%M")


class Coordinates(BaseModel):
    """A latitude/longitude pair received from a user."""

    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    accuracy_m: Optional[float] = Field(
        default=None, ge=0.0, description="Reported GPS accuracy in meters."
    )
    source: str = Field(
        default="unknown",
        description="One of: location_pin | button | web_checkin | manual.",
    )


class WalkEstimate(BaseModel):
    """Result of a walk-time computation to a target stage."""

    stage: str
    distance_m: int = Field(..., ge=0)
    duration_min: int = Field(..., ge=0)
    method: str = Field(..., description="'distance_matrix' or 'haversine_fallback'.")


class CheckinPayload(BaseModel):
    """Body accepted by ``POST /api/checkin`` from the browser fallback page."""

    user_id: str = Field(..., min_length=1, max_length=128)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    accuracy_m: Optional[float] = Field(default=None, ge=0.0)

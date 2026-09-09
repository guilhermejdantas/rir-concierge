"""Walking distance / duration estimation.

Primary path: Google Maps Distance Matrix API (``mode=walking``) using the API
key associated with ``guilherme.dantas.sp@gmail.com``.

Fallback path: a Haversine great-circle distance divided by a configurable
average walking pace (default 4.5 km/h).  The fallback is used whenever the
Maps API is unreachable, rate-limited, missing a key, or returns a non-OK
status for the requested element.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

import httpx

from config import FESTIVAL_POIS, Settings
from models import Coordinates, WalkEstimate

logger = logging.getLogger(__name__)

_EARTH_RADIUS_M = 6_371_000.0
# Detour factor: straight-line distance under-estimates real walking paths.
_PATH_DETOUR_FACTOR = 1.25


class MapsService:
    """Compute walking estimates with a resilient fallback."""

    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None):
        self._settings = settings
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "MapsService":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._settings.maps_timeout_seconds)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # Fallback maths                                                      #
    # ------------------------------------------------------------------ #
    @staticmethod
    def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """Great-circle distance in meters between two WGS84 points."""
        p1, p2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lng2 - lng1)
        a = (
            math.sin(d_phi / 2) ** 2
            + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
        )
        return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))

    def _fallback_estimate(
        self, origin: Coordinates, stage: str, dest_lat: float, dest_lng: float
    ) -> WalkEstimate:
        raw = self.haversine_m(
            origin.latitude, origin.longitude, dest_lat, dest_lng
        )
        distance_m = int(round(raw * _PATH_DETOUR_FACTOR))
        speed_m_per_min = (self._settings.walking_speed_kmh * 1000.0) / 60.0
        duration_min = max(1, int(math.ceil(distance_m / speed_m_per_min)))
        logger.info(
            "Haversine fallback: %s -> %s = %dm (~%d min)",
            (origin.latitude, origin.longitude), stage, distance_m, duration_min,
        )
        return WalkEstimate(
            stage=stage,
            distance_m=distance_m,
            duration_min=duration_min,
            method="haversine_fallback",
        )

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #
    async def walk_estimate(self, origin: Coordinates, stage: str) -> WalkEstimate:
        """Return a :class:`WalkEstimate` from ``origin`` to the named ``stage``.

        Raises:
            KeyError: if ``stage`` is not a known festival POI.
        """
        if stage not in FESTIVAL_POIS:
            raise KeyError(f"Unknown stage {stage!r}; expected one of {list(FESTIVAL_POIS)}")
        dest = FESTIVAL_POIS[stage]

        if not self._settings.google_maps_api_key:
            logger.warning("No GOOGLE_MAPS_API_KEY set; using Haversine fallback.")
            return self._fallback_estimate(origin, stage, dest.lat, dest.lng)

        if self._client is None:  # pragma: no cover - defensive
            self._client = httpx.AsyncClient(timeout=self._settings.maps_timeout_seconds)

        params = {
            "origins": f"{origin.latitude},{origin.longitude}",
            "destinations": f"{dest.lat},{dest.lng}",
            "mode": "walking",
            "units": "metric",
            "key": self._settings.google_maps_api_key,
        }
        try:
            resp = await self._client.get(
                self._settings.maps_distance_matrix_url, params=params
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Distance Matrix request failed (%s); falling back.", exc)
            return self._fallback_estimate(origin, stage, dest.lat, dest.lng)

        if data.get("status") != "OK":
            logger.warning(
                "Distance Matrix top-level status=%s (%s); falling back.",
                data.get("status"), data.get("error_message"),
            )
            return self._fallback_estimate(origin, stage, dest.lat, dest.lng)

        try:
            element = data["rows"][0]["elements"][0]
        except (KeyError, IndexError):
            logger.warning("Distance Matrix response malformed; falling back.")
            return self._fallback_estimate(origin, stage, dest.lat, dest.lng)

        if element.get("status") != "OK":
            logger.warning(
                "Distance Matrix element status=%s; falling back.", element.get("status")
            )
            return self._fallback_estimate(origin, stage, dest.lat, dest.lng)

        distance_m = int(element["distance"]["value"])
        duration_min = max(1, int(round(element["duration"]["value"] / 60.0)))
        logger.info(
            "Distance Matrix: %s -> %s = %dm (~%d min)",
            (origin.latitude, origin.longitude), stage, distance_m, duration_min,
        )
        return WalkEstimate(
            stage=stage,
            distance_m=distance_m,
            duration_min=duration_min,
            method="distance_matrix",
        )

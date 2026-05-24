#!/usr/bin/env python3
"""
api_client.py — PZTrack REST API client for the NFC reader module.

Wraps the three endpoints used by this module:
  POST /login                               → acquire JWT
  GET  /competitors/<competitor_id>         → current state + craft name
  POST /competitors/<competitor_id>/checkinstate → toggle on/off water

The existing server (phasezero-tracker-api-server) is never modified.
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

# Refresh token this many seconds before it would expire (10-day token).
_TOKEN_REFRESH_MARGIN = 300  # 5 minutes


class APIError(Exception):
    """Raised when the PZTrack API returns an unexpected response."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class PZTrackClient:
    """Thin HTTP client for the PZTrack API."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def login(self) -> None:
        """Obtain a JWT access token from POST /login."""
        resp = self._session.post(
            f"{self.base_url}/login",
            json={"username": self.username, "password": self.password},
            timeout=10,
        )
        if resp.status_code != 200:
            raise APIError(
                f"Login failed (HTTP {resp.status_code}): {resp.text}",
                resp.status_code,
            )
        data = resp.json()
        self._token = data["access_token"]
        # global_config.json sets JWT_ACCESS_TOKEN_EXPIRES = 864000 s (10 days)
        self._token_expiry = time.monotonic() + 864000 - _TOKEN_REFRESH_MARGIN
        self._session.headers.update({"Authorization": f"Bearer {self._token}"})
        logger.debug("JWT access token acquired.")

    def refresh_if_needed(self) -> None:
        """Re-login if the token is absent or close to expiry."""
        if self._token is None or time.monotonic() >= self._token_expiry:
            logger.info("Refreshing JWT token.")
            self.login()

    # ------------------------------------------------------------------
    # Competitor queries
    # ------------------------------------------------------------------

    def get_competitor(self, competitor_id: str) -> dict:
        """
        GET /competitors/<competitor_id>

        Returns the first competitor dict from the response, e.g.:
          {
            "competitorId": "186",
            "trackerId": 186,
            "craftName": "My Vessel",
            "teamName": "Team A",
            "checkinState": "checked_in" | "checked_out" | None,
            "checkinStateChangeTimestamp": "2024-01-01T00:00:00",
            "startGroup": 1,
            "contactPhoneNumber": ""
          }
        """
        self.refresh_if_needed()
        resp = self._session.get(
            f"{self.base_url}/competitors/{competitor_id}",
            timeout=10,
        )
        if resp.status_code == 404:
            raise APIError(f"Competitor '{competitor_id}' not found.", 404)
        if resp.status_code != 200:
            raise APIError(
                f"GET /competitors/{competitor_id} failed "
                f"(HTTP {resp.status_code}): {resp.text}",
                resp.status_code,
            )
        competitors = resp.json().get("competitors", [])
        if not competitors:
            raise APIError(
                f"No data returned for competitor '{competitor_id}'.", 200
            )
        return competitors[0]

    def set_checkin_state(
        self, competitor_id: str, new_state: str
    ) -> dict | None:
        """
        POST /competitors/<competitor_id>/checkinstate

        new_state must be "checked_in" or "checked_out".
        Returns the updated competitor dict on HTTP 200, or None on 204.
        Raises APIError on failure.
        """
        if new_state not in ("checked_in", "checked_out"):
            raise ValueError(
                f"new_state must be 'checked_in' or 'checked_out', got '{new_state}'"
            )

        self.refresh_if_needed()
        resp = self._session.post(
            f"{self.base_url}/competitors/{competitor_id}/checkinstate",
            json={"checkin_state": new_state},
            timeout=10,
        )
        if resp.status_code == 400:
            raise APIError(
                f"Bad request setting state for '{competitor_id}': {resp.text}", 400
            )
        if resp.status_code == 404:
            raise APIError(f"Competitor '{competitor_id}' not found.", 404)
        if resp.status_code not in (200, 204):
            raise APIError(
                f"POST checkinstate failed (HTTP {resp.status_code}): {resp.text}",
                resp.status_code,
            )
        if resp.status_code == 200:
            return resp.json().get("competitor")
        return None  # 204 No Content — success with no body

    def get_all_competitors(self) -> list[dict]:
        """
        GET /competitors/all  — useful for diagnostics / tag-registration helper.
        Returns a list of competitor dicts.
        """
        self.refresh_if_needed()
        resp = self._session.get(
            f"{self.base_url}/competitors/all",
            timeout=10,
        )
        if resp.status_code != 200:
            raise APIError(
                f"GET /competitors/all failed (HTTP {resp.status_code}): {resp.text}",
                resp.status_code,
            )
        return resp.json().get("competitors", [])

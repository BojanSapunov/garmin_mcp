"""Minimal client for the Garmin Connect Personal API.

Auth flow: POST the personal API key to the token endpoint, receive a raw
access token valid for 24h (no refresh tokens), then send it as a Bearer
token on read-only GET requests to personal.connectapi.garmin.com.
"""

import os
import time

import httpx
from dotenv import load_dotenv

BASE_URL = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
TOKEN_TTL_S = 23 * 3600  # renew well before the 24h expiry


class GarminClient:
    def __init__(self, api_key: str | None = None):
        load_dotenv()
        self.api_key = api_key or os.environ.get("GARMIN_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("GARMIN_API_KEY is not set (put it in .env)")
        self._http = httpx.Client(base_url=BASE_URL, timeout=60)
        self._token: str | None = None
        self._token_at = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() - self._token_at < TOKEN_TTL_S:
            return self._token
        r = self._http.post(TOKEN_PATH, headers={"Authorization": f"ApiKey {self.api_key}"})
        r.raise_for_status()
        self._token = r.text.strip()
        self._token_at = time.time()
        return self._token

    def get(self, path: str, params: dict | None = None) -> httpx.Response:
        """GET with retry on rate limiting / transient errors and one token renewal on 401."""
        renewed = False
        for attempt in range(6):
            r = self._http.get(path, params=params, headers={"Authorization": f"Bearer {self._get_token()}"})
            if r.status_code == 401 and not renewed:
                self._token, renewed = None, True
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(float(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

    def get_json(self, path: str, params: dict | None = None):
        return self.get(path, params).json()

    def search_activities(self, start: int = 0, limit: int = 99, **filters) -> list[dict]:
        params = {"start": start, "limit": limit, **filters}
        return self.get_json("/activitylist-service/activities/search/activities", params)

    def activity(self, activity_id: int) -> dict:
        return self.get_json(f"/activity-service/activity/{activity_id}")

    def activity_file(self, activity_id: int) -> bytes:
        """Original activity file (usually FIT), returned as a zip archive."""
        return self.get(f"/download-service/files/activity/{activity_id}").content

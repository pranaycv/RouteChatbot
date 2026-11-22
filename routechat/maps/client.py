"""Provider HTTP requests, per-run cache, and offline fixture replay."""

import hashlib
import os
import time
from collections import Counter

import httpx
import orjson

from routechat.config import ROOT, get_settings


class MapError(Exception):
    """A provider error that can be represented on a route candidate."""


class MapClient:
    def __init__(self, http, deadline=None):
        self.http = http
        self.deadline = deadline
        self.settings = get_settings().maps
        self.calls = Counter()
        self.cache = {}

    def request(self, api, method, url, params, body=None):
        key = hashlib.sha256(orjson.dumps(
            [method, url, params, body], option=orjson.OPT_SORT_KEYS,
        )).hexdigest()
        if key in self.cache:
            return self.cache[key]

        mode = os.environ.get("FIXTURES")
        fixture = ROOT / "fixtures" / f"{key}.json"
        if mode == "replay":
            try:
                payload = orjson.loads(fixture.read_bytes())
            except (OSError, ValueError):
                raise MapError("fixture_missing") from None
        else:
            token_name, token_param = (
                ("GRAPHHOPPER_KEY", "key") if api == "graphhopper"
                else ("MAPBOX_TOKEN", "access_token")
            )
            token = os.environ.get(token_name)
            if not token:
                raise MapError("provider_not_configured")
            payload = self._fetch(api, method, url, {**params, token_param: token}, body)
            if mode == "record":
                fixture.parent.mkdir(parents=True, exist_ok=True)
                fixture.write_bytes(orjson.dumps(payload))

        self.cache[key] = payload
        return payload

    def _fetch(self, api, method, url, params, body):
        for attempt in range(self.settings.attempts):
            remaining = float("inf") if self.deadline is None else self.deadline - time.monotonic()
            if remaining <= 0:
                raise MapError("tool_timeout")
            self.calls[api] += 1
            try:
                response = self.http.request(
                    method, url, params=params, json=body,
                    timeout=min(remaining, self.settings.http_timeout_s),
                )
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException:
                reason = "tool_timeout"
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if api == "graphhopper" and status == 400:
                    try:
                        error = exc.response.json()
                    except ValueError:
                        error = None
                    if isinstance(error, dict) and error.get("message") == "Free packages cannot use flexible mode":
                        raise MapError("flexible_mode_unavailable") from None
                if status != 429 and status < 500:
                    raise MapError("no_route") from None
                reason = "no_route"
            except (httpx.RequestError, ValueError):
                raise MapError("no_route") from None

            if attempt + 1 < self.settings.attempts:
                if self.deadline is not None and time.monotonic() + self.settings.wait_s >= self.deadline:
                    raise MapError("tool_timeout")
                time.sleep(self.settings.wait_s)
        raise MapError(reason)

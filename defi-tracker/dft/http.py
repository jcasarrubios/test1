"""HTTP client: one session, per-host politeness delay, bounded retries.

Every data source here is a free public API. The point of this module is to be
a good citizen on them — a fixed User-Agent, a small delay between calls to the
same host, and backoff on 429/5xx — so a scheduled job never gets a keyless
endpoint rate-limited out from under it.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)


class HttpError(RuntimeError):
    pass


class Http:
    def __init__(self, user_agent: str = "dft/0.1", timeout: float = 20.0,
                 max_retries: int = 3, min_interval: dict[str, float] | None = None):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval or {}
        self._last_call: dict[str, float] = {}

    @classmethod
    def from_config(cls, cfg) -> "Http":
        return cls(
            user_agent=cfg.get("http.user_agent", "dft/0.1"),
            timeout=float(cfg.get("http.timeout_seconds", 20)),
            max_retries=int(cfg.get("http.max_retries", 3)),
            min_interval={k: float(v) for k, v in (cfg.get("http.min_interval") or {}).items()},
        )

    def _throttle(self, host: str) -> None:
        gap = self.min_interval.get(host)
        if not gap:
            return
        last = self._last_call.get(host)
        if last is not None:
            wait = gap - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_call[host] = time.monotonic()

    def get_json(self, url: str, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> Any:
        host = urlsplit(url).netloc
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle(host)
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise HttpError(f"{resp.status_code} from {host}")
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, HttpError, ValueError) as exc:
                last_exc = exc
                if attempt == self.max_retries:
                    break
                backoff = (2 ** attempt) + random.random()
                log.warning("GET %s failed (%s); retrying in %.1fs", url, exc, backoff)
                time.sleep(backoff)
        raise HttpError(f"GET {url} failed after {self.max_retries + 1} attempts: {last_exc}")

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        host = urlsplit(url).netloc
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle(host)
            try:
                resp = self.session.get(url, headers=headers, timeout=self.timeout)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise HttpError(f"{resp.status_code} from {host}")
                resp.raise_for_status()
                return resp.text
            except (requests.RequestException, HttpError) as exc:
                last_exc = exc
                if attempt == self.max_retries:
                    break
                time.sleep((2 ** attempt) + random.random())
        raise HttpError(f"GET {url} failed after {self.max_retries + 1} attempts: {last_exc}")

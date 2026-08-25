"""Shared HTTP utilities: a session with retries and a token-bucket limiter."""

from __future__ import annotations

import time
import logging

import requests
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type,
)

import config

log = logging.getLogger("acquire.http")

_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT, "Accept": "application/json"})


class RateLimiter:
    """Minimum interval between calls (simple, single-threaded)."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()


@retry(
    reraise=True,
    stop=stop_after_attempt(config.MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(
        (requests.ConnectionError, requests.Timeout, requests.HTTPError)
    ),
)
def get_json(url: str, params: dict | None = None, auth=None) -> dict:
    """GET a URL and return parsed JSON, retrying on transient failures.

    429 (rate limited) and 5xx are retried; 4xx (other than 429) raise so we
    don't hammer a bad request.
    """
    resp = _session.get(
        url, params=params, auth=auth, timeout=config.REQUEST_TIMEOUT
    )
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", "5"))
        log.warning("429 rate limited; sleeping %ss", retry_after)
        time.sleep(retry_after)
        resp.raise_for_status()
    if 500 <= resp.status_code < 600:
        resp.raise_for_status()
    if resp.status_code >= 400:
        # Non-retryable client error: log body and raise a plain error.
        log.error("HTTP %s for %s: %s", resp.status_code, url, resp.text[:300])
        resp.raise_for_status()
    return resp.json()

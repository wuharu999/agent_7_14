from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx


log = logging.getLogger("worker.egress_region")
_MAX_REGION_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True)
class EgressRegionDecision:
    country_code: str | None
    cerebras_allowed: bool
    reason: str


def lookup_country_code(url: str, timeout: int) -> str:
    """Resolve only the outbound ISO country code; never retain or log the IP."""
    response = httpx.get(
        url,
        timeout=float(timeout),
        follow_redirects=False,
        headers={"Accept": "text/plain, application/json", "User-Agent": "agent1-region-check/1"},
    )
    response.raise_for_status()
    if len(response.content) > _MAX_REGION_RESPONSE_BYTES:
        raise ValueError("region response exceeded size limit")
    raw: object = ""
    content_type = response.headers.get("content-type", "").casefold()
    if "json" in content_type or response.text.lstrip().startswith("{"):
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("region response was not an object")
        raw = payload.get("country_code") or payload.get("countryCode") or payload.get("country")
    else:
        fields = dict(
            line.split("=", 1)
            for line in response.text.splitlines()
            if "=" in line
        )
        raw = fields.get("loc", "")
    code = str(raw or "").strip().upper()
    if len(code) != 2 or not code.isalpha() or code in {"XX", "ZZ"}:
        raise ValueError("region response did not include an ISO country code")
    return code


class EgressRegionGate:
    """Thread-safe, fail-closed Cerebras region gate with a short-lived cache."""

    def __init__(
        self,
        *,
        check_url: str,
        timeout: int,
        cache_seconds: int,
        blocked_countries: frozenset[str],
        lookup: Callable[[str, int], str] = lookup_country_code,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.check_url = check_url
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self.blocked_countries = frozenset(code.upper() for code in blocked_countries)
        self.lookup = lookup
        self.clock = clock
        self._lock = threading.Lock()
        self._cached: EgressRegionDecision | None = None
        self._expires_at = 0.0

    def decision(self) -> EgressRegionDecision:
        now = self.clock()
        with self._lock:
            if self._cached is not None and now < self._expires_at:
                return self._cached
            try:
                country_code = self.lookup(self.check_url, self.timeout).upper()
            except Exception as exc:
                decision = EgressRegionDecision(None, False, "country_check_failed")
                log.warning(
                    "Cerebras region check failed closed; using DeepSeek",
                    exc_info=exc,
                )
            else:
                blocked = country_code in self.blocked_countries
                decision = EgressRegionDecision(
                    country_code,
                    not blocked,
                    "blocked_country" if blocked else "allowed_country",
                )
                (log.info if blocked else log.debug)(
                    "Cerebras region decision country=%s allowed=%s",
                    country_code,
                    not blocked,
                )
            self._cached = decision
            # Recheck permitted routes before every new Cerebras request so a
            # VPN disconnect cannot leave an old allowed country cached. Cache
            # only blocked/unknown decisions to avoid repeated doomed checks.
            self._expires_at = (
                self.clock() + self.cache_seconds
                if not decision.cerebras_allowed
                else self.clock()
            )
            return decision

    def reset(self) -> None:
        with self._lock:
            self._cached = None
            self._expires_at = 0.0

from __future__ import annotations

import httpx
import pytest

from worker import egress_region
from worker.egress_region import EgressRegionGate


@pytest.mark.parametrize(
    ("content_type", "body", "expected"),
    [
        ("text/plain", "fl=123\nip=203.0.113.1\nloc=US\ntls=TLSv1.3\n", "US"),
        ("application/json", '{"ip":"203.0.113.1","country_code":"JP"}', "JP"),
    ],
)
def test_country_lookup_accepts_trace_and_json_without_returning_ip(
    monkeypatch, content_type: str, body: str, expected: str
) -> None:
    response = httpx.Response(
        200,
        headers={"content-type": content_type},
        text=body,
        request=httpx.Request("GET", "https://region.invalid"),
    )
    monkeypatch.setattr(egress_region.httpx, "get", lambda *_args, **_kwargs: response)

    assert egress_region.lookup_country_code("https://region.invalid", 2) == expected


@pytest.mark.parametrize("country_code", ["CN", "TW", "HK", "SG"])
def test_region_gate_blocks_configured_countries(country_code: str) -> None:
    gate = EgressRegionGate(
        check_url="https://region.invalid/json",
        timeout=2,
        cache_seconds=300,
        blocked_countries=frozenset({"CN", "TW", "HK", "SG"}),
        lookup=lambda _url, _timeout: country_code,
    )

    decision = gate.decision()

    assert decision.country_code == country_code
    assert decision.cerebras_allowed is False
    assert decision.reason == "blocked_country"


def test_region_gate_rechecks_allowed_country_before_each_cerebras_attempt() -> None:
    calls: list[str] = []

    def lookup(_url: str, _timeout: int) -> str:
        calls.append("lookup")
        return "US"

    gate = EgressRegionGate(
        check_url="https://region.invalid/json",
        timeout=2,
        cache_seconds=300,
        blocked_countries=frozenset({"CN", "TW", "HK", "SG"}),
        lookup=lookup,
    )

    assert gate.decision().cerebras_allowed is True
    assert gate.decision().cerebras_allowed is True
    assert calls == ["lookup", "lookup"]


def test_region_gate_caches_blocked_country() -> None:
    calls: list[str] = []

    def lookup(_url: str, _timeout: int) -> str:
        calls.append("lookup")
        return "CN"

    gate = EgressRegionGate(
        check_url="https://region.invalid/json",
        timeout=2,
        cache_seconds=300,
        blocked_countries=frozenset({"CN", "TW", "HK", "SG"}),
        lookup=lookup,
    )

    assert gate.decision().cerebras_allowed is False
    assert gate.decision().cerebras_allowed is False
    assert calls == ["lookup"]


def test_region_gate_fails_closed_when_country_cannot_be_verified() -> None:
    def fail(_url: str, _timeout: int) -> str:
        raise TimeoutError("lookup timed out")

    gate = EgressRegionGate(
        check_url="https://region.invalid/json",
        timeout=2,
        cache_seconds=300,
        blocked_countries=frozenset({"CN", "TW", "HK", "SG"}),
        lookup=fail,
    )

    decision = gate.decision()

    assert decision.country_code is None
    assert decision.cerebras_allowed is False
    assert decision.reason == "country_check_failed"

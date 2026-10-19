"""Exchange rates: provider parsing (Decimal, no floats), labels and status, BDT coverage, outages,
fallback disclosure, cross-rate disclosure, edge-case amounts, coverage report, key protection and
the client-IP logic used by rate limits. No test calls the network."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import SecretStr

from app import fx
from app.config import get_settings
from tests.conftest import seed

NOW = datetime.now(UTC)


def _erapi_payload(as_of: datetime, **rates: str) -> bytes:
    body = {
        "result": "success",
        "provider": "https://www.exchangerate-api.com",
        "time_last_update_unix": int(as_of.timestamp()),
        "time_next_update_unix": int((as_of + timedelta(days=1)).timestamp()),
        "base_code": "USD",
        "rates": {"USD": 1, **{k: float(v) if "." in v else int(v) for k, v in rates.items()}},
    }
    # Pad with extra currencies so the payload looks like the real ~160-currency response.
    for code in ("EUR", "GBP", "JPY", "CNY", "INR", "AUD", "CAD", "CHF", "SGD", "HKD", "AED", "SAR", "PKR", "LKR", "NPR", "THB", "MYR", "IDR", "PHP", "VND", "KRW", "NGN"):
        body["rates"].setdefault(code, 1.2345)
    return json.dumps(body).encode()


def test_erapi_parser_keeps_exact_decimals_and_rejects_bad_payloads():
    f = fx.parse_erapi(_erapi_payload(NOW, BDT="122.1234567", EUR="0.8612"))
    assert f.base == "USD" and f.rates["BDT"] == "122.1234567" and f.rates["USD"] == "1"
    assert f.provider_as_of is not None and f.next_update_at is not None
    with pytest.raises(ValueError):
        fx.parse_erapi(json.dumps({"result": "error", "error-type": "unsupported-code"}).encode())
    with pytest.raises(ValueError):
        fx.parse_erapi(json.dumps({"result": "success", "base_code": "USD", "time_last_update_unix": 1, "rates": {"USD": 1}}).encode())
    # Non-ISO / non-fiat codes and nonsense values are dropped, never stored.
    raw = json.loads(_erapi_payload(NOW, BDT="120"))
    raw["rates"].update({"XAU": 0.0004, "XDR": 0.7, "ABC": 5, "EUR": -1})
    g = fx.parse_erapi(json.dumps(raw).encode())
    assert "XAU" not in g.rates and "XDR" not in g.rates and "ABC" not in g.rates and "EUR" not in g.rates


def _unique(monkeypatch, real: str) -> str:
    """Rate snapshots are append-only and shared by the test database, so each test uses its own provider name."""
    import uuid

    name = f"{real}_{uuid.uuid4().hex[:8]}"
    monkeypatch.setitem(fx.PROVIDERS, name, dict(fx.PROVIDERS[real]))
    return name


@pytest.fixture
def provider(monkeypatch):
    s = get_settings()
    primary, fallback = _unique(monkeypatch, "erapi"), _unique(monkeypatch, "ecb")
    monkeypatch.setattr(s, "fx_provider", primary)
    monkeypatch.setattr(s, "fx_fallback_provider", fallback)
    calls = {"erapi": 0, "ecb": 0, "names": {"erapi": primary, "ecb": fallback}}
    state = {"erapi": lambda: fx.parse_erapi(_erapi_payload(NOW - timedelta(hours=3), BDT="122.50", EUR="0.86")), "ecb": None}

    def make(name):
        def run():
            calls[name] += 1
            f = state[name]
            if f is None:
                raise OSError("down")
            return f()

        return run

    monkeypatch.setattr(fx, "fetchers", {primary: make("erapi"), fallback: make("ecb")})
    return calls, state


def test_usd_to_bdt_with_labels_attribution_and_caching(as_owner, provider):
    calls, _ = provider
    seed("usd_primary")
    api = as_owner()

    def conv(a="100", f="USD", t="BDT"):
        return api.post("/api/fx/convert", {"amount": a, "from_currency": f, "to_currency": t, "mode": "latest"}).json()

    first = conv()
    assert first["result_display"] == "BDT 12,250.00" and first["rate"] == "122.5"
    src = first["source"]
    assert src["status"] == "current" and src["provider"] == calls["names"]["erapi"] and src["attribution"]["text"] == "Rates By Exchange Rate API"
    assert src["provider_as_of"] and src["fetched_at"] and "not a real-time" in src["detail"] and "fees" in src["fees_note"]
    assert first["derived"] is None  # USD is the provider's base
    again = conv("1", "BDT", "EUR")
    assert again["source"]["status"] == "cached" and calls["erapi"] == 1
    assert again["derived"] and "same timestamp" in again["derived"]
    assert conv("5", "BDT", "BDT")["result_display"] == "BDT 5.00"
    assert conv("0")["result_minor"] == 0
    for bad in ("-5", "abc", "1.234", "1e5", "99999999999999"):
        r = api.post("/api/fx/convert", {"amount": bad, "from_currency": "USD", "to_currency": "BDT", "mode": "latest"})
        assert r.status_code == 422, bad
    # Not quoted: say so before/instead of converting.
    nok = conv("5", "USD", "NOK")
    assert nok["result_minor"] is None and nok["missing"] == ["NOK"]
    cov = api.get("/api/fx/coverage").json()
    assert cov["provider"] == calls["names"]["erapi"] and "BDT" in cov["quoted"] and "NOK" in cov["unavailable"]
    assert cov["selectable_count"] >= 150 and cov["key_required"] is False and cov["refresh_frequency"] == "Once a day"


def test_outage_never_invents_rates_and_fallback_is_named(as_owner, provider, monkeypatch):
    calls, state = provider
    seed("usd_primary")
    api = as_owner()
    state["erapi"] = None  # primary down from the start, never produced a snapshot
    r = api.post("/api/fx/convert", {"amount": "100", "from_currency": "USD", "to_currency": "BDT", "mode": "latest"}).json()
    assert r["source"]["status"] == "unavailable" and r["result_minor"] is None and "Could not refresh" in r["source"]["detail"]
    # Fallback provider answers EUR pairs but is named as such; BDT stays unavailable (ECB does not cover it).
    state["ecb"] = lambda: fx.Fetched("EUR", date.today(), {"EUR": "1", "USD": "1.10", "GBP": "0.85", "JPY": "160", "CNY": "7.8", "CHF": "0.95"})
    r = api.post("/api/fx/convert", {"amount": "100", "from_currency": "USD", "to_currency": "EUR", "mode": "latest"}).json()
    assert r["source"]["provider"] == calls["names"]["ecb"] and "fallback provider" in r["source"]["detail"] and r["result_display"] == "EUR 90.91"
    b = api.post("/api/fx/convert", {"amount": "100", "from_currency": "USD", "to_currency": "BDT", "mode": "latest"}).json()
    assert b["result_minor"] is None and b["missing"] == ["BDT"]


def test_failed_refresh_keeps_last_known_rates_and_respects_provider_schedule(as_owner, provider, monkeypatch):
    calls, state = provider
    seed("usd_primary")
    api = as_owner()

    def conv():
        return api.post("/api/fx/convert", {"amount": "100", "from_currency": "USD", "to_currency": "BDT", "mode": "latest"}).json()

    assert conv()["source"]["status"] == "current"
    monkeypatch.setattr(get_settings(), "fx_refresh_hours", 0)
    state["erapi"] = None
    # The provider's next publication is still in the future, so no refetch is attempted (quota respected).
    again = conv()
    assert again["source"]["status"] == "cached" and calls["erapi"] == 1 and again["result_display"] == "BDT 12,250.00"


def test_stale_publication_is_labelled_with_age(monkeypatch):
    from app.db import get_sessionmaker

    s = get_settings()
    name = _unique(monkeypatch, "erapi")
    monkeypatch.setattr(s, "fx_provider", name)
    monkeypatch.setattr(s, "fx_fallback_provider", "off")
    monkeypatch.setattr(s, "fx_refresh_hours", 0)
    old = NOW - timedelta(days=6)

    def stale_fetch():
        f = fx.parse_erapi(_erapi_payload(old, BDT="110"))
        f.next_update_at = NOW - timedelta(days=5)  # overdue: the provider stopped publishing
        return f

    monkeypatch.setattr(fx, "fetchers", {name: stale_fetch})
    with get_sessionmaker()() as db:
        rs = fx.latest_rates(db)
    assert rs.status == "stale" and "days old" in rs.detail and "BDT" in rs.rates


def test_keyed_provider_requires_key_and_never_exposes_it(as_owner, monkeypatch):
    from app.config import Settings

    base = {"database_url": "postgresql://u:p@h/db", "sandbox_event_secret": "x" * 40}
    with pytest.raises(Exception):
        Settings(**base, fx_provider="exchangerate_api")
    ok = Settings(**base, fx_provider="exchangerate_api", fx_api_key="secret-key-123")
    assert "secret-key-123" not in repr(ok) and "secret-key-123" not in str(ok.model_dump())
    seed("usd_primary")
    api = as_owner()
    monkeypatch.setattr(get_settings(), "fx_provider", "exchangerate_api")
    monkeypatch.setattr(get_settings(), "fx_fallback_provider", "off")
    monkeypatch.setattr(get_settings(), "fx_api_key", SecretStr("secret-key-123"))
    monkeypatch.setattr(fx, "fetchers", {"exchangerate_api": lambda: (_ for _ in ()).throw(OSError("down")), "ecb": lambda: (_ for _ in ()).throw(OSError("down"))})
    body = api.post("/api/fx/convert", {"amount": "1", "from_currency": "USD", "to_currency": "BDT", "mode": "latest"}).text
    cov = api.get("/api/fx/coverage").text
    assert "secret-key-123" not in body and "secret-key-123" not in cov


def test_client_ip_uses_secret_header_or_rightmost_hop(monkeypatch):
    from starlette.requests import Request

    from app.auth.ratelimit import client_ip

    def req(headers: dict[str, str]) -> Request:
        return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()], "client": ("10.0.0.9", 1)})

    s = get_settings()
    monkeypatch.setattr(s, "trusted_proxy_hops", 1)
    monkeypatch.setattr(s, "proxy_secret", SecretStr("p" * 32))
    # A caller cannot forge the right-most entry (appended by our proxy).
    assert client_ip(req({"x-forwarded-for": "1.2.3.4, 203.0.113.9"})) == "203.0.113.9"
    # The frontend's header is trusted only with the shared secret.
    assert client_ip(req({"x-forwarded-for": "5.5.5.5", "x-lifeline-client-ip": "9.9.9.9", "x-lifeline-proxy-secret": "wrong"})) == "5.5.5.5"
    assert client_ip(req({"x-forwarded-for": "5.5.5.5", "x-lifeline-client-ip": "9.9.9.9", "x-lifeline-proxy-secret": "p" * 32})) == "9.9.9.9"
    assert client_ip(req({"x-lifeline-client-ip": "<script>", "x-lifeline-proxy-secret": "p" * 32})) == "10.0.0.9"
    monkeypatch.setattr(s, "trusted_proxy_hops", 0)
    assert client_ip(req({"x-forwarded-for": "1.2.3.4"})) == "10.0.0.9"

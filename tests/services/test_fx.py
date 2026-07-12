from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from rob.utils import fx
from rob.utils.fx import (
    UnsupportedCurrencyError,
    convert_cents_to_usd,
    current_rates,
    parse_ecb_rates,
    refresh_rates,
)
from rob.utils.money import dollars_to_cents

_ECB_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
 <Cube>
  <Cube time="2026-06-23">
   <Cube currency="USD" rate="1.2000"/>
   <Cube currency="GBP" rate="0.8000"/>
   <Cube currency="JPY" rate="120.00"/>
  </Cube>
 </Cube>
</gesmes:Envelope>"""


@pytest.fixture(autouse=True)
def _reset_live_rates():
    fx._live_rates = None
    fx._live_rates_fetched_at = None
    yield
    fx._live_rates = None
    fx._live_rates_fetched_at = None


def test_parse_ecb_rates_converts_eur_base_to_usd_per_unit():
    rates = parse_ecb_rates(_ECB_SAMPLE)
    assert rates["USD"] == Decimal("1")
    # 1 EUR = 1.20 USD (the feed's USD-per-EUR rate).
    assert rates["EUR"] == Decimal("1.2000")
    # 1 GBP = (USD per EUR) / (GBP per EUR) = 1.20 / 0.80 = 1.5 USD.
    assert rates["GBP"] == Decimal("1.2000") / Decimal("0.8000")
    # 1 JPY = 1.20 / 120 USD.
    assert rates["JPY"] == Decimal("1.2000") / Decimal("120.00")


def test_parse_ecb_rates_requires_usd():
    with pytest.raises(ValueError):
        parse_ecb_rates('<Envelope><Cube currency="GBP" rate="0.8"/></Envelope>')


def test_convert_uses_static_fallback_before_any_live_fetch():
    assert convert_cents_to_usd(100, "USD") == 100
    # Bundled snapshot has EUR at 1.0900 USD per EUR.
    assert convert_cents_to_usd(1000, "EUR") == 1090


def test_convert_unsupported_currency_raises():
    with pytest.raises(UnsupportedCurrencyError):
        convert_cents_to_usd(100, "ZZZ")


def test_convert_negative_amount_raises():
    with pytest.raises(ValueError):
        convert_cents_to_usd(-1, "USD")


def test_live_rates_override_static_and_static_still_covers_gaps(monkeypatch):
    async def fake_fetch(**_kwargs):
        return parse_ecb_rates(_ECB_SAMPLE)

    monkeypatch.setattr(fx, "fetch_ecb_rates", fake_fetch)
    assert asyncio.run(refresh_rates()) is True

    # GBP now comes from the live feed: 1.5 USD per GBP -> 1000 -> 1500.
    assert convert_cents_to_usd(1000, "GBP") == 1500
    # A currency only present in the bundled snapshot still resolves.
    assert "MXN" in current_rates()


def test_refresh_failure_keeps_working_off_static(monkeypatch):
    async def boom(**_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(fx, "fetch_ecb_rates", boom)
    assert asyncio.run(refresh_rates()) is False
    assert convert_cents_to_usd(100, "USD") == 100


def test_dollars_to_cents_rounds_half_up():
    # Half-up (not banker's): 2.675 -> 268 (the classic float/round bug yields 267).
    assert dollars_to_cents("2.675") == 268
    assert dollars_to_cents(10.005) == 1001
    assert dollars_to_cents(19.99) == 1999
    assert dollars_to_cents(0) == 0

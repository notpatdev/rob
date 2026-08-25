from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from xml.etree import ElementTree

import aiohttp

log = logging.getLogger(__name__)

# Offline fallback for the ECB daily reference rates, as USD per 1 unit of
# foreign currency. Only used before the first live fetch, for currencies the
# live feed doesn't list, or when every refresh fails. Snapshot date: 2026-05-31.
STATIC_USD_PER_UNIT: dict[str, Decimal] = {
    "USD": Decimal("1"),
    "EUR": Decimal("1.0900"),
    "GBP": Decimal("1.2800"),
    "JPY": Decimal("0.0064"),
    "CHF": Decimal("1.1200"),
    "CAD": Decimal("0.7300"),
    "AUD": Decimal("0.6600"),
    "NZD": Decimal("0.6100"),
    "MXN": Decimal("0.0540"),
    "SEK": Decimal("0.0950"),
    "NOK": Decimal("0.0900"),
    "DKK": Decimal("0.1460"),
    "PLN": Decimal("0.2550"),
    "CZK": Decimal("0.0440"),
    "HUF": Decimal("0.00280"),
    "RON": Decimal("0.2190"),
    "BGN": Decimal("0.5570"),
    "ISK": Decimal("0.00730"),
    "TRY": Decimal("0.0250"),
    "ILS": Decimal("0.2700"),
    "ZAR": Decimal("0.0550"),
    "BRL": Decimal("0.1800"),
    "INR": Decimal("0.0120"),
    "SGD": Decimal("0.7400"),
    "HKD": Decimal("0.1280"),
    "KRW": Decimal("0.00073"),
    "CNY": Decimal("0.1380"),
    "PHP": Decimal("0.0175"),
    "MYR": Decimal("0.2150"),
    "THB": Decimal("0.0275"),
}

# ECB publishes one keyless, public daily snapshot of EUR reference rates.
ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

# Live rates older than this are stale; the refresher runs well inside this window.
_RATES_TTL = timedelta(hours=72)
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024

_live_rates: dict[str, Decimal] | None = None
_live_rates_fetched_at: datetime | None = None


class UnsupportedCurrencyError(ValueError):
    """Raised when no USD conversion rate exists for a currency code."""


def _live_is_fresh(now: datetime | None = None) -> bool:
    if _live_rates is None or _live_rates_fetched_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - _live_rates_fetched_at) <= _RATES_TTL


def current_rates() -> dict[str, Decimal]:
    """Return USD-per-unit rates: the bundled snapshot with fresh live rates overlaid."""
    rates = dict(STATIC_USD_PER_UNIT)
    if _live_rates is not None and _live_is_fresh():
        rates.update(_live_rates)
    return rates


def parse_ecb_rates(xml_text: str) -> dict[str, Decimal]:
    """Parse the ECB daily XML into USD-per-unit rates.

    The feed lists units-per-EUR; usd_per_unit(X) = usd_per_eur / units_of_X_per_eur.
    """
    root = ElementTree.fromstring(xml_text)
    eur_per: dict[str, Decimal] = {}
    for element in root.iter():
        currency = element.get("currency")
        rate = element.get("rate")
        if not currency or not rate:
            continue
        try:
            value = Decimal(rate)
        except (InvalidOperation, ValueError):
            continue
        if value > 0:
            eur_per[currency.strip().upper()] = value

    usd_per_eur = eur_per.get("USD")
    if usd_per_eur is None or usd_per_eur <= 0:
        raise ValueError("ECB feed did not contain a usable USD rate.")

    rates: dict[str, Decimal] = {"USD": Decimal("1"), "EUR": usd_per_eur}
    for code, eur_rate in eur_per.items():
        rates[code] = usd_per_eur / eur_rate
    return rates


async def fetch_ecb_rates(
    *,
    session: aiohttp.ClientSession | None = None,
    url: str = ECB_DAILY_URL,
    timeout_seconds: float = 10.0,
) -> dict[str, Decimal]:
    """Fetch and parse the live ECB daily rates. Raises on any failure."""
    own_session = session is None
    session = session or aiohttp.ClientSession()
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with session.get(url, timeout=timeout) as response:
            response.raise_for_status()
            raw = await response.content.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("ECB feed response exceeded the size limit.")
        return parse_ecb_rates(raw.decode("utf-8"))
    finally:
        if own_session:
            await session.close()


async def refresh_rates(
    *,
    session: aiohttp.ClientSession | None = None,
    url: str = ECB_DAILY_URL,
) -> bool:
    """Refresh the cached live rates. Returns True on success.

    Failures are logged and return False; the previous rates stay in place.
    """
    global _live_rates, _live_rates_fetched_at
    try:
        rates = await fetch_ecb_rates(session=session, url=url)
    except Exception:
        log.warning("FX rate refresh from ECB failed; keeping existing rates.", exc_info=True)
        return False
    _live_rates = rates
    _live_rates_fetched_at = datetime.now(timezone.utc)
    log.info("Refreshed FX rates from ECB (%d currencies).", len(rates))
    return True


async def run_rate_refresher(*, interval_seconds: float = 12 * 60 * 60) -> None:
    """Refresh rates immediately, then on a fixed interval, until cancelled."""
    while True:
        await refresh_rates()
        await asyncio.sleep(interval_seconds)


def convert_cents_to_usd(amount_cents: int, currency: str | None) -> int:
    if amount_cents < 0:
        raise ValueError("amount_cents must be non-negative")

    code = (currency or "USD").strip().upper() or "USD"
    rate = current_rates().get(code)
    if rate is None:
        raise UnsupportedCurrencyError(f"Unsupported currency for USD conversion: {code}")

    return int((Decimal(amount_cents) * rate).to_integral_value(rounding=ROUND_HALF_UP))

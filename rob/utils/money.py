from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

CURRENCY_NAMES = {
    "USD": "United States Dollar",
    "EUR": "Euro",
    "GBP": "British Pound",
    "AUD": "Australian Dollar",
    "CAD": "Canadian Dollar",
    "NZD": "New Zealand Dollar",
    "JPY": "Japanese Yen",
    "CHF": "Swiss Franc",
    "MXN": "Mexican Peso",
    "SEK": "Swedish Krona",
    "NOK": "Norwegian Krone",
    "DKK": "Danish Krone",
    "PLN": "Polish Zloty",
    "CZK": "Czech Koruna",
    "HUF": "Hungarian Forint",
    "RON": "Romanian Leu",
    "BGN": "Bulgarian Lev",
    "ISK": "Icelandic Krona",
    "TRY": "Turkish Lira",
    "ILS": "Israeli New Shekel",
    "ZAR": "South African Rand",
    "BRL": "Brazilian Real",
    "INR": "Indian Rupee",
    "SGD": "Singapore Dollar",
    "HKD": "Hong Kong Dollar",
    "KRW": "South Korean Won",
    "CNY": "Chinese Yuan",
    "PHP": "Philippine Peso",
    "MYR": "Malaysian Ringgit",
    "THB": "Thai Baht",
}

def dollars_to_cents(amount: float | int | str) -> int:
    # ROUND_HALF_UP to match the FX path (rob/utils/fx.py); plain round() does
    # banker's rounding on a binary float (2.675 -> 267).
    return int((Decimal(str(amount)) * 100).to_integral_value(rounding=ROUND_HALF_UP))

def format_money_from_cents(amount_cents: int, currency: str = "USD") -> str:
    normalized_currency = (currency or "USD").upper()
    symbol = "$" if normalized_currency == "USD" else normalized_currency + " "
    return f"{symbol}{amount_cents / 100:.2f}"

def format_money_with_currency_name(amount_cents: int, currency: str = "USD") -> str:
    money = format_money_from_cents(amount_cents, currency)
    normalized_currency = (currency or "USD").upper()
    return f"{money} ({CURRENCY_NAMES.get(normalized_currency, normalized_currency)})"

"""Money-formatting helper used by throne_tracker."""

from __future__ import annotations


def format_money(amount: float | None) -> str:
    if amount is None:
        return "Unknown"
    return f"${amount:,.2f}"

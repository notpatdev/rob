from __future__ import annotations

import re
from datetime import datetime, timezone

from rob.reports.sends_pdf import (
    PdfCurrencyTotal,
    PdfSendRow,
    SendsPdfReport,
    _dt,
    _money,
    _truncate,
    generate_sends_pdf,
)


def _row(i: int) -> PdfSendRow:
    return PdfSendRow(
        sent_at=datetime(2026, 7, 10, 12, i % 60, tzinfo=timezone.utc),
        amount_cents=2500,
        currency="USD",
        domme_display_name=f"Miss {i % 3}",
        item_name=f"Item {i}",
        sub_name="someone",
    )


def _report(rows) -> SendsPdfReport:
    return SendsPdfReport(
        display_name="someone",
        generated_at=datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc),
        last_updated=(rows[0].sent_at if rows else None),
        total_count=len(rows),
        totals=[PdfCurrencyTotal("USD", 2500 * len(rows), len(rows))] if rows else [],
        all_sends=list(rows),
    )


def _page_count(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))


def test_generates_a_valid_pdf():
    pdf = generate_sends_pdf(_report([_row(i) for i in range(3)]))
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000


def test_empty_report_still_valid():
    pdf = generate_sends_pdf(
        SendsPdfReport("nobody", datetime(2026, 8, 1, tzinfo=timezone.utc), None, 0)
    )
    assert pdf[:4] == b"%PDF"


def test_many_sends_paginate_to_multiple_pages():
    single = generate_sends_pdf(_report([_row(i) for i in range(3)]))
    many = generate_sends_pdf(_report([_row(i) for i in range(60)]))
    assert _page_count(single) == 1
    assert _page_count(many) >= 2


def test_money_formatting():
    assert _money(12500, "USD") == "$125.00"
    assert _money(5000, "EUR") == "€50.00"
    assert _money(999, "GBP") == "£9.99"
    # Unknown currency falls back to a trailing code.
    assert _money(100000, "XYZ") == "1,000.00 XYZ"


def test_datetime_formatting_handles_none():
    assert _dt(None) == "—"
    assert "2026" in _dt(datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc))


def test_truncate():
    assert _truncate("short", 10) == "short"
    assert _truncate("a very long value here", 8).endswith("…")
    # Trailing whitespace is trimmed before the ellipsis, so it's <= the limit.
    assert len(_truncate("a very long value here", 8)) <= 8

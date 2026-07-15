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
    build_recipient_report,
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


def _recipient_dict(**overrides) -> dict:
    base = {
        "sent_at": datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        "amount_cents": 2500,
        "currency": "USD",
        "item_name": "Coffee",
        "sub_name": "gifter_name",
        "domme_display_name": "Miss X",
    }
    base.update(overrides)
    return base


def test_build_recipient_report_groups_totals_and_orders_by_size():
    newest = datetime(2026, 7, 12, tzinfo=timezone.utc)
    rows = [
        _recipient_dict(sent_at=newest, amount_cents=1000, currency="USD"),
        _recipient_dict(
            sent_at=datetime(2026, 7, 11, tzinfo=timezone.utc),
            amount_cents=5000,
            currency="EUR",
        ),
        _recipient_dict(
            sent_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
            amount_cents=500,
            currency="USD",
        ),
    ]

    report = build_recipient_report(
        display_name="gifter_name",
        generated_at=datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc),
        rows=rows,
    )

    assert report.display_name == "gifter_name"
    assert report.total_count == 3
    # last_updated is the newest row's timestamp (rows arrive newest-first).
    assert report.last_updated == newest
    # Totals grouped per currency, biggest amount first.
    assert [(t.currency, t.amount_cents, t.count) for t in report.totals] == [
        ("EUR", 5000, 1),
        ("USD", 1500, 2),
    ]
    assert len(report.all_sends) == 3
    assert all(isinstance(s, PdfSendRow) for s in report.all_sends)


def test_build_recipient_report_handles_empty_rows():
    report = build_recipient_report(
        display_name="nobody",
        generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        rows=[],
    )
    assert report.total_count == 0
    assert report.last_updated is None
    assert report.totals == []
    assert report.all_sends == []
    # An empty recipient report still renders a valid PDF.
    assert generate_sends_pdf(report)[:4] == b"%PDF"


def test_build_recipient_report_tolerates_missing_optional_fields():
    report = build_recipient_report(
        display_name="someone",
        generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        rows=[{"sent_at": None, "amount_cents": 100, "currency": "gbp"}],
    )
    row = report.all_sends[0]
    assert row.domme_display_name is None
    assert row.item_name is None
    assert row.sub_name is None
    # Produces a valid PDF despite the sparse row.
    assert generate_sends_pdf(report)[:4] == b"%PDF"

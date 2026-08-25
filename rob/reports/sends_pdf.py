"""Server-side keepsake PDF of a user's sends, DM'd by Rob on 1 August.

Rebuilds the website's warm-paper "Your sends" report in Python (reportlab), so
the bot can generate and attach the file itself. It won't be pixel-identical to
the browser version (no Instrument Serif / Inter embedding) — it uses the
built-in Times/Helvetica — but matches the layout and palette: a cover page with
summary + totals + the start of the sends table, and continuation pages that
repeat the table header.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Warm paper palette (matches robthebot.com).
_BG = colors.HexColor("#f7f4ec")
_FG = colors.HexColor("#2b2622")
_MUTED = colors.HexColor("#8a8079")
_BORDER = colors.HexColor("#e4dfd3")
_CARD = colors.HexColor("#fbf8f1")

_SERIF = "Times-Roman"
_SERIF_ITALIC = "Times-Italic"
_SANS = "Helvetica"

_CURRENCY_SYMBOLS = {"USD": "$", "AUD": "$", "CAD": "$", "EUR": "€", "GBP": "£"}


@dataclass(frozen=True)
class PdfSendRow:
    sent_at: datetime
    amount_cents: int
    currency: str
    domme_display_name: str | None
    item_name: str | None
    sub_name: str | None


@dataclass(frozen=True)
class PdfCurrencyTotal:
    currency: str
    amount_cents: int
    count: int


@dataclass(frozen=True)
class SendsPdfReport:
    display_name: str
    generated_at: datetime
    last_updated: datetime | None
    total_count: int
    totals: list[PdfCurrencyTotal] = field(default_factory=list)
    all_sends: list[PdfSendRow] = field(default_factory=list)


def build_recipient_report(
    *,
    display_name: str,
    generated_at: datetime,
    rows: list[dict],
) -> SendsPdfReport:
    """Assemble a :class:`SendsPdfReport` from recipient rows (dicts with
    ``sent_at``, ``amount_cents``, ``currency``, ``item_name``, ``sub_name``,
    ``domme_display_name``), newest first."""
    send_rows = [
        PdfSendRow(
            sent_at=row["sent_at"],
            amount_cents=int(row["amount_cents"]),
            currency=str(row["currency"]),
            domme_display_name=row.get("domme_display_name"),
            item_name=row.get("item_name"),
            sub_name=row.get("sub_name"),
        )
        for row in rows
    ]

    buckets: dict[str, list[int]] = {}
    for send in send_rows:
        bucket = buckets.setdefault(send.currency, [0, 0])
        bucket[0] += send.amount_cents
        bucket[1] += 1
    totals = sorted(
        (
            PdfCurrencyTotal(currency=cur, amount_cents=amt, count=cnt)
            for cur, (amt, cnt) in buckets.items()
        ),
        key=lambda t: (-t.amount_cents, t.currency),
    )

    return SendsPdfReport(
        display_name=display_name,
        generated_at=generated_at,
        last_updated=(send_rows[0].sent_at if send_rows else None),
        total_count=len(send_rows),
        totals=totals,
        all_sends=send_rows,
    )


def _money(amount_cents: int, currency: str) -> str:
    symbol = _CURRENCY_SYMBOLS.get((currency or "").upper())
    value = f"{amount_cents / 100:,.2f}"
    return f"{symbol}{value}" if symbol else f"{value} {currency}"


def _dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    # e.g. "10 Jul 2026, 12:34 AM"
    return value.strftime("%-d %b %Y, %-I:%M %p")


def _style(**kwargs) -> ParagraphStyle:
    base = {"name": kwargs.pop("name", "s"), "fontName": _SANS, "fontSize": 9, "textColor": _FG, "leading": 13}
    base.update(kwargs)
    return ParagraphStyle(**base)


_EYEBROW = _style(name="eyebrow", fontSize=8, textColor=_MUTED)
_TITLE = _style(name="title", fontName=_SERIF, fontSize=30, textColor=_FG, leading=32)
_SUBTITLE = _style(name="subtitle", fontName=_SERIF_ITALIC, fontSize=14, textColor=_MUTED, leading=17)
_LABEL = _style(name="label", fontSize=8, textColor=_MUTED)
_BIGNUM = _style(name="bignum", fontName=_SERIF, fontSize=22, textColor=_FG, leading=24)
_CARD_VALUE = _style(name="cardvalue", fontName=_SERIF, fontSize=13, textColor=_FG, leading=16)
_CELL = _style(name="cell", fontSize=9, leading=12)
_CELL_MUTED = _style(name="cellmuted", fontSize=9, textColor=_MUTED, leading=12)
_CELL_RIGHT = _style(name="cellright", fontSize=9, leading=12, alignment=TA_RIGHT)
_TH = _style(name="th", fontSize=7.5, textColor=_MUTED, leading=10)
_TH_RIGHT = _style(name="thright", fontSize=7.5, textColor=_MUTED, leading=10, alignment=TA_RIGHT)
_GENERATED = _style(name="generated", fontSize=8, textColor=_MUTED, leading=12, alignment=TA_RIGHT)
_GENERATED_VALUE = _style(name="genvalue", fontSize=8.5, textColor=_FG, leading=12, alignment=TA_RIGHT)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


class _NumberedCanvas(canvas.Canvas):
    """Draws the warm background behind every page and a footer with a
    ``page / total`` count once the total page count is known."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict] = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total: int) -> None:
        width, _ = letter
        y = 34
        self.setStrokeColor(_BORDER)
        self.setLineWidth(0.5)
        self.line(44, y + 12, width - 44, y + 12)
        self.setFillColor(_MUTED)
        self.setFont(_SERIF_ITALIC, 11)
        self.drawString(44, y, "Thanks for being part of Rob.")
        self.setFont(_SANS, 8)
        self.drawRightString(
            width - 44, y, f"robthebot.com  ·  {self._pageNumber} / {total}"
        )


def _draw_background(canv, _doc) -> None:
    width, height = letter
    canv.setFillColor(_BG)
    canv.rect(0, 0, width, height, fill=1, stroke=0)


def _summary_cards(report: SendsPdfReport, width: float) -> Table:
    def card(label: str, value_para: Paragraph) -> Table:
        inner = Table([[Paragraph(label, _LABEL)], [value_para]], colWidths=[width / 2 - 6])
        inner.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _CARD),
                    ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 14),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                    ("TOPPADDING", (0, 0), (0, 0), 12),
                    ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
                ]
            )
        )
        return inner

    left = card("TOTAL SENDS", Paragraph(str(report.total_count), _BIGNUM))
    right = card("LAST ACTIVITY", Paragraph(_dt(report.last_updated), _CARD_VALUE))
    row = Table([[left, right]], colWidths=[width / 2, width / 2])
    row.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 6),
                ("LEFTPADDING", (1, 0), (1, 0), 6),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return row


def _totals_table(report: SendsPdfReport, width: float) -> Table:
    rows = [[Paragraph("CURRENCY", _TH), Paragraph("SENDS", _TH_RIGHT), Paragraph("AMOUNT", _TH_RIGHT)]]
    if report.totals:
        for total in report.totals:
            rows.append(
                [
                    Paragraph(total.currency, _CELL),
                    Paragraph(str(total.count), _CELL_RIGHT),
                    Paragraph(_money(total.amount_cents, total.currency), _CELL_RIGHT),
                ]
            )
    else:
        rows.append([Paragraph("No totals yet.", _CELL_MUTED), "", ""])

    table = Table(rows, colWidths=[width * 0.5, width * 0.2, width * 0.3])
    style = [
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, _BORDER),
        ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 1:
            style.append(("BACKGROUND", (0, i), (-1, i), _CARD))
    table.setStyle(TableStyle(style))
    return table


def _sends_table(report: SendsPdfReport, width: float) -> Table:
    header = [
        Paragraph("#", _TH),
        Paragraph("DATE", _TH),
        Paragraph("RECIPIENT", _TH),
        Paragraph("ITEM", _TH),
        Paragraph("AMOUNT", _TH_RIGHT),
    ]
    rows = [header]
    if report.all_sends:
        for index, send in enumerate(report.all_sends):
            rows.append(
                [
                    Paragraph(str(index + 1), _CELL_MUTED),
                    Paragraph(_dt(send.sent_at), _CELL_MUTED),
                    Paragraph(_truncate(send.domme_display_name or "—", 24), _CELL),
                    Paragraph(_truncate(send.item_name or "—", 40), _CELL),
                    Paragraph(_money(send.amount_cents, send.currency), _CELL_RIGHT),
                ]
            )
    else:
        rows.append([Paragraph("No sends found.", _CELL_MUTED), "", "", "", ""])

    col_widths = [width * 0.06, width * 0.22, width * 0.28, width * 0.30, width * 0.14]
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, _BORDER),
        ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 1:
            style.append(("BACKGROUND", (0, i), (-1, i), _CARD))
    table.setStyle(TableStyle(style))
    return table


def generate_sends_pdf(report: SendsPdfReport) -> bytes:
    """Render ``report`` to PDF bytes."""
    buffer = io.BytesIO()
    margin = 44
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=48,
        bottomMargin=54,
        title=f"Your sends — {report.display_name}",
        author="Rob",
    )
    content_width = doc.width

    header = Table(
        [
            [
                [
                    Paragraph("ROBTHEBOT.COM", _EYEBROW),
                    Spacer(1, 6),
                    Paragraph("Your sends", _TITLE),
                    Paragraph(f"@{report.display_name}", _SUBTITLE),
                ],
                [
                    Paragraph("GENERATED", _GENERATED),
                    Paragraph(_dt(report.generated_at), _GENERATED_VALUE),
                ],
            ]
        ],
        colWidths=[content_width * 0.62, content_width * 0.38],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, _BORDER),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ]
        )
    )

    story = [
        header,
        Spacer(1, 16),
        _summary_cards(report, content_width),
        Spacer(1, 18),
        Paragraph("TOTALS BY CURRENCY", _LABEL),
        Spacer(1, 6),
        _totals_table(report, content_width),
        Spacer(1, 18),
        Paragraph(f"ALL SENDS  ·  {report.total_count} total", _LABEL),
        Spacer(1, 6),
        _sends_table(report, content_width),
    ]

    doc.build(
        story,
        onFirstPage=_draw_background,
        onLaterPages=_draw_background,
        canvasmaker=_NumberedCanvas,
    )
    return buffer.getvalue()

"""The billing as a PDF document (A4), in the current language.

DejaVu Sans is embedded because the PDF standard fonts cannot show names such as
"Şahin" or "Łukasz".
"""

from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.utils import formats, timezone
from django.utils.html import escape
from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from duties.models import Duty
from duties.templatetags.duty_tags import hours as format_hours
from duties.templatetags.duty_tags import money

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
GREY = colors.HexColor("#554f4a")
LINE = colors.HexColor("#b4b4b4")
HEADER_BACKGROUND = colors.HexColor("#efeeea")
CANCELLED_RED = colors.HexColor("#a51e0f")
BODY_SIZE = 9.5
CELL_PADDING = 6  # reportlab's default left and right padding of table cells
MARGIN = 20 * mm


def _register_fonts():
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT, FONT_DIR / "DejaVuSans.ttf"))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, FONT_DIR / "DejaVuSans-Bold.ttf"))


def _styles():
    base = ParagraphStyle("base", fontName=FONT, fontSize=BODY_SIZE, leading=13)
    return {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=10, textColor=GREY),
        "title": ParagraphStyle("title", parent=base, fontName=FONT_BOLD, fontSize=17, leading=22),
        "heading": ParagraphStyle("heading", parent=base, fontName=FONT_BOLD, fontSize=11),
        "bold": ParagraphStyle("bold", parent=base, fontName=FONT_BOLD),
        "right": ParagraphStyle("right", parent=base, alignment=TA_RIGHT),
        "right_bold": ParagraphStyle(
            "right_bold", parent=base, fontName=FONT_BOLD, alignment=TA_RIGHT
        ),
        "cancelled": ParagraphStyle(
            "cancelled", parent=base, fontName=FONT_BOLD, fontSize=13, textColor=CANCELLED_RED
        ),
    }


def _date(value):
    return formats.date_format(value, "SHORT_DATE_FORMAT")


def _rate(item):
    if item.pay_type == Duty.PayType.HOURLY:
        return _("%(amount)s/h") % {"amount": money(item.rate)}
    return _("%(amount)s flat") % {"amount": money(item.rate)}


def _column_widths(header, cells, total_row):
    """Date and the number columns as wide as their content; duty and helper share the rest."""

    def width(texts, font):
        return max(pdfmetrics.stringWidth(text, font, BODY_SIZE) for text in texts)

    padding = 2 * CELL_PADDING + 1
    fixed = {}
    for column in (0, 3, 4, 5):
        bold = [header[column], total_row[column]]
        normal = [row[column] for row in cells]
        fixed[column] = max(width(bold, FONT_BOLD), width(normal or [""], FONT)) + padding
    available = A4[0] - 2 * MARGIN - sum(fixed.values())
    helper = min(width([header[2]] + [row[2] for row in cells], FONT) + padding, available * 0.45)
    return [fixed[0], available - helper, helper, fixed[3], fixed[4], fixed[5]]


def filename(billing):
    return f"{_('Billing')}-{billing.number}.pdf"


def render(billing):
    """Returns the PDF of the billing as bytes."""
    _register_fonts()
    styles = _styles()

    def p(text, style="base"):
        return Paragraph(escape(str(text)), styles[style])

    buffer = BytesIO()
    title = _("Statement of duty pay %(number)s") % {"number": billing.number}
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=title,
        author=settings.SITE_NAME,
    )
    items = list(billing.items.all())
    first, last = billing.period
    story = [p(settings.SITE_NAME, "small"), Spacer(1, 2 * mm), p(title, "title")]
    if billing.status == billing.Status.CANCELLED:
        story.append(p(_("Cancelled"), "cancelled"))
    story.append(Spacer(1, 4 * mm))

    facts = [
        (_("Unit"), billing.unit.name),
        (_("Number"), billing.number),
        (_("Date"), _date(timezone.localdate(billing.created_at))),
    ]
    if first:
        facts.append((_("Duties"), f"{_date(first)} – {_date(last)}"))
    if billing.status == billing.Status.PAID and billing.paid_at:
        facts.append((_("Paid on"), _date(timezone.localdate(billing.paid_at))))
    story.append(
        Table(
            [[p(label, "bold"), p(value)] for label, value in facts],
            colWidths=[35 * mm, None],
            style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP")],
        )
    )
    story.append(Spacer(1, 6 * mm))

    header = [_("Date"), _("Duty"), _("Helper"), _("Hours"), _("Rate"), _("Amount")]
    cells = [
        [
            _date(item.duty_date),
            item.duty_title,
            item.helper_display,
            format_hours(item.hours) if item.hours is not None else "–",
            _rate(item),
            money(item.amount),
        ]
        for item in items
    ]
    total_row = ["", "", "", "", _("Total"), money(billing.total)]
    rows = [[p(text, "bold" if i < 3 else "right_bold") for i, text in enumerate(header)]]
    rows += [[p(text, "base" if i < 3 else "right") for i, text in enumerate(row)] for row in cells]
    rows.append(["", "", "", "", p(total_row[4], "right_bold"), p(total_row[5], "right_bold")])
    table = Table(rows, colWidths=_column_widths(header, cells, total_row), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BACKGROUND),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
                ("LINEABOVE", (4, -1), (-1, -1), 1, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story += [table, Spacer(1, 8 * mm)]

    story.append(p(_("Bank account"), "heading"))
    if billing.iban:
        bank = [
            (_("Account holder"), billing.account_holder),
            (_("IBAN"), billing.iban_display),
        ]
        if billing.bic:
            bank.append((_("BIC"), billing.bic))
        if billing.bank_name:
            bank.append((_("Bank"), billing.bank_name))
        story.append(
            Table(
                [[p(label, "bold"), p(value)] for label, value in bank],
                colWidths=[35 * mm, None],
                style=[("LEFTPADDING", (0, 0), (-1, -1), 0)],
            )
        )
    else:
        story.append(p(_("The unit has not entered any bank details yet.")))
    if billing.note:
        story += [Spacer(1, 6 * mm), p(_("Note"), "heading")]
        story += [p(line) for line in billing.note.splitlines() or [""]]

    created_by = billing.created_by.get_full_name() if billing.created_by else ""
    footer_text = _("%(site)s · Statement %(number)s · created by %(name)s") % {
        "site": settings.SITE_NAME,
        "number": billing.number,
        "name": created_by or "–",
    }

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 7.5)
        canvas.setFillColor(GREY)
        canvas.drawString(20 * mm, 12 * mm, footer_text)
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, _("Page %(page)d") % {"page": doc.page})
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()

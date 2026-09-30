"""How a report's cell is drawn on the page (2026-09-30): a status as a
coloured badge, a grid day's letter as a coloured square, a zero or an empty
time faint. The rows themselves stay plain values - the downloads, the table
search and the sort read those, never this markup.
"""

from dataclasses import dataclass

from django.utils.html import format_html
from django.utils.safestring import mark_safe

from reports.builders import CODE_TONE, STATUS_TONE

#: Status words that start a longer phrase ("Not counted: Unknown employee").
_PREFIX_TONE = (("Not counted", "danger"),)
_ZERO = ("0", "0:00", "")
_EMPTY = mark_safe('<span class="report-cell--empty">—</span>')
_NO_DAY = mark_safe('<span class="report-code report-code--none">·</span>')


@dataclass
class Cell:
    html: str
    css: str


def _tone(text):
    if text in STATUS_TONE:
        return STATUS_TONE[text]
    for prefix, tone in _PREFIX_TONE:
        if text.startswith(prefix):
            return tone
    return "neutral"


def cell(value, column):
    text = "" if value is None else str(value)
    numeric = "numeric" if column.numeric else ""
    kind = column.kind
    if kind == "status":
        if not text:
            return Cell(_EMPTY, "")
        return Cell(format_html('<span class="badge badge--{}">{}</span>', _tone(text), text), "")
    if kind == "code":
        css = "report-day" + (" report-day--today" if column.today else "")
        if not text:
            return Cell(_NO_DAY, css)
        return Cell(format_html('<span class="report-code report-code--{}" title="{}">{}</span>',
                                CODE_TONE.get(text, "neutral"), text, text), css)
    if kind in ("number", "duration", "clock") and text in _ZERO:
        return Cell(_EMPTY, numeric)
    return Cell(format_html("{}", text), f"{numeric} mono".strip() if kind else numeric)


def rows(page_rows, columns):
    """The page's rows as cells, column by column."""
    return [[cell(value, column) for value, column in zip(row, columns)] for row in page_rows]

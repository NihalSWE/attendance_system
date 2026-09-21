"""Excel and PDF downloads of what a page is showing.

The pages build their own rows - with their own scoping, filters, search and
sort, and their own rules about what a viewer may see - and hand them here.
This module only writes the file, and holds the rules every download shares:

- **It says what it is.** A title, the company, and a line of the filters and
  the row count at the top of every file - a header block in Excel, a subtitle
  in PDF - so a printed sheet can be read without the screen it came from.
- **It has a ceiling.** Above ``MAX_ROWS[format]`` the download is refused with
  a message naming the ceiling and asking for a narrower filter. Nothing is
  truncated silently, and one click cannot hold a worker for a minute.
- **It is recorded.** Each download is a company event (``export.downloaded``)
  with who, which page, which format, which filters and how many rows.
- **It is named for what it holds:** ``employees-headoffice-2026-09-21.xlsx``.

PDF uses reportlab (pure Python; no system libraries on the server), with the
standard Helvetica font - names in non-Latin scripts would print as boxes.
"""

import datetime
import io
import re

from django.http import HttpResponse

from auditlog.services import record_company_event

XLSX = "xlsx"
PDF = "pdf"
FORMATS = (XLSX, PDF)

#: The most rows one download may hold. Excel is cheap to write; a PDF of this
#: many rows is already ~40 landscape pages and a few seconds of work.
MAX_ROWS = {XLSX: 10_000, PDF: 1_500}

CONTENT_TYPES = {
    XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    PDF: "application/pdf",
}


class TooManyRows(Exception):
    """The download would exceed the ceiling; ``str()`` is the reader's message."""


def check_size(count, fmt, noun="rows"):
    ceiling = MAX_ROWS[fmt]
    if count > ceiling:
        label = "Excel" if fmt == XLSX else "PDF"
        raise TooManyRows(
            f"That is {count:,} {noun}. A {label} download holds at most "
            f"{ceiling:,}. Narrow the filter - one branch, a shorter date range, "
            "or a search - and download again."
        )


def slug(text):
    """``Head Office`` -> ``headoffice``: letters and digits only, for filenames."""
    return re.sub(r"[^a-z0-9]", "", (text or "").casefold()) or "company"


def filename(page, scope, period, fmt):
    return f"{page}-{slug(scope)}-{period}.{fmt}"


def record(*, actor, membership, page, fmt, filters, count):
    """The audit line for a download: salary and attendance leaving the system."""
    record_company_event(
        actor=actor, membership=membership, company=membership.company,
        action="export.downloaded", obj=membership.company,
        after={"page": page, "format": fmt, "filters": filters, "rows": count},
    )


def response(content, fmt, name):
    reply = HttpResponse(content, content_type=CONTENT_TYPES[fmt])
    reply["Content-Disposition"] = f'attachment; filename="{name}"'
    return reply


def _cell_text(value):
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.strftime("%d %b %Y %H:%M")
    if isinstance(value, datetime.date):
        return value.strftime("%d %b %Y")
    return str(value)


# --- Excel -----------------------------------------------------------------


def table_xlsx(*, title, lines, headers, rows, numeric=(), widths=None):
    """One sheet: the title and the filter lines, a blank row, then the table.

    ``rows`` are lists of plain values; numbers stay numbers (``numeric`` names
    the column indexes to right-align and format with thousands separators),
    dates stay dates, so the sheet can be summed and sorted.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    sheet = book.active
    sheet.title = title[:31]
    sheet.append([title])
    sheet["A1"].font = Font(bold=True, size=13)
    for line in lines:
        sheet.append([line])
    sheet.append([])
    header_row = sheet.max_row + 1
    sheet.append(list(headers))
    fill = PatternFill("solid", fgColor="E8ECF1")
    for cell in sheet[header_row]:
        cell.font = Font(bold=True)
        cell.fill = fill
    for row in rows:
        sheet.append([value if value is not None else "" for value in row])
    for index in numeric:
        letter = get_column_letter(index + 1)
        for (cell,) in sheet.iter_rows(min_row=header_row + 1, min_col=index + 1,
                                       max_col=index + 1):
            cell.alignment = Alignment(horizontal="right")
            if isinstance(cell.value, (int, float)) or hasattr(cell.value, "as_tuple"):
                cell.number_format = "#,##0.00"
        sheet[f"{letter}{header_row}"].alignment = Alignment(horizontal="right")
    for index, heading in enumerate(headers, start=1):
        width = (widths or {}).get(index - 1) or max(12, min(40, len(str(heading)) + 6))
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = f"A{header_row + 1}"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# --- PDF -------------------------------------------------------------------


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=14, leading=17,
                                alignment=0, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontSize=8, leading=10,
                              textColor=colors.HexColor("#4a5a6b")),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontSize=7.5, leading=9),
        "head": ParagraphStyle("h", parent=base["Normal"], fontSize=7.5, leading=9,
                               fontName="Helvetica-Bold"),
    }


def _document(buffer, title, landscape_page=True):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    size = landscape(A4) if landscape_page else A4

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(size[0] - 12 * mm, 8 * mm, f"{title} · page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=size, title=title,
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=14 * mm)
    return doc, footer


def _escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def table_pdf(*, title, lines, headers, rows, numeric=(), widths=None):
    """A landscape A4 table: title, filter lines, then the rows.

    The header repeats on every page and long cells wrap. ``widths`` are
    relative column weights; the table fills the page width.
    """
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    styles = _styles()
    buffer = io.BytesIO()
    doc, footer = _document(buffer, title)
    story = [Paragraph(_escape(title), styles["title"])]
    story += [Paragraph(_escape(line), styles["sub"]) for line in lines]
    story.append(Spacer(1, 6))

    right = styles["cell"].clone("r", alignment=2)
    head_right = styles["head"].clone("hr", alignment=2)
    data = [[Paragraph(_escape(str(h)), head_right if i in numeric else styles["head"])
             for i, h in enumerate(headers)]]
    for row in rows:
        data.append([Paragraph(_escape(_cell_text(value)), right if i in numeric else styles["cell"])
                     for i, value in enumerate(row)])

    weights = [(widths or {}).get(i, 1) for i in range(len(headers))]
    total = sum(weights)
    col_widths = [doc.width * w / total for w in weights]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8ecf1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#9aa7b5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(table)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def grid_pdf(*, title, lines, blocks):
    """Calendar grids, one block after another.

    ``blocks`` is a list of ``(heading, header_row, week_rows)``: a heading
    line (a person's name, say), the weekday names, and one list per week of
    cell texts - a cell may hold several lines separated by newlines.
    """
    from reportlab.lib import colors
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

    styles = _styles()
    buffer = io.BytesIO()
    doc, footer = _document(buffer, title)
    story = [Paragraph(_escape(title), styles["title"])]
    story += [Paragraph(_escape(line), styles["sub"]) for line in lines]
    story.append(Spacer(1, 6))
    cell = styles["cell"]
    for heading, header_row, weeks in blocks:
        data = [[Paragraph(f"<b>{_escape(h)}</b>", cell) for h in header_row]]
        for week in weeks:
            data.append([Paragraph(_escape(text).replace("\n", "<br/>"), cell)
                         for text in week])
        grid = Table(data, colWidths=[doc.width / len(header_row)] * len(header_row),
                     repeatRows=1)
        grid.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c3ccd6")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8ecf1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 1), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ]))
        part = []
        if heading:
            part.append(Paragraph(f"<b>{_escape(heading)}</b>", styles["sub"]))
            part.append(Spacer(1, 3))
        part += [grid, Spacer(1, 10)]
        story.append(KeepTogether(part))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()

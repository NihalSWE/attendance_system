"""The shared download rules (common/exports.py), and helpers the page tests use.

``xlsx_table`` and ``pdf_text`` read a download back, so a page's tests can
assert what is actually in the file - which names, which columns, which lines -
rather than only that a file came out.
"""

import base64
import io
import re
import zlib

from django.test import SimpleTestCase

from common import exports


def xlsx_table(content):
    """``(header_lines, headers, rows)`` of the first sheet of a download."""
    from openpyxl import load_workbook

    sheet = load_workbook(io.BytesIO(content), data_only=True).worksheets[0]
    values = [list(row) for row in sheet.iter_rows(values_only=True)]
    blank = next(i for i, row in enumerate(values) if not any(cell is not None for cell in row))
    lines = [row[0] for row in values[:blank]]
    headers, *rows = values[blank + 1:]
    return lines, headers, rows


def pdf_text(content):
    """Every string drawn in a reportlab PDF, joined - enough to search."""
    assert content.startswith(b"%PDF"), "not a PDF"
    text = []
    # reportlab closes a stream as "...~>endstream", with no newline between.
    for raw in re.findall(rb"stream\r?\n(.*?)\s*endstream", content, re.S):
        raw = raw.strip()
        if raw.endswith(b"~>"):             # reportlab's default ASCII85 layer
            try:
                raw = base64.a85decode(raw[:-2].replace(b"\n", b"").replace(b"\r", b""))
            except ValueError:
                continue
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        text += re.findall(rb"\((.*?)(?<!\\)\)\s*Tj", raw)
    return " ".join(part.decode("latin-1").replace("\\(", "(").replace("\\)", ")")
                    for part in text)


class SharedRulesTests(SimpleTestCase):
    def test_the_ceiling_refuses_and_names_itself(self):
        exports.check_size(exports.MAX_ROWS[exports.XLSX], exports.XLSX)   # at it: fine
        with self.assertRaises(exports.TooManyRows) as caught:
            exports.check_size(exports.MAX_ROWS[exports.XLSX] + 1, exports.XLSX, noun="rows")
        message = str(caught.exception)
        self.assertIn(f"{exports.MAX_ROWS[exports.XLSX]:,}", message)
        self.assertIn("Narrow the filter", message)

    def test_the_pdf_ceiling_is_lower(self):
        self.assertLess(exports.MAX_ROWS[exports.PDF], exports.MAX_ROWS[exports.XLSX])

    def test_filenames_carry_the_page_the_scope_and_the_period(self):
        self.assertEqual(exports.filename("employees", "Head Office", "2026-09-21", "xlsx"),
                         "employees-headoffice-2026-09-21.xlsx")
        self.assertEqual(exports.filename("attendance", "Head Office", "2026-09", "pdf"),
                         "attendance-headoffice-2026-09.pdf")

    def test_the_writers_round_trip(self):
        content = exports.table_xlsx(title="T", lines=["one", "two"], headers=["A", "B"],
                                     rows=[[1, "x"], [2, "y"]], numeric=(0,))
        lines, headers, rows = xlsx_table(content)
        self.assertEqual((lines, headers, rows), (["T", "one", "two"], ["A", "B"],
                                                   [[1, "x"], [2, "y"]]))
        text = pdf_text(exports.table_pdf(title="Title here", lines=["a line"],
                                          headers=["A"], rows=[["Rahim (HQ)"]]))
        self.assertIn("Title here", text)
        self.assertIn("Rahim (HQ)", text)

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


# Bangla names in PDFs (Ajay, 2026-09-21): HR types real names, and a printed
# sheet of boxes reads as lost data. Written with \u escapes so the file stays
# ASCII: "রহিম আহমেদ" (Rahim Ahmed), "কি", "লক্ষ্মী" (Lakshmi, a three-letter conjunct).
RAHIM_AHMED = "\u09b0\u09b9\u09bf\u09ae \u0986\u09b9\u09ae\u09c7\u09a6"
KI = "\u0995\u09bf"
LAKSHMI = "\u09b2\u0995\u09cd\u09b7\u09cd\u09ae\u09c0"
BANGLA_NAMES = (RAHIM_AHMED, LAKSHMI, "\u09a8\u09c1\u09b8\u09b0\u09be\u09a4 \u099c\u09be\u09b9\u09be\u09a8",
                "\u09ae\u09cb\u0983 \u09ab\u099c\u09b2\u09c7 \u09b0\u09be\u09ac\u09cd\u09ac\u09bf")


def glyph_ids(font_name, text):
    """The glyphs a registered TTF font draws for ``text``, after shaping.

    Glyph 0 is .notdef - the empty box a font draws for a character it lacks.
    """
    import uharfbuzz
    from reportlab.pdfbase import pdfmetrics

    buffer = uharfbuzz.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()
    uharfbuzz.shape(pdfmetrics.getFont(font_name).hbFont(10), buffer)
    return [info.codepoint for info in buffer.glyph_infos]


class BanglaInPdfTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        exports._register_fonts()

    def test_bangla_names_draw_real_glyphs_not_boxes(self):
        for name in BANGLA_NAMES + ("Rahim Ahmed", "Employee ID 445962 · 09:00 – 18:00"):
            with self.subTest(name=name.encode("unicode_escape")):
                self.assertNotIn(0, glyph_ids(exports.FONT, name))
                self.assertNotIn(0, glyph_ids(exports.FONT_BOLD, name))

    def test_the_check_would_catch_boxes(self):
        """Control: an English-only font draws Bangla as boxes, and the check sees it."""
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        if "ControlVera" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("ControlVera", "Vera.ttf"))
        self.assertIn(0, glyph_ids("ControlVera", RAHIM_AHMED))

    def test_bangla_is_shaped_not_just_drawn(self):
        """Letters present is not enough: in the wrong order it is still unreadable."""
        from reportlab.pdfbase.ttfonts import shapeStr

        # The vowel sign "ি" is written after "ক" but drawn before it.
        self.assertEqual(str(shapeStr(KI, exports.FONT, 10)), KI[1] + KI[0])
        # A three-letter conjunct (ক্ষ্ম) becomes one joined glyph: 7 characters, 3 glyphs.
        self.assertEqual(len(glyph_ids(exports.FONT, LAKSHMI)), 3)

    def test_every_pdf_style_uses_the_bundled_font_with_shaping_on(self):
        """reportlab defaults shaping OFF; one style missing it prints scrambled Bangla."""
        for key, style in exports._styles().items():
            with self.subTest(style=key):
                self.assertIn(style.fontName, (exports.FONT, exports.FONT_BOLD))
                self.assertTrue(style.shaping)

    def test_a_pdf_with_a_bangla_name_carries_only_the_bundled_font(self):
        content = exports.table_pdf(
            title=f"\u09a2\u09be\u0995\u09be Ltd \u2014 Employees",
            lines=[f"Search: \"{KI}\""],
            headers=["Employee ID", "Name"],
            rows=[["445962", RAHIM_AHMED], ["445963", "Rahim Ahmed"]])
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertIn(b"HindSiliguri", content)
        self.assertNotIn(b"Helvetica", content)
        grid = exports.grid_pdf(title="Calendar", lines=[RAHIM_AHMED],
                                blocks=[("", ["Mon"], [["1\n" + KI]])])
        self.assertIn(b"HindSiliguri", grid)
        self.assertNotIn(b"Helvetica", grid)

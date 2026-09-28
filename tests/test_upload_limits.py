"""How much work one upload can make the server do.

`MAX_OCR_PAGES` capped the OCR fallback and nothing capped anything else. A
valid 594 KB PDF whose page tree references one small page object 99,000 times
took 78 seconds to read and produced 7.8 MB of text -- which then went into the
validation prompt whole (~2M tokens), into `case_documents.ocr_text`, back out
of `submitted_kt_docs()` on every manager page load, and into the browser as a
`<pre>`. pypdf's own 100,000-page ceiling was the only bound. A signed-in
leaver could do that from the upload form as many times as they liked.

Local and deterministic: nothing here touches the gateway or the OCR engine.
"""
from __future__ import annotations

import time

import pytest

from backend import ocr


def _page_bomb(pages: int) -> bytes:
    """A structurally valid PDF whose page tree points at one page N times.

    The same trick the review used: tiny on disk, enormous to walk. Built
    through pypdf so the xref is real -- a hand-rolled one is rejected before
    it can demonstrate anything.
    """
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, NameObject, NumberObject

    writer = PdfWriter()
    writer.add_page(PdfReader(BytesIO(_long_pdf(200))).pages[0])

    tree = writer._root_object[NameObject("/Pages")]
    kid = tree[NameObject("/Kids")][0]
    tree[NameObject("/Kids")] = ArrayObject([kid] * pages)
    tree[NameObject("/Count")] = NumberObject(pages)

    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _long_pdf(chars: int) -> bytes:
    from io import BytesIO

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    written = 0
    while written < chars:
        y = 800
        while y > 40 and written < chars:
            line = "filler " * 14
            c.drawString(40, y, line)
            written += len(line)
            y -= 12
        c.showPage()
    c.save()
    return buf.getvalue()


def test_a_page_bomb_is_bounded_in_time_and_output():
    data = _page_bomb(99_000)
    assert len(data) < 10 * 1024 * 1024, "has to be under the upload cap to matter"

    start = time.perf_counter()
    out = ocr.extract_document(data, "application/pdf", "bomb.pdf")
    elapsed = time.perf_counter() - start

    assert out["pages"] == 99_000, "the file really does declare that many pages"
    assert out["pages_read"] == ocr.MAX_PDF_PAGES
    assert len(out["text"]) <= ocr.MAX_TEXT_CHARS + len(ocr._TRUNCATION_MARK)
    # Was 78.5 seconds. Generous bound so a slow CI box does not flake it.
    assert elapsed < 10, f"took {elapsed:.1f}s"


def test_a_long_but_honest_pdf_is_cut_and_says_so():
    out = ocr.extract_document(_long_pdf(ocr.MAX_TEXT_CHARS * 2),
                               "application/pdf", "manual.pdf")
    assert out["truncated"] is True
    assert "truncated" in out["text"][-200:]
    assert len(out["text"]) <= ocr.MAX_TEXT_CHARS + len(ocr._TRUNCATION_MARK)


def test_an_ordinary_form_is_untouched():
    """The cap must be invisible to the documents this actually collects."""
    out = ocr.extract_document(_long_pdf(2_000), "application/pdf", "nodues.pdf")
    assert out["truncated"] is False
    assert "truncated" not in out["text"]
    assert out["pages_read"] == out["pages"]


def test_html_is_capped_by_the_same_gate():
    """The cap lives in `_finish`, which every extractor goes through, so a
    fourth source cannot arrive without one."""
    huge = b"<p>" + (b"filler " * 20_000) + b"</p>"
    out = ocr.extract_document(huge, "text/html", "big.html")
    assert out["truncated"] is True
    assert len(out["text"]) <= ocr.MAX_TEXT_CHARS + len(ocr._TRUNCATION_MARK)


@pytest.mark.parametrize("limit", ["MAX_PDF_PAGES", "MAX_OCR_PAGES",
                                   "MAX_OCR_IMAGES", "MAX_TEXT_CHARS"])
def test_every_dimension_has_a_bound(limit):
    """Capping pages left images unbounded; capping the OCR path left the text
    layer unbounded. Each dimension needs its own number."""
    assert getattr(ocr, limit) > 0

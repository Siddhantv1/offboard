"""Reading a PDF or an HTML form, not just a photograph of one.

The upload only ever accepted images, so somebody holding a signed PDF -- which
is what an exit form actually is, most of the time -- had to print it, or
photograph their screen, to satisfy a step whose whole purpose was to check the
document's contents. And the check itself was worse for it: OCR of a photo of a
PDF, judged by a model told to forgive garbled characters, when the exact text
was sitting in the file.

No network here. `extract_document` is local by design -- that is the point of
it -- so these run the real extractors over real bytes. Only `validate_upload`
calls the gateway, and nothing here reaches it.
"""
from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from backend import auth, ocr
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import CaseDocument, User
from backend.main import app

TEXT = "No-Dues Declaration\nEmployee: Aarav Mehta\nSigned: 2026-09-01"


def _pdf(text: str = TEXT) -> bytes:
    """A real PDF with a real text layer, built with the library already here
    for the leaving documents."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in text.splitlines():
        c.drawString(72, y, line)
        y -= 18
    c.save()
    return buf.getvalue()


def _png(size: tuple[int, int] = (40, 40)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


# -- sniffing -----------------------------------------------------------------
def test_the_bytes_decide_not_the_label():
    """The declared content type is a claim by the uploader's browser, and it
    also governs how the file is served back out -- so an HTML file labelled as
    a PNG must not be stored as, and later served as, an image."""
    assert ocr.sniff_kind(_pdf(), "image/png", "cv.png") == "pdf"
    assert ocr.sniff_kind(b"<!doctype html><p>hi", "image/png", "x.png") == "html"
    assert ocr.sniff_kind(_png(), "text/html", "x.html") == "image"


def test_an_unreadable_file_is_refused_rather_than_guessed_at():
    assert ocr.sniff_kind(b"MZ\x90\x00this is an exe", "", "form.exe") is None
    assert ocr.sniff_kind(b"\x00\x01\x02\x03", "", "") is None


def test_the_extension_settles_html_with_no_doctype():
    """An HTML fragment saved out of a mail client has no doctype and no <html>
    tag, so the bytes cannot answer it and the filename has to."""
    assert ocr.sniff_kind(b"<p>Bank details</p>", "", "nodues.html") == "html"


# -- reading ------------------------------------------------------------------
def test_a_pdf_is_read_exactly_not_recognised():
    """The whole reason PDFs are worth accepting separately: the characters are
    in the file. No OCR engine is touched, so this works on a machine that has
    none."""
    out = ocr.extract_document(_pdf(), "application/pdf", "nodues.pdf")
    assert out["engine"] == "pdf-text"
    assert out["kind"] == "pdf"
    assert out["pages"] == 1
    assert "No-Dues Declaration" in out["text"]
    assert "Aarav Mehta" in out["text"]
    # Nothing was guessed, so there is no confidence to report. 0.0 would read
    # as "recognised it, badly".
    assert out["confidence"] is None


def test_html_gives_up_its_visible_text_and_nothing_else():
    html = b"""<!doctype html><html><head><title>Ignore me</title>
    <style>body{color:red}</style><script>alert('xss')</script></head>
    <body><h1>No-Dues Declaration</h1><p>Employee: Aarav Mehta</p>
    <p>Signed: 2026-09-01</p></body></html>"""
    out = ocr.extract_document(html, "text/html", "nodues.html")

    assert out["engine"] == "html-text"
    assert "No-Dues Declaration" in out["text"]
    assert "Employee: Aarav Mehta" in out["text"]
    # Script and style contents would otherwise be handed to the model as if
    # they were the document's wording.
    assert "alert" not in out["text"]
    assert "color:red" not in out["text"]
    assert "Ignore me" not in out["text"]


def test_block_tags_become_line_breaks():
    """A form is label/value pairs, and the model reads them as pairs. Flatten
    them onto one line and every field runs into the next."""
    out = ocr.extract_document(b"<p>Name: A</p><p>Date: B</p>", "text/html", "f.html")
    assert out["text"] == "Name: A\nDate: B"


def test_a_file_we_cannot_read_raises_something_sayable():
    with pytest.raises(ocr.UnreadableUpload) as caught:
        ocr.extract_document(b"MZ\x90\x00", "application/x-msdownload", "x.exe")
    assert "PDF" in str(caught.value)


def test_a_corrupt_pdf_is_reported_not_crashed():
    with pytest.raises(ocr.UnreadableUpload):
        ocr.extract_document(b"%PDF-1.4\nnot actually a pdf", "application/pdf", "x.pdf")


# -- the route ----------------------------------------------------------------
@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def employee():
    cases = store.exit_cases()
    if not cases:
        pytest.skip("no seeded exit case")
    emp_id = str(cases[0].meta["employee_id"])
    email = f"pytest-fmt-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-format"
    with session_scope() as s:
        s.add(User(email=email, password_hash=auth.hash_password(password),
                   role="employee", employee_id=emp_id, full_name="Pytest Formats"))
    yield email, password, str(cases[0].meta["case_id"])
    with session_scope() as s:
        s.execute(delete(User).where(User.email == email))


def _login(client: TestClient, email: str, password: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    client.headers["X-CSRF-Token"] = client.cookies.get(auth.CSRF_COOKIE)


def test_the_route_refuses_a_file_it_cannot_read(client, employee):
    """There was no type gate at all. The route took whatever bytes arrived and
    stored them; the complaint came later, from PIL, as "could not read the
    image" -- after an executable had been written to the database."""
    email, password, _ = employee
    _login(client, email, password)
    name = "No-dues declaration"

    resp = client.post(
        "/api/me/case/documents",
        data={"document_name": name},
        files={"file": ("payload.exe", b"MZ\x90\x00binary", "application/x-msdownload")},
    )
    assert resp.status_code == 415
    assert "PDF" in resp.json()["detail"]

    with session_scope() as s:
        stored = s.query(CaseDocument).filter(
            CaseDocument.filename == "payload.exe").count()
    assert stored == 0, "the refused file must not have been persisted"


# -- serving it back ----------------------------------------------------------
def test_an_uploaded_html_file_is_never_rendered_on_our_origin():
    """Accepting HTML and serving it inline would be stored XSS handed to us by
    the feature that accepts it: the file would run as a page of this app, at
    this app's origin, with the reader's session attached."""
    from backend.api.cases_routes import _stored_file

    resp = _stored_file((b"<script>alert(1)</script>", "text/html", "form.html"))
    assert resp.headers["content-disposition"].startswith("attachment")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in resp.headers["content-security-policy"]


@pytest.mark.parametrize("content_type", ["application/pdf", "image/png", "image/jpeg"])
def test_what_is_safe_to_look_at_is_shown_inline(content_type):
    """The point of accepting a PDF is reading it beside the verdict rather
    than collecting a folder of downloads."""
    from backend.api.cases_routes import _stored_file

    resp = _stored_file((b"x", content_type, "form"))
    assert resp.headers["content-disposition"].startswith("inline")
    assert resp.headers["cache-control"] == "private, no-store"

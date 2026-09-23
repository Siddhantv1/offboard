"""Reading the text out of an uploaded exit document, locally.

Agent 12 validates uploaded forms. Rather than send the file to the LLM (slow,
and it would leave the machine), the text is extracted here and only that goes
to a short LLM call. Same idea as the RAG pipeline: cheap local processing
first; the model only ever sees compact text.

Three kinds of upload, and only one of them needs OCR:

  * **PDF** -- almost always carries a real text layer, because almost every
    exit form is produced by a computer rather than photographed. `pypdf`
    reads it exactly, in milliseconds, with none of OCR's errors. A PDF that
    is a wrapper around a scan has no text layer, and its pages are then one
    embedded image each, which we pull out and OCR like any other picture.
  * **HTML** -- "save as" out of a web form, or a printed email. Tags are
    stripped with the standard library; there is nothing to recognise.
  * **Image** (PNG/JPEG/WebP) -- a phone photo or a scanner's output. This is
    the only case that needs OCR, and it used to be the only case accepted at
    all, which asked somebody holding a signed PDF to photograph their screen.

Two OCR engines, picked at first use:
  1. `winocr` -- the OCR engine built into Windows 10/11 (Windows.Media.Ocr).
     Measured ~0.4 s per page on this machine; no model download.
  2. `rapidocr` -- PP-OCR models on ONNX Runtime, CPU only, any OS. Slower
     here (6-10 s) but portable; used when the Windows engine is unavailable.

The chosen engine is cached for the process. `warm_up()` runs at server start
so the first upload doesn't pay the load cost.
"""
from __future__ import annotations

import io
import logging
import re
import sys
import threading
import time
from html.parser import HTMLParser
from typing import Any, Callable

_engine: Callable[[Any], dict[str, Any]] | None = None
_engine_name = ""
_lock = threading.Lock()

MAX_SIDE = 1600  # downscale very large photos; accuracy is fine well below this

#: Pages OCR'd when a PDF turns out to have no text layer. Exit forms are one
#: to three pages; the cap is what stops a mistakenly-uploaded 200-page manual
#: occupying the OCR engine for ten minutes while an employee watches a
#: spinner. The text layer path has no cap -- it costs milliseconds a page.
MAX_OCR_PAGES = 5

#: What an upload may be. The extension is what the person sees in their file
#: dialog; the media type is what the browser sends. Both are advisory -- the
#: bytes are sniffed -- but they are what the error message can talk about.
ACCEPTED = {
    "image": {"types": ("image/png", "image/jpeg", "image/webp"),
              "extensions": (".png", ".jpg", ".jpeg", ".webp")},
    "pdf": {"types": ("application/pdf",), "extensions": (".pdf",)},
    "html": {"types": ("text/html", "application/xhtml+xml"),
             "extensions": (".html", ".htm")},
}

#: For a human. "PDF, PNG, JPG, WebP or HTML".
ACCEPTED_LABEL = "PDF, PNG, JPG, WebP or HTML"


class OCRUnavailable(RuntimeError):
    """No OCR engine could be loaded."""


class UnreadableUpload(ValueError):
    """The bytes are not a document we can read. The message is safe to show."""


def sniff_kind(data: bytes, content_type: str = "", filename: str = "") -> str | None:
    """"image", "pdf", "html", or None for something we cannot read.

    Sniffs the bytes first and only then falls back to what the client said.
    A declared content type is a claim by the uploader's browser, and this is
    the gate that decides both how the file is parsed and how it is served
    back -- an HTML file mislabelled as an image would otherwise be stored and
    then handed out with an image's inline disposition.

    One function, used by the upload route to refuse early and by
    `extract_document` to dispatch, so the set of things we accept and the set
    of things we can read cannot drift apart.
    """
    head = data[:1024]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8")):
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"

    # HTML has no magic number. A leading doctype or tag is the usual shape;
    # an HTML fragment saved from a mail client may have neither, so the
    # declared type and the extension are allowed to settle it.
    lowered = head.lstrip()[:512].lower()
    if lowered.startswith((b"<!doctype html", b"<html", b"<?xml")) or b"<html" in lowered:
        return "html"

    declared = (content_type or "").split(";")[0].strip().lower()
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    for kind, spec in ACCEPTED.items():
        if declared in spec["types"] or suffix in spec["extensions"]:
            return kind
    return None


def _load_windows():
    import winocr  # type: ignore

    def run(img) -> dict[str, Any]:
        res = winocr.recognize_pil_sync(img, "en")
        lines = [str(ln.get("text", "")) for ln in (res.get("lines") or []) if ln.get("text")]
        return {"lines": [{"text": t, "confidence": None} for t in lines], "confidence": None}

    return run, "windows-ocr"


def _load_rapidocr():
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    import numpy as np
    from rapidocr import RapidOCR  # type: ignore

    ocr = RapidOCR()

    def run(img) -> dict[str, Any]:
        out = ocr(np.array(img), use_cls=False)  # forms are upright; skip the angle classifier
        txts = list(getattr(out, "txts", None) or [])
        scores = [float(s) for s in (getattr(out, "scores", None) or [])]
        return {
            "lines": [{"text": t, "confidence": round(s, 3)} for t, s in zip(txts, scores)],
            "confidence": round(sum(scores) / len(scores), 3) if scores else 0.0,
        }

    return run, "rapidocr-onnx"


def _get_engine():
    global _engine, _engine_name
    if _engine is None:
        with _lock:
            if _engine is None:
                loaders = ([_load_windows] if sys.platform == "win32" else []) + [_load_rapidocr]
                errors: list[str] = []
                for loader in loaders:
                    try:
                        _engine, _engine_name = loader()
                        break
                    except Exception as exc:  # noqa: BLE001 - try the next engine
                        errors.append(f"{loader.__name__}: {exc}")
                if _engine is None:
                    raise OCRUnavailable("No local OCR engine available (" + "; ".join(errors) + ")")
    return _engine


def engine_name() -> str:
    return _engine_name or "not loaded"


def warm_up() -> None:
    """Load the engine at server start so the first upload isn't slow."""
    try:
        _get_engine()
        print(f"[ocr] ready: {_engine_name}")
    except OCRUnavailable as exc:
        print(f"[ocr] {exc}")


def extract_document(data: bytes, content_type: str = "",
                     filename: str = "") -> dict[str, Any]:
    """Text out of whatever was uploaded -> the same shape whichever way it came.

    Callers get `{text, lines, confidence, seconds, engine, kind, ...}` and do
    not care which path produced it. `engine` says which one did, and it is
    stored on the row, so a reviewer looking at a rejected document can tell an
    OCR misread from a genuinely blank form.

    Raises `UnreadableUpload` for a file we cannot read at all, and
    `OCRUnavailable` only on the paths that actually need OCR -- a text-layer
    PDF still works on a machine with no OCR engine.
    """
    kind = sniff_kind(data, content_type, filename)
    if kind == "pdf":
        return _extract_pdf(data)
    if kind == "html":
        return _extract_html(data)
    if kind == "image":
        return extract_text(data)
    raise UnreadableUpload(
        f"That file is not something we can read. Send a {ACCEPTED_LABEL} file."
    )


def _finish(text: str, *, engine: str, seconds: float, **extra: Any) -> dict[str, Any]:
    """The common return shape. `lines` exists for every path because the
    stored record and the review UI read it regardless of where text came
    from; for a text layer a line is just a line."""
    lines = [ln for ln in (l.strip() for l in text.splitlines()) if ln]
    return {
        "text": "\n".join(lines),
        "lines": [{"text": ln, "confidence": None} for ln in lines],
        # None, not 0.0: nothing was recognised, so there is no confidence to
        # report. Zero would read as "recognised it, badly".
        "confidence": None,
        "seconds": round(seconds, 2),
        "engine": engine,
        **extra,
    }


def _extract_pdf(data: bytes) -> dict[str, Any]:
    """A PDF's text layer, falling back to OCR of its embedded page images.

    The fallback is not a rasteriser. Rendering a PDF page to a bitmap needs
    Poppler or PyMuPDF, and neither is here; but a PDF that wraps a scan holds
    exactly one full-page image per page, and `pypdf` can hand that image over
    directly. That covers what scanners actually produce.

    ponytail: no rasteriser, so a PDF whose text is drawn as vectors (rare
    outside of CAD exports) reads as empty. Add `pymupdf` and render pages if
    that ever shows up.
    """
    from pypdf import PdfReader

    t0 = time.perf_counter()
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages
    except Exception as exc:  # noqa: BLE001 - corrupt or encrypted file
        raise UnreadableUpload(f"That PDF could not be opened ({exc}).") from exc

    layer = "\n".join((page.extract_text() or "") for page in pages)
    if layer.strip():
        return _finish(layer, engine="pdf-text", seconds=time.perf_counter() - t0,
                       kind="pdf", pages=len(pages))

    # No text layer: it is a scan in a PDF wrapper. OCR the embedded images.
    run = _get_engine()
    from PIL import Image, ImageOps

    chunks: list[str] = []
    scanned = 0
    for page in pages[:MAX_OCR_PAGES]:
        for image in page.images:
            try:
                img = Image.open(io.BytesIO(image.data))
                img = ImageOps.exif_transpose(img).convert("RGB")
            except Exception:  # noqa: BLE001 - an exotic codec PIL cannot open
                continue
            if max(img.size) > MAX_SIDE:
                scale = MAX_SIDE / max(img.size)
                img = img.resize((round(img.width * scale), round(img.height * scale)))
            chunks.extend(ln["text"] for ln in run(img)["lines"])
            scanned += 1

    return _finish("\n".join(chunks), engine=f"pdf-scan+{_engine_name}",
                   seconds=time.perf_counter() - t0, kind="pdf",
                   pages=len(pages), ocr_pages=scanned)


class _HTMLText(HTMLParser):
    """Visible text only. Everything a browser would not show is dropped --
    including `<script>`, whose contents would otherwise be handed to the LLM
    as if it were the document's wording."""

    HIDDEN = {"script", "style", "head", "title", "meta", "link", "noscript"}
    BREAK = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
             "table", "section", "article", "header", "footer", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.HIDDEN:
            self._hidden += 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.HIDDEN:
            self._hidden = max(0, self._hidden - 1)
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def _extract_html(data: bytes) -> dict[str, Any]:
    """Visible text out of an HTML file. No OCR, no dependency."""
    t0 = time.perf_counter()
    for encoding in ("utf-8", "cp1252"):
        try:
            source = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        source = data.decode("utf-8", errors="replace")

    parser = _HTMLText()
    parser.feed(source)
    parser.close()
    # Collapse the runs of spaces that markup leaves behind, but keep the line
    # breaks the block tags put in -- a form's field labels and values are on
    # separate lines and the LLM reads them as pairs.
    text = re.sub(r"[ \t ]+", " ", "".join(parser.parts))
    return _finish(text, engine="html-text", seconds=time.perf_counter() - t0,
                   kind="html")


def extract_text(image_bytes: bytes) -> dict[str, Any]:
    """OCR an image (PNG/JPEG/WebP bytes) -> {text, lines, confidence, seconds, engine}."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > MAX_SIDE:
        scale = MAX_SIDE / max(img.size)
        img = img.resize((round(img.width * scale), round(img.height * scale)))

    run = _get_engine()
    t0 = time.perf_counter()
    out = run(img)
    seconds = round(time.perf_counter() - t0, 2)
    lines = out["lines"]
    return {
        "text": "\n".join(ln["text"] for ln in lines),
        "lines": lines,
        "confidence": out.get("confidence"),
        "seconds": seconds,
        "engine": _engine_name,
        "kind": "image",
        "image_size": list(img.size),
    }

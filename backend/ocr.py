"""Local OCR for uploaded exit documents.

Agent 12 validates uploaded forms. Rather than send the image to the LLM
(slow, and the image would leave the machine), we OCR it locally and pass only
the extracted *text* to a short LLM call. Same idea as the RAG pipeline: cheap
local processing first; the model only ever sees compact text.

Two engines, picked at first use:
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
import sys
import threading
import time
from typing import Any, Callable

_engine: Callable[[Any], dict[str, Any]] | None = None
_engine_name = ""
_lock = threading.Lock()

MAX_SIDE = 1600  # downscale very large photos; accuracy is fine well below this


class OCRUnavailable(RuntimeError):
    """No OCR engine could be loaded."""


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
        "image_size": list(img.size),
    }

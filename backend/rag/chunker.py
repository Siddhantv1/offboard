"""Heading-aware, overlapping chunker.

Walks a Markdown doc tracking the heading breadcrumb (H1 > H2 > H3) and emits
character-bounded chunks with overlap, so a policy clause or FAQ Q&A stays intact
and each chunk carries where it came from. This replaces the old heading-only
split that produced passages too coarse (whole sections) or too thin (bare intros).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import RAG
from ..data_store import Doc


@dataclass
class Chunk:
    id: str
    text: str
    source: str      # doc title
    heading: str     # breadcrumb, e.g. "Final Settlement > Timeline"
    index: int       # ordinal within the doc


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _breadcrumb(stack: list[tuple[int, str]]) -> str:
    return " > ".join(h for _, h in stack)


def _window(text: str, size: int, overlap: int) -> list[str]:
    """Split text into ~`size`-char windows, breaking on paragraph/sentence
    boundaries where possible, with `overlap` chars carried between windows."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []

    out: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            # prefer to break at a paragraph, then sentence, then space
            window = text[start:end]
            for sep in ("\n\n", ". ", "\n", " "):
                cut = window.rfind(sep)
                if cut > size * 0.5:
                    end = start + cut + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            out.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return out


def chunk_doc(doc: Doc) -> list[Chunk]:
    """Chunk one doc into heading-scoped, overlapping windows."""
    stack: list[tuple[int, str]] = []  # (heading level, text)
    sections: list[tuple[str, list[str]]] = []  # (breadcrumb, lines)
    buf: list[str] = []

    def flush() -> None:
        if buf:
            sections.append((_breadcrumb(stack), buf.copy()))
            buf.clear()

    for line in doc.body.splitlines():
        m = _HEADING.match(line.strip())
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2).strip()))
        else:
            buf.append(line)
    flush()

    chunks: list[Chunk] = []
    idx = 0
    for breadcrumb, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        heading = breadcrumb or doc.title
        for window in _window(body, RAG.chunk_chars, RAG.chunk_overlap):
            # Prefix the heading so retrieval matches section titles too.
            text = f"{heading}\n{window}" if heading != doc.title else window
            chunks.append(Chunk(
                id=f"{doc.slug}#{idx}",
                text=text,
                source=doc.title,
                heading=heading,
                index=idx,
            ))
            idx += 1
    return chunks

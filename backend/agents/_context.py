"""Helpers that turn stored Markdown docs into compact LLM context strings."""
from __future__ import annotations

from ..data_store import Doc


def doc_to_context(doc: Doc, include_body: bool = True) -> str:
    """Render a doc's frontmatter + body as a plain-text block."""
    lines: list[str] = []
    for key, value in doc.meta.items():
        lines.append(f"{key}: {value}")
    text = "\n".join(lines)
    if include_body and doc.body.strip():
        text += "\n\n" + doc.body.strip()
    return text


def parse_days_open(stage: dict) -> int:
    for key in ("days_open", "daysOpen", "age_days"):
        if key in stage:
            try:
                return int(stage[key])
            except (TypeError, ValueError):
                return 0
    return 0

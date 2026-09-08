"""Data store.

Loads the Markdown corpus under `data/` (employees, exit cases, policies,
process docs, KT docs, interviews) into memory. Each file is parsed as YAML
frontmatter + body via python-frontmatter. The store is intentionally simple —
directory scans, cached at first use — since the dataset is small demo data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import frontmatter

from .config import DATA_DIR


@dataclass
class Doc:
    """A parsed Markdown document."""

    path: Path
    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @property
    def slug(self) -> str:
        return self.path.stem

    @property
    def title(self) -> str:
        return str(
            self.meta.get("name")
            or self.meta.get("title")
            or self.meta.get("id")
            or self.slug
        )


def _load_dir(subdir: str) -> list[Doc]:
    folder = DATA_DIR / subdir
    if not folder.exists():
        return []
    docs: list[Doc] = []
    for path in sorted(folder.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        try:
            post = frontmatter.load(path)
            docs.append(Doc(path=path, meta=dict(post.metadata), body=post.content))
        except Exception:  # noqa: BLE001 - skip a malformed file, keep the rest
            docs.append(Doc(path=path, meta={}, body=path.read_text(encoding="utf-8")))
    return docs


class DataStore:
    @lru_cache(maxsize=1)  # noqa: B019 - single long-lived instance
    def employees(self) -> list[Doc]:
        return _load_dir("employees")

    @lru_cache(maxsize=1)  # noqa: B019
    def exit_cases(self) -> list[Doc]:
        return _load_dir("exit-cases")

    @lru_cache(maxsize=1)  # noqa: B019
    def policies(self) -> list[Doc]:
        return _load_dir("policies")

    @lru_cache(maxsize=1)  # noqa: B019
    def process_docs(self) -> list[Doc]:
        return _load_dir("process-docs")

    @lru_cache(maxsize=1)  # noqa: B019
    def kt_docs(self) -> list[Doc]:
        return _load_dir("kt-docs")

    @lru_cache(maxsize=1)  # noqa: B019
    def interviews(self) -> list[Doc]:
        return _load_dir("interviews")

    # -- lookups -----------------------------------------------------------
    def employee(self, emp_id: str) -> Doc | None:
        return self._by_id(self.employees(), emp_id, ("id",))

    def exit_case(self, case_id: str) -> Doc | None:
        return self._by_id(self.exit_cases(), case_id, ("case_id", "id"))

    def kt_doc(self, kt_id: str) -> Doc | None:
        return self._by_id(self.kt_docs(), kt_id, ("kt_id", "id"))

    def interview(self, interview_id: str) -> Doc | None:
        return self._by_id(self.interviews(), interview_id, ("interview_id", "id"))

    @staticmethod
    def _by_id(docs: list[Doc], value: str, keys: tuple[str, ...]) -> Doc | None:
        value = str(value).strip().lower()
        for doc in docs:
            if doc.slug.lower() == value:
                return doc
            for key in keys:
                if str(doc.meta.get(key, "")).strip().lower() == value:
                    return doc
        return None


store = DataStore()

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

ProgressStage = Literal[
    "discovered", "document", "page", "building", "committing", "complete"
]


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    stage: ProgressStage
    document_index: int
    document_count: int
    filename: str = ""
    page_number: int = 0
    page_count: int = 0


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass(frozen=True, slots=True)
class PageResult:
    page_number: int
    text: str
    nul_count: int
    is_empty: bool


@dataclass(frozen=True, slots=True)
class DocumentResult:
    index: int
    filename: str
    page_count: int
    pages: tuple[PageResult, ...]

    @property
    def character_count(self) -> int:
        return sum(len(page.text) for page in self.pages)

    @property
    def warning_count(self) -> int:
        return sum(bool(page.is_empty) + bool(page.nul_count) for page in self.pages)


@dataclass(frozen=True, slots=True)
class ExtractionWarning:
    code: str
    message: str
    filename: str | None = None
    page_number: int | None = None
    count: int | None = None


@dataclass(frozen=True, slots=True)
class BuiltMarkdown:
    content: str
    warnings: tuple[ExtractionWarning, ...]
    marker_prefix: str
    character_count: int
    byte_count: int


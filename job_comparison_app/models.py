from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class RunLevel(str, Enum):
    SUCCESS = "success"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    path: Path
    exists: bool
    size: int | None = None
    modified_ns: int | None = None
    sha256: str = ""


@dataclass(frozen=True, slots=True)
class PdfInventoryItem:
    name: str
    fingerprint: FileFingerprint

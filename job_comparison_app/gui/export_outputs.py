from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..errors import InputValidationError
from .pdf_outputs import OutputConflictChoice

OUTPUT_FILENAME = "PDF全文_統合テキスト化.md"


@dataclass(frozen=True, slots=True)
class ExportOutputConflict:
    base_path: Path
    existing_path: Path | None
    suggested_path: Path | None


def resolve_export_output(input_dir: Path) -> Path:
    return (input_dir.resolve(strict=False) / "output" / OUTPUT_FILENAME).resolve(strict=False)


def resolve_numbered_export_output(base_path: Path) -> Path:
    index = 1
    while True:
        candidate = base_path.with_name(f"{base_path.stem}({index}){base_path.suffix}")
        if not candidate.exists():
            return candidate.resolve(strict=False)
        index += 1


def inspect_export_output(base_path: Path) -> ExportOutputConflict:
    path = base_path.resolve(strict=False)
    if path.exists() and not path.is_file():
        raise InputValidationError(f"Markdown出力先が通常ファイルではありません: {path}")
    return ExportOutputConflict(
        path,
        path if path.exists() else None,
        resolve_numbered_export_output(path) if path.exists() else None,
    )


__all__ = [
    "ExportOutputConflict", "OutputConflictChoice", "inspect_export_output",
    "resolve_export_output", "resolve_numbered_export_output",
]

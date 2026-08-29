"""GUI-only CSV output path selection for the PDF organization tab."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..errors import InputValidationError


MAIN_CSV_NAME = "求人情報.csv"
REPORT_CSV_NAME = "処理レポート.csv"


class PdfCsvConflictChoice(Enum):
    REPLACE = "replace"
    SAVE_AS_NEW = "save_as_new"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class PdfCsvOutputPaths:
    output_dir: Path
    output_csv: Path
    report_csv: Path


@dataclass(frozen=True, slots=True)
class PdfCsvConflict:
    base_paths: PdfCsvOutputPaths
    existing_paths: tuple[Path, ...]
    suggested_paths: PdfCsvOutputPaths | None


def _is_reparse_like(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _validate_output_directory(path: Path) -> None:
    if _is_reparse_like(path):
        raise InputValidationError(f"CSV出力フォルダーにリンクは指定できません: {path}")
    if path.exists() and not path.is_dir():
        raise InputValidationError(f"CSV出力フォルダーが通常のフォルダーではありません: {path}")


def _validate_output_target(path: Path) -> None:
    if _is_reparse_like(path):
        raise InputValidationError(f"CSV出力先にリンクは指定できません: {path}")
    if path.exists() and not path.is_file():
        raise InputValidationError(f"CSV出力先が通常ファイルではありません: {path}")


def resolve_base_csv_paths(
    input_dir: Path,
    explicit_output_dir: Path | None,
) -> PdfCsvOutputPaths:
    resolved_input = Path(input_dir).resolve(strict=False)
    output_dir = (
        Path(explicit_output_dir).resolve(strict=False)
        if explicit_output_dir is not None
        else (resolved_input / "output").resolve(strict=False)
    )
    _validate_output_directory(output_dir)
    output_csv = (output_dir / MAIN_CSV_NAME).resolve(strict=False)
    report_csv = (output_dir / REPORT_CSV_NAME).resolve(strict=False)
    _validate_output_target(output_csv)
    _validate_output_target(report_csv)
    return PdfCsvOutputPaths(output_dir, output_csv, report_csv)


def inspect_csv_conflict(paths: PdfCsvOutputPaths) -> PdfCsvConflict:
    _validate_output_directory(paths.output_dir)
    _validate_output_target(paths.output_csv)
    _validate_output_target(paths.report_csv)
    existing = tuple(path for path in (paths.output_csv, paths.report_csv) if path.exists())
    suggested = resolve_numbered_csv_paths(paths) if existing else None
    return PdfCsvConflict(paths, existing, suggested)


def resolve_numbered_csv_paths(base_paths: PdfCsvOutputPaths) -> PdfCsvOutputPaths:
    _validate_output_directory(base_paths.output_dir)
    index = 1
    while True:
        output_csv = (base_paths.output_dir / f"求人情報({index}).csv").resolve(strict=False)
        report_csv = (base_paths.output_dir / f"処理レポート({index}).csv").resolve(strict=False)
        _validate_output_target(output_csv)
        _validate_output_target(report_csv)
        if not output_csv.exists() and not report_csv.exists():
            return PdfCsvOutputPaths(base_paths.output_dir, output_csv, report_csv)
        index += 1


__all__ = [
    "PdfCsvConflict",
    "PdfCsvConflictChoice",
    "PdfCsvOutputPaths",
    "inspect_csv_conflict",
    "resolve_base_csv_paths",
    "resolve_numbered_csv_paths",
]

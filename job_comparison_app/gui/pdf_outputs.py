"""GUI-only JSON output and legacy CSV selection for PDF organization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..errors import InputValidationError


JOB_INFO_JSON_NAME = "求人情報.json"
REPORT_JSON_NAME = "処理レポート.json"
LEGACY_MAIN_CSV_NAME = "求人情報.csv"


class OutputConflictChoice(Enum):
    REPLACE = "replace"
    SAVE_AS_NEW = "save_as_new"
    CANCEL = "cancel"


class LegacyCsvMigrationChoice(Enum):
    IMPORT = "import"
    START_NEW = "start_new"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class OrganizationOutputPaths:
    output_dir: Path
    job_info_json: Path
    report_json: Path


@dataclass(frozen=True, slots=True)
class OrganizationOutputConflict:
    base_paths: OrganizationOutputPaths
    existing_paths: tuple[Path, ...]
    suggested_paths: OrganizationOutputPaths | None


def _is_reparse_like(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _validate_output_directory(path: Path) -> None:
    if _is_reparse_like(path):
        raise InputValidationError(f"JSON出力フォルダーにリンクは指定できません: {path}")
    if path.exists() and not path.is_dir():
        raise InputValidationError(f"JSON出力フォルダーが通常のフォルダーではありません: {path}")


def _validate_output_target(path: Path) -> None:
    if _is_reparse_like(path):
        raise InputValidationError(f"JSON出力先にリンクは指定できません: {path}")
    if path.exists() and not path.is_file():
        raise InputValidationError(f"JSON出力先が通常ファイルではありません: {path}")


def resolve_base_output_paths(
    input_dir: Path,
    explicit_output_dir: Path | None,
) -> OrganizationOutputPaths:
    resolved_input = Path(input_dir).resolve(strict=False)
    output_dir = (
        Path(explicit_output_dir).resolve(strict=False)
        if explicit_output_dir is not None
        else (resolved_input / "output").resolve(strict=False)
    )
    _validate_output_directory(output_dir)
    job_info_json = (output_dir / JOB_INFO_JSON_NAME).resolve(strict=False)
    report_json = (output_dir / REPORT_JSON_NAME).resolve(strict=False)
    _validate_output_target(job_info_json)
    _validate_output_target(report_json)
    return OrganizationOutputPaths(output_dir, job_info_json, report_json)


def inspect_output_conflict(paths: OrganizationOutputPaths) -> OrganizationOutputConflict:
    _validate_output_directory(paths.output_dir)
    _validate_output_target(paths.job_info_json)
    _validate_output_target(paths.report_json)
    existing = tuple(
        path for path in (paths.job_info_json, paths.report_json) if path.exists()
    )
    suggested = resolve_numbered_output_paths(paths) if existing else None
    return OrganizationOutputConflict(paths, existing, suggested)


def resolve_numbered_output_paths(base_paths: OrganizationOutputPaths) -> OrganizationOutputPaths:
    _validate_output_directory(base_paths.output_dir)
    index = 1
    while True:
        job_info_json = (
            base_paths.output_dir / f"求人情報({index}).json"
        ).resolve(strict=False)
        report_json = (
            base_paths.output_dir / f"処理レポート({index}).json"
        ).resolve(strict=False)
        _validate_output_target(job_info_json)
        _validate_output_target(report_json)
        if not job_info_json.exists() and not report_json.exists():
            return OrganizationOutputPaths(
                base_paths.output_dir, job_info_json, report_json
            )
        index += 1


def discover_legacy_main_csv_candidates(
    input_dir: Path,
    output_dir: Path,
) -> tuple[Path, ...]:
    resolved_input = input_dir.resolve(strict=False)
    resolved_output = output_dir.resolve(strict=False)
    exact = [
        (resolved_output / LEGACY_MAIN_CSV_NAME).resolve(strict=False),
        (resolved_input / LEGACY_MAIN_CSV_NAME).resolve(strict=False),
    ]
    seen: set[Path] = set()
    for candidate in exact:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file() and not _is_reparse_like(candidate):
            return (candidate,)

    candidates: list[Path] = []
    for directory in (resolved_output, resolved_input):
        if not directory.is_dir():
            continue
        try:
            matches = sorted(
                directory.glob("求人情報*.csv"), key=lambda path: path.name.casefold()
            )
        except OSError:
            continue
        for candidate in matches:
            resolved = candidate.resolve(strict=False)
            if resolved in seen:
                continue
            seen.add(resolved)
            if candidate.is_file() and not _is_reparse_like(candidate):
                candidates.append(resolved)
    return tuple(candidates)


def validate_legacy_csv(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    if resolved.suffix.casefold() != ".csv":
        raise InputValidationError("移行元にはCSVファイルを指定してください。")
    if _is_reparse_like(resolved) or not resolved.is_file():
        raise InputValidationError(f"移行元の求人情報CSVが通常ファイルではありません: {resolved}")
    return resolved


__all__ = [
    "LegacyCsvMigrationChoice",
    "OrganizationOutputConflict",
    "OrganizationOutputPaths",
    "OutputConflictChoice",
    "discover_legacy_main_csv_candidates",
    "inspect_output_conflict",
    "resolve_base_output_paths",
    "resolve_numbered_output_paths",
    "validate_legacy_csv",
]

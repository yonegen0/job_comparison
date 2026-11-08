"""Configuration-free local job workflows shared by GUI and internal CLI."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .app_paths import AppPaths
from .constants import MAX_CURRENT_JOBS
from .csv_exporter import CsvExporter
from .errors import CsvExportError, JobValidationError, JobLimitError
from .models import Job, JobSource, RawJob
from .normalizer import Normalizer
from .repository import Repository
from .validator import Validator


class ImportMode(str, Enum):
    REPLACE = "replace"
    MERGE = "merge"

    @property
    def label(self) -> str:
        return "置換" if self is ImportMode.REPLACE else "追加・更新"


@dataclass(frozen=True, slots=True)
class ImportSaveResult:
    input_count: int
    current_count: int


class JobWorkflow:
    """Domain orchestration without UI, argument parsing, or feature flags."""

    def __init__(self, paths: AppPaths) -> None:
        self.validator = Validator()
        self.normalizer = Normalizer()
        self.repository = Repository(
            work_dir=paths.work_dir,
            backup_dir=paths.backup_dir,
            validator=self.validator,
        )

    def normalize_all(self, raw_jobs: Iterable[RawJob]) -> list[Job]:
        jobs: list[Job] = []
        for raw in raw_jobs:
            job = self.normalizer.normalize(raw)
            result = self.validator.validate(job)
            if not result.is_valid:
                raise JobValidationError(
                    f"求人データが不正です: {','.join(result.errors)}"
                )
            jobs.append(job)
        return jobs

    def save_import(
        self,
        provider: JobSource,
        jobs: Iterable[Job],
        mode: ImportMode,
    ) -> ImportSaveResult:
        if not isinstance(mode, ImportMode):
            try:
                mode = ImportMode(mode)
            except (TypeError, ValueError) as error:
                raise ValueError("mode must be replace or merge") from error
        incoming = list(jobs)
        self.validate_import(provider, incoming, mode)
        current = self.repository.load_all(provider)
        if not incoming:
            return ImportSaveResult(0, len(current))
        self.repository.begin_run(provider)
        saved = (
            self.repository.replace_many(provider, incoming)
            if mode is ImportMode.REPLACE
            else self.repository.upsert_many(provider, incoming)
        )
        return ImportSaveResult(len(incoming), len(saved))

    def validate_import(
        self, provider: JobSource, incoming: list[Job], mode: ImportMode,
    ) -> None:
        """Validate the complete import before preparing output or backups."""
        seen: set[str] = set()
        for job in incoming:
            result = self.validator.validate(job)
            if not result.is_valid or job.source is not provider:
                raise JobValidationError("求人データまたは媒体が不正です")
            if job.internal_job_id in seen:
                raise JobValidationError("取込対象に重複する求人IDがあります")
            seen.add(job.internal_job_id)

        current = self.repository.load_all(provider)
        if not incoming:
            return
        projected = (
            len(incoming)
            if mode is ImportMode.REPLACE
            else len({job.internal_job_id for job in current} | seen)
        )
        if projected > MAX_CURRENT_JOBS:
            raise JobLimitError(projected, MAX_CURRENT_JOBS)

    def export_current(
        self, provider: JobSource, *, output_dir: Path
    ) -> tuple[Path | None, int]:
        jobs = self.repository.load_all(provider)
        if len(jobs) > MAX_CURRENT_JOBS:
            raise JobValidationError(
                f"現行求人が上限{MAX_CURRENT_JOBS}件を超えています"
            )
        if not jobs:
            return None, 0
        exporter = CsvExporter(output_dir=output_dir, validator=self.validator)
        return exporter.export(provider, jobs), len(jobs)

    def restore_and_sync_csv(
        self,
        provider: JobSource,
        *,
        output_dir: Path,
    ) -> tuple[Path | None, int, bool, tuple[str, ...]]:
        previous = self.repository.load_previous(provider)
        if len(previous) > MAX_CURRENT_JOBS:
            raise JobValidationError(
                f"直前状態が上限{MAX_CURRENT_JOBS}件を超えています"
            )
        restored = self.repository.restore_previous(provider)
        destination = output_dir / f"{provider.value.lower()}_jobs.csv"
        if not restored:
            existed = destination.exists()
            try:
                destination.unlink(missing_ok=True)
            except OSError as error:
                raise CsvExportError("復元後のCSVを削除できませんでした") from error
            return None, 0, existed, ()
        try:
            exporter = CsvExporter(output_dir=output_dir, validator=self.validator)
            path = exporter.export(provider, restored)
            return path, len(restored), False, ()
        except CsvExportError:
            removed = False
            try:
                removed = destination.exists()
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            return (
                None,
                len(restored),
                removed,
                ("求人は復元されましたがCSVを更新できませんでした。CSVを再出力してください。",),
            )


__all__ = ["ImportMode", "ImportSaveResult", "JobWorkflow"]

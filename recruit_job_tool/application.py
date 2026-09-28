"""Facade used by both the desktop UI and the internal command adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from .analysis_prompt.models import AnalysisGenerationResult, PromptGenerationResult
from .analysis_prompt.service import generate_analysis_files, generate_analysis_prompt
from .candidate_profile import CandidateProfile
from .app_paths import AppPaths
from .constants import MAX_CURRENT_JOBS
from .errors import (
    CsvExportError,
    OutputLocationError,
    RecruitJobToolError,
    RunLogError,
)
from .models import JobSource
from .output_location import OutputLocationStore, StagedOutputLocation
from .providers.careerbank import CareerBankProvider, CareerBankSearchHtmlImporter
from .providers.errors import ProviderError
from .providers.jobins import JobinsProvider, JobinsSearchHtmlImporter
from .run_logging import RunLogger
from .workflow import ImportMode, JobWorkflow


_JST = timezone(timedelta(hours=9), name="JST")


class ProviderHealth(str, Enum):
    READY = "READY"
    REPLACE_REQUIRED = "REPLACE_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    provider: JobSource
    health: ProviderHealth
    current_count: int | None
    csv_exists: bool
    restore_available: bool
    recovered_from_previous: bool
    output_configured: bool
    output_error: bool


@dataclass(frozen=True, slots=True)
class ImportResult:
    provider: JobSource
    mode: ImportMode
    input_count: int
    current_count: int
    csv_path: Path | None
    changed: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExportResult:
    provider: JobSource
    count: int
    path: Path | None
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RestoreResult:
    provider: JobSource
    count: int
    path: Path | None
    csv_removed: bool
    warnings: tuple[str, ...]


class RecruitJobApplication:
    def __init__(
        self,
        paths: AppPaths,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.paths = paths
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._workflow = JobWorkflow(paths)
        self._output_locations = OutputLocationStore(paths.state_dir)

    def get_provider_status(self, *, provider: JobSource) -> ProviderStatus:
        output_directory: Path | None = None
        output_error = False
        try:
            output_directory = self._output_locations.load(provider)
        except OutputLocationError:
            output_error = True
        csv_exists = False
        if output_directory is not None:
            csv_path = output_directory / f"{provider.value.lower()}_jobs.csv"
            try:
                csv_exists = csv_path.is_file() and not csv_path.is_symlink()
            except OSError:
                output_error = True
        try:
            loaded = self._workflow.repository.load_all_with_status(provider)
            count = len(loaded.jobs)
            health = (
                ProviderHealth.REPLACE_REQUIRED
                if count > MAX_CURRENT_JOBS
                else ProviderHealth.READY
            )
            restore_available = False
            if self._workflow.repository.previous_exists(provider):
                try:
                    restore_available = (
                        len(self._workflow.repository.load_previous(provider))
                        <= MAX_CURRENT_JOBS
                    )
                except RecruitJobToolError:
                    restore_available = False
            return ProviderStatus(
                provider,
                health,
                count,
                csv_exists,
                restore_available,
                loaded.recovered_from_previous,
                output_directory is not None,
                output_error,
            )
        except RecruitJobToolError:
            return ProviderStatus(
                provider,
                ProviderHealth.UNAVAILABLE,
                None,
                csv_exists,
                False,
                False,
                output_directory is not None,
                output_error,
            )

    def import_saved_html(
        self,
        *,
        provider: JobSource,
        input_path: Path,
        mode: ImportMode,
    ) -> ImportResult:
        logger = self._start_log(provider)
        count = 0
        staged: StagedOutputLocation | None = None
        try:
            if provider is JobSource.JOBINS:
                raw = JobinsProvider(importer=JobinsSearchHtmlImporter()).load(
                    input_path, limit=MAX_CURRENT_JOBS
                )
            else:
                raw = CareerBankProvider(
                    importer=CareerBankSearchHtmlImporter()
                ).load(input_path, limit=MAX_CURRENT_JOBS)
            jobs = self._workflow.normalize_all(raw)
            if not jobs:
                saved = self._workflow.save_import(provider, jobs, mode)
                warnings = self._finish_log(logger, 0)
                return ImportResult(
                    provider, mode, 0, saved.current_count, None, False, warnings
                )
            output_directory, update_location = self._import_output_directory(
                provider=provider,
                input_path=input_path,
                mode=mode,
            )
            output_directory = self._output_locations.prepare_directory(
                output_directory
            )
            if update_location:
                staged = self._output_locations.stage(provider, output_directory)
            try:
                saved = self._workflow.save_import(provider, jobs, mode)
            except Exception:
                if staged is not None:
                    staged.discard()
                raise
            count = saved.input_count
            warnings: list[str] = []
            if staged is not None:
                try:
                    staged.commit()
                except OutputLocationError:
                    staged.discard()
                    warnings.append(
                        "求人は保存されましたが、出力先設定を更新できませんでした。"
                        "置換取り込みをもう一度実行してください。"
                    )
                    warnings.extend(self._finish_log(logger, count))
                    return ImportResult(
                        provider,
                        mode,
                        saved.input_count,
                        saved.current_count,
                        None,
                        True,
                        tuple(warnings),
                    )
            csv_path: Path | None = None
            try:
                csv_path, _ = self._workflow.export_current(
                    provider, output_dir=output_directory
                )
            except CsvExportError:
                warnings.append(
                    "求人は保存されましたがCSVを更新できませんでした。CSVを再出力してください。"
                )
            warnings.extend(self._finish_log(logger, count))
            return ImportResult(
                provider,
                mode,
                saved.input_count,
                saved.current_count,
                csv_path,
                True,
                tuple(warnings),
            )
        except (RecruitJobToolError, ProviderError) as error:
            self._record_failure(logger, count, _error_code(error))
            raise
        except Exception:
            self._record_failure(logger, count, "APP001")
            raise

    def export_csv(self, *, provider: JobSource) -> ExportResult:
        logger = self._start_log(provider)
        try:
            output_directory = self.ensure_output_directory(provider=provider)
            path, count = self._workflow.export_current(
                provider, output_dir=output_directory
            )
            warnings = self._finish_log(logger, count)
            return ExportResult(provider, count, path, warnings)
        except RecruitJobToolError as error:
            self._record_failure(logger, 0, error.error_code)
            raise
        except Exception:
            self._record_failure(logger, 0, "APP001")
            raise

    def restore_previous(self, *, provider: JobSource) -> RestoreResult:
        logger = self._start_log(provider)
        try:
            output_directory = self.ensure_output_directory(provider=provider)
            path, count, removed, workflow_warnings = (
                self._workflow.restore_and_sync_csv(
                    provider, output_dir=output_directory
                )
            )
            warnings = (*workflow_warnings, *self._finish_log(logger, count))
            return RestoreResult(provider, count, path, removed, warnings)
        except RecruitJobToolError as error:
            self._record_failure(logger, 0, error.error_code)
            raise
        except Exception:
            self._record_failure(logger, 0, "APP001")
            raise

    def create_analysis_pair(
        self,
        *,
        provider: JobSource,
        candidate_profile: CandidateProfile,
        input_path: Path | None = None,
        markdown_path: Path | None = None,
        json_path: Path | None = None,
    ) -> PromptGenerationResult:
        output_directory: Path | None = None
        if input_path is None:
            output_directory = self.ensure_output_directory(provider=provider)
            input_path, count = self._workflow.export_current(
                provider, output_dir=output_directory
            )
            if input_path is None or count == 0:
                from .errors import PromptInputError

                raise PromptInputError("分析対象の求人がありません")
        if markdown_path is None and json_path is None:
            if output_directory is None:
                output_directory = self.ensure_output_directory(provider=provider)
        generated_at = self._clock()
        markdown, json_output = _analysis_output_paths(
            output_directory,
            provider,
            markdown_path,
            json_path,
            generated_at,
        )
        return generate_analysis_prompt(
            input_path=input_path,
            output_path=markdown,
            json_output_path=json_output,
            provider=provider,
            candidate_profile=candidate_profile,
            generated_at=generated_at,
        )

    def create_analysis_files(
        self,
        *,
        provider: JobSource,
        input_path: Path | None = None,
        json_path: Path | None = None,
        guide_path: Path | None = None,
        include_guide: bool = True,
        candidate_profile: CandidateProfile | None = None,
    ) -> AnalysisGenerationResult:
        """一般比較、またはMarkdown条件付き比較の成果物を生成する。"""

        json_was_explicit = json_path is not None
        if candidate_profile is not None and not include_guide:
            from .errors import PromptOutputError

            raise PromptOutputError("転職者条件付き比較ではMarkdownガイドが必要です")
        if not include_guide and guide_path is not None:
            from .errors import PromptOutputError

            raise PromptOutputError(
                "ガイドを生成しない場合はMarkdown出力先を指定できません"
            )
        output_directory: Path | None = None
        if input_path is None:
            output_directory = self.ensure_output_directory(provider=provider)
            input_path, count = self._workflow.export_current(
                provider, output_dir=output_directory
            )
            if input_path is None or count == 0:
                from .errors import PromptInputError

                raise PromptInputError("分析対象の求人がありません")
        if (
            json_path is None
            and guide_path is None
            and output_directory is None
        ):
            output_directory = self.ensure_output_directory(provider=provider)

        generated_at = self._clock()
        if candidate_profile is not None:
            if json_path is not None and guide_path is None:
                guide_path = Path(json_path).with_suffix(".md")
            elif json_path is None and guide_path is not None:
                json_path = Path(guide_path).with_suffix(".json")
            elif json_path is None and guide_path is None:
                assert output_directory is not None
                json_path, guide_path = _next_candidate_analysis_paths(
                    output_directory,
                    provider,
                    generated_at,
                )
        else:
            if json_path is None and guide_path is not None:
                json_path = Path(guide_path).with_suffix(".json")
            elif json_path is None:
                assert output_directory is not None
                json_path = _next_analysis_path(
                    output_directory,
                    provider,
                    "jobs",
                    ".json",
                    generated_at,
                )
            if include_guide and guide_path is None:
                if json_was_explicit:
                    guide_path = Path(json_path).with_suffix(".md")
                else:
                    guide_directory = output_directory or Path(json_path).parent
                    guide_path = _next_analysis_path(
                        guide_directory,
                        provider,
                        "guide",
                        ".md",
                        generated_at,
                    )
        if json_path is None:
            from .errors import PromptOutputError

            raise PromptOutputError("求人JSON出力先を解決できません")
        return generate_analysis_files(
            input_path=Path(input_path),
            json_output_path=Path(json_path),
            guide_output_path=Path(guide_path) if guide_path is not None else None,
            include_guide=include_guide,
            candidate_profile=candidate_profile,
            provider=provider,
            generated_at=generated_at,
        )

    def get_output_directory(self, *, provider: JobSource) -> Path:
        directory = self._output_locations.load(provider)
        if directory is None:
            raise OutputLocationError(
                "出力先が未設定です。HTMLを取り込んでください"
            )
        return directory

    def ensure_output_directory(self, *, provider: JobSource) -> Path:
        return self._output_locations.prepare_directory(
            self.get_output_directory(provider=provider)
        )

    def _import_output_directory(
        self,
        *,
        provider: JobSource,
        input_path: Path,
        mode: ImportMode,
    ) -> tuple[Path, bool]:
        if mode is ImportMode.REPLACE:
            return self._output_locations.candidate_from_html(input_path), True
        existing = self._output_locations.load(provider)
        if existing is not None:
            return existing, False
        return self._output_locations.candidate_from_html(input_path), True

    def _start_log(self, provider: JobSource) -> RunLogger:
        logger = RunLogger(log_dir=self.paths.log_dir, clock=self._clock)
        logger.start(provider)
        return logger

    @staticmethod
    def _finish_log(logger: RunLogger, count: int) -> tuple[str, ...]:
        try:
            logger.record(count)
        except RunLogError:
            return ("処理は完了しましたが実行ログを記録できませんでした（LOG001）。",)
        return ()

    @staticmethod
    def _record_failure(logger: RunLogger, count: int, code: str) -> None:
        try:
            logger.record(count, code)
        except RunLogError:
            pass


def _error_code(error: RecruitJobToolError | ProviderError) -> str:
    return error.error_code if isinstance(error, RecruitJobToolError) else error.code


def _analysis_output_paths(
    output_dir: Path | None,
    provider: JobSource,
    markdown_path: Path | None,
    json_path: Path | None,
    generated_at: datetime,
) -> tuple[Path, Path]:
    if markdown_path is not None and json_path is None:
        return markdown_path, markdown_path.with_suffix(".json")
    if markdown_path is None and json_path is not None:
        return json_path.with_suffix(".md"), json_path
    if markdown_path is not None and json_path is not None:
        return markdown_path, json_path
    if output_dir is None:
        raise OutputLocationError("出力先が未設定です。HTMLを取り込んでください")
    timestamp = generated_at.astimezone(_JST).strftime("%Y%m%d_%H%M%S")
    stem = f"{provider.value.lower()}_analysis_prompt_{timestamp}"
    sequence = 0
    while True:
        suffix = "" if sequence == 0 else f"_{sequence:02d}"
        base = output_dir / f"{stem}{suffix}"
        md = base.with_suffix(".md")
        js = base.with_suffix(".json")
        if not (md.exists() or md.is_symlink() or js.exists() or js.is_symlink()):
            return md, js
        sequence += 1


def _next_analysis_path(
    output_dir: Path,
    provider: JobSource,
    kind: str,
    suffix: str,
    generated_at: datetime,
) -> Path:
    timestamp = generated_at.astimezone(_JST).strftime("%Y%m%d_%H%M%S")
    stem = f"{provider.value.lower()}_analysis_{kind}_{timestamp}"
    sequence = 0
    while True:
        numbered = "" if sequence == 0 else f"_{sequence:02d}"
        candidate = output_dir / f"{stem}{numbered}{suffix}"
        if not (candidate.exists() or candidate.is_symlink()):
            return candidate
        sequence += 1


def _next_candidate_analysis_paths(
    output_dir: Path,
    provider: JobSource,
    generated_at: datetime,
) -> tuple[Path, Path]:
    """JSONとガイドに共通する未使用timestamp・連番を返す。"""

    timestamp = generated_at.astimezone(_JST).strftime("%Y%m%d_%H%M%S")
    provider_name = provider.value.lower()
    sequence = 0
    while True:
        numbered = "" if sequence == 0 else f"_{sequence:02d}"
        json_path = output_dir / (
            f"{provider_name}_analysis_jobs_{timestamp}{numbered}.json"
        )
        guide_path = output_dir / (
            f"{provider_name}_analysis_guide_{timestamp}{numbered}.md"
        )
        occupied = any(
            path.exists() or path.is_symlink() for path in (json_path, guide_path)
        )
        if not occupied:
            return json_path, guide_path
        sequence += 1


__all__ = [
    "ExportResult",
    "AnalysisGenerationResult",
    "ImportMode",
    "ImportResult",
    "ProviderHealth",
    "ProviderStatus",
    "RecruitJobApplication",
    "RestoreResult",
]

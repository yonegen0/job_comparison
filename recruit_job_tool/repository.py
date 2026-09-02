"""媒体別JSONLの検証、atomic保存、1世代復元。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from .errors import JobValidationError, RepositoryCorruptionError, RepositoryError
from .models import Job, JobSource
from .validator import Validator


@dataclass(frozen=True, slots=True)
class LoadResult:
    jobs: tuple[Job, ...]
    recovered_from_previous: bool


class Repository:
    """一人・一プロセス利用を前提とする媒体別スナップショット保存。"""

    def __init__(
        self,
        work_dir: Path | str = Path("data/work"),
        backup_dir: Path | str = Path("data/backup"),
        validator: Validator | None = None,
    ) -> None:
        self._work_dir = Path(work_dir)
        self._backup_dir = Path(backup_dir)
        self._validator = validator or Validator()

    def begin_run(self, provider: JobSource | str) -> list[Job]:
        """現行データを検証し、直前実行前スナップショットを1世代保存する。"""

        source = _coerce_source(provider)
        current = self.load_all(source)
        self._atomic_write(self._backup_path(source), current, source)
        return current

    def load_all(self, provider: JobSource | str) -> list[Job]:
        """現行データを読む。破損時は検証済みの前世代から自動復旧する。"""

        source = _coerce_source(provider)
        current_path = self._work_path(source)
        if not current_path.exists():
            return []
        try:
            return self._read_jobs(current_path, source)
        except RepositoryCorruptionError as current_error:
            backup_path = self._backup_path(source)
            if not backup_path.exists():
                raise RepositoryCorruptionError(
                    f"{source.value} の現行JSONLが破損し、前世代がありません"
                ) from current_error
            try:
                recovered = self._read_jobs(backup_path, source)
            except RepositoryCorruptionError as backup_error:
                raise RepositoryCorruptionError(
                    f"{source.value} の現行JSONLと前世代がともに破損しています"
                ) from backup_error
            self._atomic_write(current_path, recovered, source)
            return recovered

    def load_all_with_status(self, provider: JobSource | str) -> LoadResult:
        """Load current jobs and report whether backup recovery changed work."""

        source = _coerce_source(provider)
        current_path = self._work_path(source)
        if not current_path.exists():
            return LoadResult((), False)
        try:
            return LoadResult(tuple(self._read_jobs(current_path, source)), False)
        except RepositoryCorruptionError as current_error:
            backup_path = self._backup_path(source)
            if not backup_path.exists():
                raise RepositoryCorruptionError(
                    f"{source.value} の現行JSONLが破損し、前世代がありません"
                ) from current_error
            try:
                recovered = self._read_jobs(backup_path, source)
            except RepositoryCorruptionError as backup_error:
                raise RepositoryCorruptionError(
                    f"{source.value} の現行JSONLと前世代がともに破損しています"
                ) from backup_error
            self._atomic_write(current_path, recovered, source)
            return LoadResult(tuple(recovered), True)

    def load_previous(self, provider: JobSource | str) -> list[Job]:
        """Read and validate the previous snapshot without changing any file."""

        source = _coerce_source(provider)
        path = self._backup_path(source)
        if not path.exists():
            raise RepositoryError(f"{source.value} の復元可能な前世代がありません")
        return self._read_jobs(path, source)

    def read_snapshot(
        self,
        path: Path | str,
        provider: JobSource | str,
    ) -> list[Job]:
        """Strict read-only JSONL validation for migration and diagnostics."""

        return self._read_jobs(Path(path), _coerce_source(provider))

    def previous_exists(self, provider: JobSource | str) -> bool:
        return self._backup_path(_coerce_source(provider)).is_file()

    def current_exists(self, provider: JobSource | str) -> bool:
        return self._work_path(_coerce_source(provider)).is_file()

    def upsert_many(
        self, provider: JobSource | str, jobs: Iterable[Job]
    ) -> list[Job]:
        """同一媒体＋媒体求人IDを最新版で更新し、全体をatomic保存する。"""

        source = _coerce_source(provider)
        incoming = list(jobs)
        for job in incoming:
            self._validate_for_source(job, source)

        existing = self.load_all(source)
        merged = {job.internal_job_id: job for job in existing}
        for job in incoming:
            merged[job.internal_job_id] = job

        result = list(merged.values())
        self._atomic_write(self._work_path(source), result, source)
        return result

    def replace_many(
        self, provider: JobSource | str, jobs: Iterable[Job]
    ) -> list[Job]:
        """Validate and atomically replace one provider's current snapshot."""

        source = _coerce_source(provider)
        incoming = list(jobs)
        seen_ids: set[str] = set()
        for job in incoming:
            self._validate_for_source(job, source)
            if job.internal_job_id in seen_ids:
                raise JobValidationError("取込対象に同じ求人IDが重複しています")
            seen_ids.add(job.internal_job_id)

        self._atomic_write(self._work_path(source), incoming, source)
        return incoming

    def restore_previous(self, provider: JobSource | str) -> list[Job]:
        """媒体単位で直前begin_run前の状態へ戻す。"""

        source = _coerce_source(provider)
        previous = self.load_previous(source)
        self._atomic_write(self._work_path(source), previous, source)
        return previous

    def _validate_for_source(self, job: Job, source: JobSource) -> None:
        result = self._validator.validate(job)
        if not result.is_valid:
            joined = ",".join(result.errors)
            raise JobValidationError(f"求人データが不正です: {joined}")
        if job.source is not source:
            raise JobValidationError("保存先媒体と求人の媒体が一致しません")

    def _read_jobs(self, path: Path, source: JobSource) -> list[Job]:
        jobs: list[Job] = []
        seen_ids: set[str] = set()
        try:
            with path.open("r", encoding="utf-8", newline="") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.endswith("\n"):
                        raise ValueError("JSONL line is not newline-terminated")
                    payload = line[:-1]
                    if payload.endswith("\r") or not payload:
                        raise ValueError("JSONL contains a blank or CRLF line")
                    record = json.loads(
                        payload,
                        object_pairs_hook=_object_without_duplicate_keys,
                        parse_constant=_reject_json_constant,
                    )
                    job = Job.from_record(record)
                    self._validate_for_source(job, source)
                    if job.internal_job_id in seen_ids:
                        raise ValueError("JSONL contains a duplicate job ID")
                    seen_ids.add(job.internal_job_id)
                    jobs.append(job)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            JobValidationError,
        ) as exc:
            line_hint = locals().get("line_number", 0)
            raise RepositoryCorruptionError(
                f"{path.name} のJSONL検証に失敗しました (line={line_hint})"
            ) from exc
        return jobs

    def _atomic_write(
        self, destination: Path, jobs: Iterable[Job], source: JobSource
    ) -> None:
        materialized = list(jobs)
        temporary_path: Path | None = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                for job in materialized:
                    json.dump(
                        job.to_record(),
                        temporary,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    )
                    temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())

            verified = self._read_jobs(temporary_path, source)
            if len(verified) != len(materialized):
                raise RepositoryError("一時JSONLの件数検証に失敗しました")
            os.replace(temporary_path, destination)
            temporary_path = None
        except RepositoryError:
            raise
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            raise RepositoryError(f"{destination.name} を保存できませんでした") from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _work_path(self, source: JobSource) -> Path:
        return self._work_dir / f"{source.value.lower()}.jsonl"

    def _backup_path(self, source: JobSource) -> Path:
        return self._backup_dir / f"{source.value.lower()}.previous.jsonl"


def _coerce_source(provider: JobSource | str) -> JobSource:
    if isinstance(provider, JobSource):
        return provider
    if not isinstance(provider, str):
        raise TypeError("provider must be JobSource or string")
    try:
        return JobSource(provider.strip().upper())
    except ValueError as exc:
        raise ValueError("provider must be JOBINS or CAREERBANK") from exc


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object contains a duplicate key")
        result[key] = value
    return result


def _reject_json_constant(value: str):
    raise ValueError(f"unsupported JSON constant: {value}")

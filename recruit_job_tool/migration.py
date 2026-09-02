"""Read-only legacy discovery and atomic first-run data migration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid

from . import __version__
from .app_paths import AppPaths
from .candidate_profile import (
    CANDIDATE_PROFILE_FORMAT,
    _candidate_profile_from_json,
    _candidate_profile_sha256,
)
from .constants import MAX_CURRENT_JOBS
from .csv_exporter import CsvExporter
from .errors import MigrationError, PromptInputError, RepositoryCorruptionError
from .models import Job, JobSource, format_rfc3339
from .repository import Repository


MARKER_FORMAT = "recruit-job-tool-migration-v1"
MARKER_NAME = "migration-v1.json"
_STAGE_RE = re.compile(r"^RecruitJobTool\.migrating-[0-9a-f]{32}$")
_PAIR_RE = re.compile(
    r"^(?P<provider>jobins|careerbank)_analysis_prompt"
    r"(?:_[0-9]{8}_[0-9]{6}(?:_[0-9]{2})?)?$"
)
_LOWER_SHA = re.compile(r"^[0-9a-f]{64}$")
_JOB_KEYS = {
    "job_id",
    "source_url",
    "company",
    "job_title",
    "industry",
    "location",
    "salary",
    "application_requirements",
    "referral_reward",
}


@dataclass(frozen=True, slots=True)
class SnapshotItem:
    source: Path | None
    jobs: tuple[Job, ...]
    action: str


@dataclass(frozen=True, slots=True)
class AnalysisPair:
    markdown: Path
    json_file: Path
    provider: JobSource
    job_count: int


@dataclass(frozen=True, slots=True)
class SourceInventory:
    root: Path
    current: dict[JobSource, SnapshotItem]
    previous: dict[JobSource, SnapshotItem]
    analysis_pairs: tuple[AnalysisPair, ...]
    input_directory_present: bool
    logs_directory_present: bool


class MigrationManager:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    @property
    def marker_path(self) -> Path:
        return self.paths.state_dir / MARKER_NAME

    def is_ready(self) -> bool:
        if not self.paths.root.exists():
            return False
        if not self.marker_path.is_file():
            return False
        self.read_marker(self.marker_path)
        return True

    def recover_crash_stage(self) -> bool:
        parent = self.paths.root.parent
        complete: list[Path] = []
        incomplete: list[Path] = []
        try:
            candidates = list(parent.glob("RecruitJobTool.migrating-*"))
        except OSError as error:
            raise MigrationError(
                "移行途中フォルダーを確認できませんでした", error_code="MIG007"
            ) from error
        for candidate in candidates:
            if not _STAGE_RE.fullmatch(candidate.name):
                continue
            _reject_reparse(candidate, "MIG007")
            marker = candidate / "state" / MARKER_NAME
            try:
                self.read_marker(marker)
            except MigrationError:
                incomplete.append(candidate)
            else:
                complete.append(candidate)
        if self.paths.root.exists():
            return False
        if len(complete) > 1:
            raise MigrationError(
                "復旧可能な移行途中フォルダーが複数あります", error_code="MIG007"
            )
        for candidate in incomplete:
            self._remove_incomplete_stage(candidate)
        if not complete:
            return False
        try:
            os.replace(complete[0], self.paths.root)
            self.read_marker(self.marker_path)
        except OSError as error:
            raise MigrationError("移行を復旧できませんでした", error_code="MIG006") from error
        return True

    def discover_candidates(
        self,
        *,
        executable_dir: Path,
        cwd: Path,
        documents_dir: Path | None,
    ) -> tuple[Path, ...]:
        raw = [Path(executable_dir), Path(cwd)]
        if documents_dir is not None:
            raw.append(Path(documents_dir) / "job_get")
        candidates: list[Path] = []
        for candidate in raw:
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if any(_same_path(resolved, existing) for existing in candidates):
                continue
            if _same_or_contains(resolved, self.paths.root):
                continue
            if self.recognizes_legacy_root(resolved):
                candidates.append(resolved)
        return tuple(candidates)

    def recognizes_legacy_root(self, root: Path) -> bool:
        try:
            _reject_reparse(root, "MIG002")
            recognized_jsonl = any(
                (root / relative).is_file()
                for relative in (
                    "data/work/jobins.jsonl",
                    "data/work/careerbank.jsonl",
                    "data/backup/jobins.previous.jsonl",
                    "data/backup/careerbank.previous.jsonl",
                )
            )
            old_launcher = (root / "config.toml").is_file() or any(
                (root / name).is_file()
                for name in ("start_jobins.bat", "start_careerbank.bat", "setup.bat")
            )
            recognized_output = any(
                path.is_file()
                for path in (root / "data" / "output").glob("*_analysis_prompt*.md")
            ) if (root / "data" / "output").is_dir() else False
            return recognized_jsonl or (old_launcher and recognized_output)
        except (MigrationError, OSError):
            return False

    def inspect_source(self, root: Path) -> SourceInventory:
        source_root = Path(root).resolve(strict=True)
        if _same_or_contains(source_root, self.paths.root):
            raise MigrationError("移行元と保存先の境界が不正です", error_code="MIG002")
        if not self.recognizes_legacy_root(source_root):
            raise MigrationError("旧版フォルダーとして認識できません", error_code="MIG002")
        current: dict[JobSource, SnapshotItem] = {}
        previous: dict[JobSource, SnapshotItem] = {}
        for provider in JobSource:
            key = provider.value.lower()
            current_path = source_root / "data" / "work" / f"{key}.jsonl"
            previous_path = source_root / "data" / "backup" / f"{key}.previous.jsonl"
            current_item = self._read_optional_snapshot(current_path, provider)
            previous_item = self._read_optional_snapshot(previous_path, provider)
            if current_item is None:
                current_item = SnapshotItem(None, (), "empty")
            elif current_item.action == "invalid":
                if previous_item is None or previous_item.action == "invalid":
                    raise MigrationError(
                        f"{provider.value}の現行データを復旧できません",
                        error_code="MIG003",
                    )
                current_item = SnapshotItem(
                    previous_item.source, previous_item.jobs, "recovered"
                )
            if previous_item is None:
                previous_item = SnapshotItem(None, (), "missing")
            elif previous_item.action == "invalid":
                previous_item = SnapshotItem(None, (), "excluded_invalid")
            current[provider] = current_item
            previous[provider] = previous_item

        output = source_root / "data" / "output"
        if output.is_dir() and any(path.suffix == ".bak" for path in output.iterdir()):
            raise MigrationError(
                "未復旧の分析バックアップがあります", error_code="MIG003"
            )
        pairs = self._analysis_pairs(output)
        return SourceInventory(
            source_root,
            current,
            previous,
            pairs,
            (source_root / "data" / "input").exists(),
            (source_root / "logs").exists(),
        )

    def migrate(self, inventory: SourceInventory, *, source_kind: str) -> None:
        if source_kind not in {"automatic", "selected"}:
            raise ValueError("source_kind must be automatic or selected")
        stage = self._new_stage()
        receipts: list[dict[str, object]] = []
        initial = self._source_fingerprints(inventory)
        try:
            for provider in JobSource:
                key = provider.value.lower()
                current = inventory.current[provider]
                if current.source is not None:
                    destination = stage / "data" / "work" / f"{key}.jsonl"
                    if current.action == "copied":
                        self._copy_verified(current.source, destination)
                    else:
                        Repository(
                            stage / "data" / "work", stage / "data" / "backup"
                        ).replace_many(provider, current.jobs)
                    receipts.append(_receipt(stage, destination, current.action))
                previous = inventory.previous[provider]
                if previous.source is not None:
                    destination = (
                        stage / "data" / "backup" / f"{key}.previous.jsonl"
                    )
                    self._copy_verified(previous.source, destination)
                    receipts.append(_receipt(stage, destination, "copied"))
                if current.jobs:
                    destination = CsvExporter(stage / "data" / "output").export(
                        provider, current.jobs
                    )
                    receipts.append(_receipt(stage, destination, "regenerated"))
            for pair in inventory.analysis_pairs:
                for source in (pair.markdown, pair.json_file):
                    destination = stage / "data" / "output" / source.name
                    self._copy_verified(source, destination)
                    receipts.append(_receipt(stage, destination, "copied"))
            result = self._result(inventory, receipts)
            self._write_marker(stage, "migrated", source_kind, result)
            self._verify_stage(stage)
            if initial != self._source_fingerprints(inventory):
                raise MigrationError("移行中に旧データが変更されました", error_code="MIG005")
            self._commit(stage)
        except BaseException:
            if stage.exists():
                self._remove_incomplete_stage(stage)
            raise

    def create_fresh(self, *, declined: bool = False) -> None:
        stage = self._new_stage()
        try:
            for relative in ("data/work", "data/backup", "data/output", "logs", "state"):
                (stage / relative).mkdir(parents=True, exist_ok=True)
            self._write_marker(
                stage, "declined" if declined else "fresh", "none", None
            )
            self._verify_stage(stage)
            self._commit(stage)
        except BaseException:
            if stage.exists():
                self._remove_incomplete_stage(stage)
            raise

    def adopt_existing(self) -> None:
        if not self.paths.root.is_dir() or self.marker_path.exists():
            raise MigrationError("既存保存先を採用できません", error_code="MIG004")
        repository = Repository(self.paths.work_dir, self.paths.backup_dir)
        receipts: list[dict[str, object]] = []
        providers: dict[str, dict[str, object]] = {}
        for provider in JobSource:
            try:
                jobs = repository.read_snapshot(
                    self.paths.work_dir / f"{provider.value.lower()}.jsonl", provider
                ) if repository.current_exists(provider) else []
            except RepositoryCorruptionError as error:
                raise MigrationError("既存保存先が不正です", error_code="MIG004") from error
            if len(jobs) > MAX_CURRENT_JOBS:
                raise MigrationError(
                    f"既存保存先が{MAX_CURRENT_JOBS}件を超えています",
                    error_code="MIG004",
                )
            providers[provider.value] = {
                "current_count": len(jobs),
                "current_action": "adopted",
                "previous_count": 0,
                "previous_action": "adopted",
                "csv_action": "adopted",
            }
        result = {
            "providers": providers,
            "analysis_pair_count": 0,
            "excluded": _empty_excluded(),
            "files": receipts,
        }
        self._write_marker(self.paths.root, "adopted", "destination", result)

    @staticmethod
    def read_marker(path: Path) -> dict[str, object]:
        try:
            raw = path.read_bytes()
            if (
                raw.startswith(b"\xef\xbb\xbf")
                or not raw.endswith(b"\n")
                or raw.endswith(b"\n\n")
                or b"\r" in raw
            ):
                raise ValueError
            marker = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise MigrationError("移行状態ファイルが不正です", error_code="MIG001") from error
        if set(marker) != {
            "format", "status", "completed_at", "app_version", "source_kind", "result"
        }:
            raise MigrationError("移行状態ファイルのキーが不正です", error_code="MIG001")
        if marker["format"] != MARKER_FORMAT:
            raise MigrationError("移行状態ファイルの形式が不明です", error_code="MIG001")
        if marker["status"] not in {"migrated", "declined", "fresh", "adopted"}:
            raise MigrationError("移行状態が不正です", error_code="MIG001")
        if marker["source_kind"] not in {"automatic", "selected", "destination", "none"}:
            raise MigrationError("移行元種別が不正です", error_code="MIG001")
        try:
            completed_at = marker["completed_at"]
            if not isinstance(completed_at, str) or not completed_at.endswith("Z"):
                raise ValueError
            parsed_time = datetime.fromisoformat(completed_at[:-1] + "+00:00")
            if parsed_time.tzinfo is None:
                raise ValueError
            if not isinstance(marker["app_version"], str) or not marker["app_version"]:
                raise ValueError
            _validate_marker_result(marker)
        except (TypeError, ValueError) as error:
            raise MigrationError("移行状態ファイルの内容が不正です", error_code="MIG001") from error
        return marker

    def _read_optional_snapshot(
        self, path: Path, provider: JobSource
    ) -> SnapshotItem | None:
        if not path.exists():
            return None
        try:
            _reject_regular_file(path, "MIG002")
            jobs = Repository().read_snapshot(path, provider)
            if len(jobs) > MAX_CURRENT_JOBS:
                raise MigrationError(
                    f"{provider.value}のデータが{MAX_CURRENT_JOBS}件を超えています",
                    error_code="MIG003",
                )
            return SnapshotItem(path, tuple(jobs), "copied")
        except RepositoryCorruptionError:
            return SnapshotItem(path, (), "invalid")

    def _analysis_pairs(self, output: Path) -> tuple[AnalysisPair, ...]:
        if not output.is_dir():
            return ()
        result: list[AnalysisPair] = []
        for markdown in output.iterdir():
            if markdown.suffix.lower() != ".md" or not _PAIR_RE.fullmatch(markdown.stem):
                continue
            json_file = markdown.with_suffix(".json")
            try:
                pair = _validate_pair(markdown, json_file)
            except MigrationError:
                continue
            result.append(pair)
        return tuple(sorted(result, key=lambda item: item.markdown.name))

    def _new_stage(self) -> Path:
        if self.paths.root.exists():
            raise MigrationError("保存先が既に存在します", error_code="MIG004")
        stage = self.paths.root.parent / f"RecruitJobTool.migrating-{uuid.uuid4().hex}"
        try:
            stage.mkdir(parents=False, exist_ok=False)
        except OSError as error:
            raise MigrationError("移行準備フォルダーを作成できません", error_code="MIG005") from error
        return stage

    @staticmethod
    def _copy_verified(source: Path, destination: Path) -> None:
        _reject_regular_file(source, "MIG002")
        before = source.read_bytes()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(before)
                stream.flush()
                os.fsync(stream.fileno())
            if temporary.read_bytes() != before:
                raise OSError("copy verification failed")
            os.replace(temporary, destination)
            temporary = None
        except OSError as error:
            raise MigrationError("旧データを安全にコピーできません", error_code="MIG005") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _write_marker(
        self,
        root: Path,
        status: str,
        source_kind: str,
        result: dict[str, object] | None,
    ) -> None:
        marker = {
            "format": MARKER_FORMAT,
            "status": status,
            "completed_at": format_rfc3339(datetime.now(timezone.utc)),
            "app_version": __version__,
            "source_kind": source_kind,
            "result": result,
        }
        content = json.dumps(marker, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        path = root / "state" / MARKER_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as error:
            raise MigrationError("移行状態を保存できません", error_code="MIG005") from error
        finally:
            temporary.unlink(missing_ok=True)

    def _verify_stage(self, stage: Path) -> None:
        marker = self.read_marker(stage / "state" / MARKER_NAME)
        result = marker["result"]
        if isinstance(result, dict):
            for receipt in result.get("files", []):
                relative = receipt.get("path")
                if not isinstance(relative, str) or "\\" in relative:
                    raise MigrationError("移行receiptが不正です", error_code="MIG005")
                parts = Path(relative).parts
                if Path(relative).is_absolute() or ".." in parts:
                    raise MigrationError("移行receiptのパスが不正です", error_code="MIG005")
                target = stage.joinpath(*relative.split("/"))
                if _sha256(target) != receipt.get("sha256") or target.stat().st_size != receipt.get("bytes"):
                    raise MigrationError("移行receiptが一致しません", error_code="MIG005")

    def _commit(self, stage: Path) -> None:
        if self.paths.root.exists():
            raise MigrationError("保存先が既に存在します", error_code="MIG004")
        try:
            os.replace(stage, self.paths.root)
            self._verify_stage(self.paths.root)
        except (OSError, MigrationError) as error:
            failed = self.paths.root.parent / f"migration-failed-{uuid.uuid4().hex}"
            if self.paths.root.exists():
                try:
                    os.replace(self.paths.root, failed)
                except OSError as isolation_error:
                    raise MigrationError("移行失敗データを隔離できません", error_code="MIG006") from isolation_error
            raise MigrationError("移行結果を公開できません", error_code="MIG006") from error

    def _source_fingerprints(
        self, inventory: SourceInventory
    ) -> tuple[tuple[str, int, int, str], ...]:
        paths = {
            item.source
            for item in (*inventory.current.values(), *inventory.previous.values())
            if item.source is not None
        }
        paths.update(
            path for pair in inventory.analysis_pairs for path in (pair.markdown, pair.json_file)
        )
        result = []
        for path in sorted(paths, key=lambda item: str(item)):
            stat_result = path.stat()
            result.append((str(path), stat_result.st_size, stat_result.st_mtime_ns, _sha256(path)))
        return tuple(result)

    def _result(
        self, inventory: SourceInventory, receipts: list[dict[str, object]]
    ) -> dict[str, object]:
        providers: dict[str, dict[str, object]] = {}
        for provider in JobSource:
            current = inventory.current[provider]
            previous = inventory.previous[provider]
            providers[provider.value] = {
                "current_count": len(current.jobs),
                "current_action": current.action,
                "previous_count": len(previous.jobs),
                "previous_action": previous.action,
                "csv_action": "regenerated" if current.jobs else "omitted_empty",
            }
        excluded = _empty_excluded()
        excluded["input_directory_present"] = inventory.input_directory_present
        excluded["logs_directory_present"] = inventory.logs_directory_present
        return {
            "providers": providers,
            "analysis_pair_count": len(inventory.analysis_pairs),
            "excluded": excluded,
            "files": receipts,
        }

    def _remove_incomplete_stage(self, path: Path) -> None:
        if path.parent != self.paths.root.parent or not _STAGE_RE.fullmatch(path.name):
            raise MigrationError("移行途中フォルダーの境界が不正です", error_code="MIG007")
        _reject_reparse(path, "MIG007")
        try:
            shutil.rmtree(path)
        except OSError as error:
            raise MigrationError("移行途中フォルダーを整理できません", error_code="MIG007") from error


def _validate_pair(markdown: Path, json_file: Path) -> AnalysisPair:
    _reject_regular_file(markdown, "MIG003")
    _reject_regular_file(json_file, "MIG003")
    try:
        md_raw = markdown.read_bytes()
        js_raw = json_file.read_bytes()
        if md_raw.startswith(b"\xef\xbb\xbf") or js_raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError
        md = md_raw.decode("utf-8")
        js = js_raw.decode("utf-8")
        if "\x00" in md or "\x00" in js or len(md) > 500_000 or len(js) > 500_000:
            raise ValueError
        versions = re.findall(r"^prompt_template_version: (.+)$", md, re.MULTILINE)
        formats = re.findall(r"^job_data_format: (.+)$", md, re.MULTILINE)
        filenames = re.findall(r'^job_data_file: "([^"]+)"$', md, re.MULTILINE)
        hashes = re.findall(r'^job_data_sha256: "([0-9a-f]{64})"$', md, re.MULTILINE)
        counts = re.findall(r"^job_count: ([0-9]+)$", md, re.MULTILINE)
        if (
            versions[:1]
            not in (
                ["job-comparison-v5"],
                ["job-comparison-v6"],
                ["job-comparison-v7"],
            )
            or formats[:1] != ["job-comparison-jobs-v3"]
            or filenames[:1] != [json_file.name]
            or hashes[:1] != [hashlib.sha256(js_raw).hexdigest()]
            or not counts
        ):
            raise ValueError
        if versions[0] == "job-comparison-v7":
            _validate_candidate_profile_in_markdown(md)
        count = int(counts[0])
        payload = json.loads(js, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        if not isinstance(payload, list) or len(payload) != count or not 1 <= count <= MAX_CURRENT_JOBS:
            raise ValueError
        if any(not isinstance(job, dict) or set(job) != _JOB_KEYS for job in payload):
            raise ValueError
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        PromptInputError,
    ) as error:
        raise MigrationError("分析ペアが不正です", error_code="MIG003") from error
    provider = JobSource.JOBINS if markdown.stem.startswith("jobins_") else JobSource.CAREERBANK
    return AnalysisPair(markdown, json_file, provider, count)


def _validate_candidate_profile_in_markdown(markdown: str) -> None:
    formats = re.findall(
        r"^candidate_profile_format: (.+)$", markdown, re.MULTILINE
    )
    hashes = re.findall(
        r'^candidate_profile_sha256: "([0-9a-f]{64})"$',
        markdown,
        re.MULTILINE,
    )
    blocks = re.findall(
        r"<!-- candidate_profile:start -->\n```json\n([^\r\n]+)\n```\n"
        r"<!-- candidate_profile:end -->",
        markdown,
    )
    if formats != [CANDIDATE_PROFILE_FORMAT] or len(hashes) != 1 or len(blocks) != 1:
        raise ValueError("candidate profile metadata")
    profile = _candidate_profile_from_json(blocks[0], require_canonical=True)
    if hashes != [_candidate_profile_sha256(profile)]:
        raise ValueError("candidate profile hash")
    verification_lines = [
        line.strip()
        for line in markdown.splitlines()
        if line.startswith("    {") and '"prompt_template_version"' in line
    ]
    if len(verification_lines) != 1:
        raise ValueError("verification values")
    verification = json.loads(
        verification_lines[0],
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    expected_keys = {
        "prompt_template_version",
        "job_data_format",
        "job_data_file",
        "job_data_sha256",
        "candidate_profile_format",
        "candidate_profile_sha256",
        "job_count",
    }
    if (
        not isinstance(verification, dict)
        or set(verification) != expected_keys
        or verification.get("prompt_template_version") != "job-comparison-v7"
        or verification.get("candidate_profile_format") != CANDIDATE_PROFILE_FORMAT
        or verification.get("candidate_profile_sha256") != hashes[0]
    ):
        raise ValueError("verification values")


def _receipt(root: Path, path: Path, action: str) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "action": action,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_regular_file(path: Path, code: str) -> None:
    try:
        _reject_reparse(path, code)
        if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
            raise OSError
    except OSError as error:
        raise MigrationError("通常ファイルではありません", error_code=code) from error


def _reject_reparse(path: Path, code: str) -> None:
    try:
        details = path.stat(follow_symlinks=False)
    except OSError as error:
        raise MigrationError("パスを検査できません", error_code=code) from error
    attributes = getattr(details, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if path.is_symlink() or attributes & reparse:
        raise MigrationError("リンクまたはreparse pointは使用できません", error_code=code)


def _same_path(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _same_or_contains(source: Path, destination: Path) -> bool:
    try:
        source = source.resolve(strict=False)
        destination = destination.resolve(strict=False)
        return source == destination or source in destination.parents or destination in source.parents
    except OSError:
        return True


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise ValueError(f"unsupported constant: {value}")


def _empty_excluded() -> dict[str, object]:
    return {
        "input_directory_present": False,
        "logs_directory_present": False,
        "placeholder_file_count": 0,
        "temporary_file_count": 0,
        "unknown_file_count": 0,
        "unknown_directory_count": 0,
        "invalid_analysis_pair_count": 0,
        "stale_csv_count": 0,
    }


def _validate_marker_result(marker: dict[str, object]) -> None:
    status = marker["status"]
    source_kind = marker["source_kind"]
    result = marker["result"]
    if status in {"fresh", "declined"}:
        if source_kind != "none" or result is not None:
            raise ValueError("fresh marker contract mismatch")
        return
    if status == "migrated" and source_kind not in {"automatic", "selected"}:
        raise ValueError("migrated source kind mismatch")
    if status == "adopted" and source_kind != "destination":
        raise ValueError("adopted source kind mismatch")
    if not isinstance(result, dict) or set(result) != {
        "providers", "analysis_pair_count", "excluded", "files"
    }:
        raise ValueError("result keys")
    providers = result["providers"]
    if not isinstance(providers, dict) or set(providers) != {"JOBINS", "CAREERBANK"}:
        raise ValueError("providers")
    for value in providers.values():
        if not isinstance(value, dict) or set(value) != {
            "current_count",
            "current_action",
            "previous_count",
            "previous_action",
            "csv_action",
        }:
            raise ValueError("provider result")
        for key in ("current_count", "previous_count"):
            count = value[key]
            if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= MAX_CURRENT_JOBS:
                raise ValueError("provider count")
        if value["current_action"] not in {"copied", "recovered", "empty", "adopted"}:
            raise ValueError("current action")
        if value["previous_action"] not in {
            "copied", "missing", "excluded_invalid", "adopted"
        }:
            raise ValueError("previous action")
        if value["csv_action"] not in {"regenerated", "omitted_empty", "adopted"}:
            raise ValueError("csv action")
    pair_count = result["analysis_pair_count"]
    if isinstance(pair_count, bool) or not isinstance(pair_count, int) or pair_count < 0:
        raise ValueError("analysis pair count")
    excluded = result["excluded"]
    expected_excluded = set(_empty_excluded())
    if not isinstance(excluded, dict) or set(excluded) != expected_excluded:
        raise ValueError("excluded")
    for key, value in excluded.items():
        if key in {"input_directory_present", "logs_directory_present"}:
            if type(value) is not bool:
                raise ValueError("excluded bool")
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("excluded count")
    files = result["files"]
    if not isinstance(files, list):
        raise ValueError("files")
    for receipt in files:
        if not isinstance(receipt, dict) or set(receipt) != {
            "path", "action", "bytes", "sha256"
        }:
            raise ValueError("receipt")
        relative = receipt["path"]
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ValueError("receipt path")
        if receipt["action"] not in {"copied", "recovered", "regenerated", "adopted"}:
            raise ValueError("receipt action")
        size = receipt["bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("receipt bytes")
        if not isinstance(receipt["sha256"], str) or not _LOWER_SHA.fullmatch(receipt["sha256"]):
            raise ValueError("receipt sha256")


__all__ = [
    "AnalysisPair",
    "MARKER_FORMAT",
    "MigrationManager",
    "SnapshotItem",
    "SourceInventory",
]

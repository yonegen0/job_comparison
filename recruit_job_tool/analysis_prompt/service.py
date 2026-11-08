"""分析プロンプト生成のユースケースと2ファイルの安全な公開。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid
from collections.abc import Callable, Sequence

from recruit_job_tool.candidate_profile import (
    CANDIDATE_PROFILE_FORMAT,
    CandidateProfile,
    _candidate_profile_from_json,
    _candidate_profile_sha256,
)
from recruit_job_tool.errors import PromptInputError, PromptOutputError
from recruit_job_tool.models import Job, JobSource
from recruit_job_tool.csv_exporter import serialize_jobs_csv

from .csv_input import load_prompt_jobs, load_prompt_jobs_from_bytes
from .models import AnalysisGenerationResult, PromptGenerationResult
from .renderer import (
    CANDIDATE_GUIDE_TEMPLATE_VERSION,
    render_analysis_guide,
    render_analysis_prompt,
    render_candidate_analysis_guide,
    render_job_data_json,
    render_job_data_json_v4,
)


@dataclass(slots=True)
class _OutputArtifact:
    destination: Path
    content: bytes
    stage: Path
    backup: Path
    existed: bool
    original_content: bytes | None = None
    backup_ready: bool = False
    published: bool = False


_GUIDE_FAILURE_WARNING = (
    "分析用JSONは作成しましたが、任意の詳細ガイドを作成できませんでした。"
    "JSONだけをAIへ添付して分析できます。"
)


def generate_analysis_files(
    *,
    input_path: Path | None = None,
    input_jobs: Sequence[Job] | None = None,
    json_output_path: Path,
    provider: JobSource,
    guide_output_path: Path | None = None,
    include_guide: bool = True,
    candidate_profile: CandidateProfile | None = None,
    generated_at: datetime | None = None,
) -> AnalysisGenerationResult:
    """一般比較は独立公開し、条件付き比較は2成果物を一括公開する。"""

    if candidate_profile is not None and not include_guide:
        raise PromptOutputError("転職者条件付き比較ではMarkdownガイドが必要です")
    if not include_guide and guide_output_path is not None:
        raise PromptOutputError(
            "ガイドを生成しない場合はMarkdown出力先を指定できません"
        )
    if (input_path is None) == (input_jobs is None):
        raise PromptInputError("入力CSVと保存求人のどちらか一方だけを指定してください")
    input_file = Path(input_path) if input_path is not None else None
    json_file = Path(json_output_path)
    guide_file = Path(guide_output_path) if guide_output_path is not None else None
    _validate_analysis_paths(input_file, json_file, guide_file)

    if input_file is not None:
        jobs = load_prompt_jobs(input_file, expected_source=provider)
    else:
        assert input_jobs is not None
        jobs = load_prompt_jobs_from_bytes(
            serialize_jobs_csv(provider, input_jobs), expected_source=provider,
        )
    company_count = len({job.company for job in jobs if job.company is not None})
    instant = generated_at or datetime.now(timezone.utc)
    json_content = render_job_data_json_v4(
        provider=provider,
        generated_at=instant,
        jobs=jobs,
    )
    expected_json = _load_generated_json(json_content)

    if candidate_profile is not None:
        if guide_file is None:
            raise PromptOutputError("転職者条件付き比較のMarkdown出力先が未指定です")
        guide_content = render_candidate_analysis_guide(
            candidate_profile=candidate_profile
        )
        write_candidate_analysis_pair_atomic(
            json_path=json_file,
            json_content=json_content,
            markdown_path=guide_file,
            markdown_content=guide_content,
            expected_json=expected_json,
        )
        return AnalysisGenerationResult(
            provider=provider,
            job_count=len(jobs),
            company_count=company_count,
            json_path=json_file,
            guide_path=guide_file,
            json_character_count=len(json_content),
            guide_character_count=len(guide_content),
            warnings=(),
        )

    warnings = list(
        write_file_atomic(
            path=json_file,
            content=json_content,
            artifact_label="求人JSON",
            verifier=lambda published: _verify_v4_json(published, expected_json),
        )
    )

    guide_content: str | None = None
    published_guide: Path | None = None
    if include_guide:
        if guide_file is None:
            raise PromptOutputError("Markdown出力先が指定されていません")
        try:
            guide_content = render_analysis_guide()
            warnings.extend(
                write_file_atomic(
                    path=guide_file,
                    content=guide_content,
                    artifact_label="詳細ガイド",
                )
            )
            published_guide = guide_file
        except Exception:
            warnings.append(_GUIDE_FAILURE_WARNING)

    return AnalysisGenerationResult(
        provider=provider,
        job_count=len(jobs),
        company_count=company_count,
        json_path=json_file,
        guide_path=published_guide,
        json_character_count=len(json_content),
        guide_character_count=(len(guide_content) if published_guide else None),
        warnings=tuple(warnings),
    )


def write_file_atomic(
    *,
    path: Path,
    content: str,
    artifact_label: str = "出力ファイル",
    verifier: Callable[[Path], None] | None = None,
) -> tuple[str, ...]:
    """単一成果物を検証付きで公開し、失敗時はその成果物だけを戻す。"""

    if not isinstance(content, str):
        raise PromptOutputError(f"{artifact_label}の内容が不正です")
    destination = _resolve_output_path(Path(path))
    transaction_id = uuid.uuid4().hex
    artifact = _artifact(destination, content.encode("utf-8"), transaction_id)
    artifacts = (artifact,)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _validate_destination(destination)
        _write_verified(artifact.stage, artifact.content)
        if artifact.existed:
            artifact.original_content = destination.read_bytes()
            _write_verified(artifact.backup, artifact.original_content)
            artifact.backup_ready = True
        os.replace(artifact.stage, destination)
        artifact.published = True
        if destination.read_bytes() != artifact.content:
            raise PromptOutputError(f"公開した{artifact_label}の検証に失敗しました")
        if verifier is not None:
            verifier(destination)
    except BaseException as error:
        rollback_failures = _rollback_artifacts(artifacts)
        _cleanup_artifacts(
            artifacts,
            keep_backups=frozenset(rollback_failures),
        )
        if isinstance(error, KeyboardInterrupt):
            raise
        if rollback_failures:
            raise PromptOutputError(
                f"{artifact_label}を完全に復元できませんでした。バックアップを保持しています"
            ) from error
        if isinstance(error, PromptOutputError):
            raise
        raise PromptOutputError(f"{artifact_label}を公開できません") from error

    cleanup_failures = _cleanup_artifacts(artifacts, keep_backups=frozenset())
    if cleanup_failures:
        return (f"{artifact_label}は作成しましたが、一時バックアップを削除できませんでした。",)
    return ()


def _validate_analysis_paths(
    input_path: Path | None,
    json_path: Path,
    guide_path: Path | None,
) -> None:
    input_resolved = _resolve_path(input_path) if input_path is not None else None
    outputs = [_resolve_output_path(json_path)]
    if guide_path is not None:
        outputs.append(_resolve_output_path(guide_path))
    for output in outputs:
        _validate_destination(output)
        if input_resolved is not None and _paths_match(input_resolved, output):
            raise PromptOutputError("入力CSVと出力先には異なるパスが必要です")
    if len(outputs) == 2 and _paths_match(outputs[0], outputs[1]):
        raise PromptOutputError("JSONとMarkdownに同じ出力先は指定できません")


def _load_generated_json(content: str) -> object:
    try:
        return json.loads(content)
    except (TypeError, ValueError) as exc:
        raise PromptOutputError("生成した求人JSONを検証できません") from exc


def _verify_v4_json(path: Path, expected: object) -> None:
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
            raise PromptOutputError("公開した求人JSONの文字コードまたは改行が不正です")
        actual = json.loads(raw.decode("utf-8", errors="strict"))
    except PromptOutputError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise PromptOutputError("公開した求人JSONを再読込みできません") from exc
    if actual != expected:
        raise PromptOutputError("公開した求人JSONの内容が生成内容と一致しません")


def generate_analysis_prompt(
    *,
    input_path: Path,
    output_path: Path,
    json_output_path: Path,
    provider: JobSource,
    candidate_profile: CandidateProfile,
    generated_at: datetime | None = None,
) -> PromptGenerationResult:
    input_file = Path(input_path)
    markdown_file = Path(output_path)
    json_file = Path(json_output_path)
    _validate_paths(input_file, markdown_file, json_file)

    jobs = load_prompt_jobs(input_file, expected_source=provider)
    company_count = len({job.company for job in jobs if job.company is not None})
    json_content = render_job_data_json(jobs=jobs)
    json_sha256 = hashlib.sha256(json_content.encode("utf-8")).hexdigest()
    markdown_content = render_analysis_prompt(
        provider=provider,
        candidate_profile=candidate_profile,
        job_count=len(jobs),
        company_count=company_count,
        generated_at=generated_at or datetime.now(timezone.utc),
        job_data_filename=json_file.name,
        job_data_sha256=json_sha256,
    )
    write_prompt_pair_atomic(
        markdown_path=markdown_file,
        markdown_content=markdown_content,
        json_path=json_file,
        json_content=json_content,
    )
    return PromptGenerationResult(
        markdown_path=markdown_file,
        json_path=json_file,
        provider=provider,
        job_count=len(jobs),
        company_count=company_count,
        markdown_character_count=len(markdown_content),
        json_character_count=len(json_content),
    )


def write_prompt_pair_atomic(
    *,
    markdown_path: Path,
    markdown_content: str,
    json_path: Path,
    json_content: str,
) -> None:
    """両出力を検証後に公開し、途中失敗時は旧状態へ戻す。"""

    _write_pair_atomic(
        markdown_path=markdown_path,
        markdown_content=markdown_content,
        json_path=json_path,
        json_content=json_content,
        verifier=_verify_published_pair,
        artifact_label="分析プロンプト",
    )


def write_candidate_analysis_pair_atomic(
    *,
    markdown_path: Path,
    markdown_content: str,
    json_path: Path,
    json_content: str,
    expected_json: object,
) -> None:
    """v4求人JSONと条件付きガイドを1トランザクションで公開する。"""

    def verify(
        *,
        markdown_artifact: _OutputArtifact,
        json_artifact: _OutputArtifact,
    ) -> None:
        _verify_candidate_analysis_pair(
            markdown_artifact=markdown_artifact,
            json_artifact=json_artifact,
            expected_json=expected_json,
        )

    _write_pair_atomic(
        markdown_path=markdown_path,
        markdown_content=markdown_content,
        json_path=json_path,
        json_content=json_content,
        verifier=verify,
        artifact_label="転職者条件付きAI比較ファイル",
    )


def _write_pair_atomic(
    *,
    markdown_path: Path,
    markdown_content: str,
    json_path: Path,
    json_content: str,
    verifier: Callable[..., None],
    artifact_label: str,
) -> None:
    """2ファイルに共通するstage、backup、公開、rollbackを実行する。"""

    if not isinstance(markdown_content, str) or not isinstance(json_content, str):
        raise PromptOutputError(f"{artifact_label}の出力内容が不正です")

    markdown_destination = _resolve_output_path(Path(markdown_path))
    json_destination = _resolve_output_path(Path(json_path))
    if _paths_match(markdown_destination, json_destination):
        raise PromptOutputError("MarkdownとJSONに同じ出力先は指定できません")

    transaction_id = uuid.uuid4().hex
    json_artifact = _artifact(
        json_destination,
        json_content.encode("utf-8"),
        transaction_id,
    )
    markdown_artifact = _artifact(
        markdown_destination,
        markdown_content.encode("utf-8"),
        transaction_id,
    )
    artifacts = (json_artifact, markdown_artifact)

    try:
        for artifact in artifacts:
            artifact.destination.parent.mkdir(parents=True, exist_ok=True)
            _validate_destination(artifact.destination)
        for artifact in artifacts:
            _write_verified(artifact.stage, artifact.content)
        for artifact in artifacts:
            if artifact.existed:
                artifact.original_content = artifact.destination.read_bytes()
                _write_verified(artifact.backup, artifact.original_content)
                artifact.backup_ready = True
    except BaseException as error:
        _cleanup_artifacts(artifacts, keep_backups=frozenset())
        if isinstance(error, KeyboardInterrupt):
            raise
        if isinstance(error, PromptOutputError):
            raise
        raise PromptOutputError(f"{artifact_label}の公開準備に失敗しました") from error

    try:
        for artifact in artifacts:
            os.replace(artifact.stage, artifact.destination)
            artifact.published = True
        verifier(
            markdown_artifact=markdown_artifact,
            json_artifact=json_artifact,
        )
    except BaseException as error:
        rollback_failures = _rollback_artifacts(artifacts)
        keep_backups = frozenset(rollback_failures)
        _cleanup_artifacts(artifacts, keep_backups=keep_backups)
        if rollback_failures:
            raise PromptOutputError(
                f"{artifact_label}の公開に失敗し、旧出力を完全に復旧できませんでした。"
                "復旧用バックアップがある場合は保持しています"
            ) from error
        if isinstance(error, KeyboardInterrupt):
            raise
        if isinstance(error, PromptOutputError):
            raise
        raise PromptOutputError(f"{artifact_label}を公開できません") from error

    cleanup_failures = _cleanup_artifacts(artifacts, keep_backups=frozenset())
    if cleanup_failures:
        raise PromptOutputError(
            f"{artifact_label}は生成されましたが、一時バックアップを削除できません"
        )


def _validate_paths(input_path: Path, markdown_path: Path, json_path: Path) -> None:
    input_resolved = _resolve_path(input_path)
    markdown_resolved = _resolve_output_path(markdown_path)
    json_resolved = _resolve_output_path(json_path)
    _validate_destination(markdown_resolved)
    _validate_destination(json_resolved)
    pairs = (
        (input_resolved, markdown_resolved),
        (input_resolved, json_resolved),
        (markdown_resolved, json_resolved),
    )
    if any(_paths_match(left, right) for left, right in pairs):
        raise PromptOutputError("入力CSVと2つの出力先には異なるパスが必要です")


def _artifact(destination: Path, content: bytes, transaction_id: str) -> _OutputArtifact:
    prefix = f".{destination.name}.{transaction_id}"
    return _OutputArtifact(
        destination=destination,
        content=content,
        stage=destination.parent / f"{prefix}.tmp",
        backup=destination.parent / f"{prefix}.bak",
        existed=destination.exists(),
    )


def _write_verified(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != content:
            raise PromptOutputError("一時出力の検証に失敗しました")
    except PromptOutputError:
        raise
    except OSError as exc:
        raise PromptOutputError("一時出力を書き込めません") from exc


def _verify_published_pair(
    *,
    markdown_artifact: _OutputArtifact,
    json_artifact: _OutputArtifact,
) -> None:
    try:
        published_json = json_artifact.destination.read_bytes()
        published_markdown = markdown_artifact.destination.read_bytes()
        if published_json != json_artifact.content:
            raise PromptOutputError("公開JSONの検証に失敗しました")
        if published_markdown != markdown_artifact.content:
            raise PromptOutputError("公開Markdownの検証に失敗しました")
        markdown_text = published_markdown.decode("utf-8", errors="strict")
        expected_filename = (
            "job_data_file: "
            + json.dumps(json_artifact.destination.name, ensure_ascii=False)
        )
        expected_sha256 = (
            "job_data_sha256: "
            + json.dumps(hashlib.sha256(published_json).hexdigest())
        )
        lines = set(markdown_text.splitlines())
        if expected_filename not in lines or expected_sha256 not in lines:
            raise PromptOutputError("公開した2ファイルの整合性検証に失敗しました")
        _verify_candidate_profile(markdown_text)
    except PromptOutputError:
        raise
    except (OSError, UnicodeError) as exc:
        raise PromptOutputError("公開した2ファイルを検証できません") from exc


def _verify_candidate_analysis_pair(
    *,
    markdown_artifact: _OutputArtifact,
    json_artifact: _OutputArtifact,
    expected_json: object,
) -> None:
    try:
        published_json = json_artifact.destination.read_bytes()
        published_markdown = markdown_artifact.destination.read_bytes()
        if published_json != json_artifact.content:
            raise PromptOutputError("公開した求人JSONが生成内容と一致しません")
        if published_markdown != markdown_artifact.content:
            raise PromptOutputError("公開した条件入りガイドが生成内容と一致しません")
        _verify_v4_json(json_artifact.destination, expected_json)
        if published_markdown.startswith(b"\xef\xbb\xbf") or not published_markdown.endswith(
            b"\n"
        ):
            raise PromptOutputError("条件入りガイドの文字コードまたは改行が不正です")
        markdown_text = published_markdown.decode("utf-8", errors="strict")
        if "\r" in markdown_text:
            raise PromptOutputError("条件入りガイドの改行コードが不正です")
        if markdown_text.splitlines().count(f"prompt_template_version: {CANDIDATE_GUIDE_TEMPLATE_VERSION}") != 1:
            raise PromptOutputError("条件入りガイドのテンプレート版が不正です")
        if markdown_text.splitlines().count("## 転職者条件") != 1:
            raise PromptOutputError("条件入りガイドの転職者条件ブロックが不正です")
        lines = markdown_text.splitlines()
        if any(
            line.startswith("candidate_profile_format:")
            or line.startswith("candidate_profile_sha256:")
            or line == "<!-- candidate_profile:start -->"
            for line in lines
        ):
            raise PromptOutputError("条件入りガイドに旧候補者JSON表現が含まれています")
    except PromptOutputError:
        raise
    except (OSError, UnicodeError) as exc:
        raise PromptOutputError("公開した条件付き比較ファイルを検証できません") from exc


def _verify_candidate_profile(markdown_text: str) -> None:
    lines = markdown_text.splitlines()
    versions = [line for line in lines if line.startswith("prompt_template_version: ")]
    if not versions or versions[0] != "prompt_template_version: job-comparison-v7":
        return
    formats = [line for line in lines if line.startswith("candidate_profile_format: ")]
    hashes = [line for line in lines if line.startswith("candidate_profile_sha256: ")]
    start = "<!-- candidate_profile:start -->\n```json\n"
    end = "\n```\n<!-- candidate_profile:end -->"
    if (
        formats != [f"candidate_profile_format: {CANDIDATE_PROFILE_FORMAT}"]
        or len(hashes) != 1
        or markdown_text.count(start) != 1
        or markdown_text.count(end) != 1
    ):
        raise PromptOutputError("公開した2ファイルの整合性検証に失敗しました")
    profile_json = markdown_text.split(start, 1)[1].split(end, 1)[0]
    try:
        profile = _candidate_profile_from_json(profile_json, require_canonical=True)
        expected = f'candidate_profile_sha256: "{_candidate_profile_sha256(profile)}"'
    except Exception as error:
        raise PromptOutputError(
            "公開した2ファイルの整合性検証に失敗しました"
        ) from error
    if hashes != [expected]:
        raise PromptOutputError("公開した2ファイルの整合性検証に失敗しました")


def _rollback_artifacts(
    artifacts: tuple[_OutputArtifact, ...],
) -> set[int]:
    failures: set[int] = set()
    for artifact in reversed(artifacts):
        if not artifact.published:
            continue
        try:
            if artifact.existed:
                if not artifact.backup_ready:
                    raise OSError("verified backup is unavailable")
                backup_content = artifact.backup.read_bytes()
                _write_verified(artifact.stage, backup_content)
                os.replace(artifact.stage, artifact.destination)
                if artifact.destination.read_bytes() != backup_content:
                    raise OSError("restored output differs from backup")
            else:
                artifact.destination.unlink()
                if artifact.destination.exists():
                    raise OSError("new output still exists")
            artifact.published = False
        except (OSError, PromptOutputError):
            failures.add(id(artifact))
    return failures


def _cleanup_artifacts(
    artifacts: tuple[_OutputArtifact, ...],
    *,
    keep_backups: frozenset[int],
) -> set[int]:
    failures: set[int] = set()
    for artifact in artifacts:
        for path, keep in (
            (artifact.stage, False),
            (artifact.backup, id(artifact) in keep_backups),
        ):
            if keep:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                failures.add(id(artifact))
    return failures


def _validate_destination(path: Path) -> None:
    try:
        if path.is_symlink():
            raise PromptOutputError("出力先にシンボリックリンクは使用できません")
        if path.exists() and not path.is_file():
            raise PromptOutputError("出力先が通常ファイルではありません")
    except PromptOutputError:
        raise
    except OSError as exc:
        raise PromptOutputError("出力先を検査できません") from exc


def _resolve_output_path(path: Path) -> Path:
    try:
        expanded = path.expanduser()
        if expanded.is_symlink():
            raise PromptOutputError("出力先にシンボリックリンクは使用できません")
        return expanded.resolve(strict=False)
    except PromptOutputError:
        raise
    except OSError as exc:
        raise PromptOutputError("出力パスを解決できません") from exc


def _resolve_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=False)
    except OSError as exc:
        raise PromptOutputError("パスを解決できません") from exc


def _paths_match(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError as exc:
        raise PromptOutputError("パスの同一性を検査できません") from exc

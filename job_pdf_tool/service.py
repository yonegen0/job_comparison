from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from job_comparison_app.fingerprints import (
    ensure_fingerprints_unchanged,
    ensure_pdf_inventory_unchanged,
    fingerprint_files,
    fingerprint_pdf_inventory,
)
from job_comparison_app.models import FileFingerprint, PdfInventoryItem, RunLevel
from job_comparison_app.pdf_comparison_guide import render_pdf_comparison_guide, template_path
from recruit_job_tool.candidate_profile import CandidateProfile

from .analysis import analyze
from .models import DocumentResult
from .output import JobInfoDocument, prepare_job_info_document
from .transaction import apply_transaction, directory_locks


def _is_reparse_like(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _validate_output_target(path: Path) -> None:
    if _is_reparse_like(path):
        raise ValueError(f"JSON出力先にリンクは指定できません: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"JSON出力先が通常ファイルではありません: {path}")
    parent = path.parent
    if _is_reparse_like(parent):
        raise ValueError(f"JSON出力フォルダーにリンクは指定できません: {parent}")
    if parent.exists() and not parent.is_dir():
        raise ValueError(f"JSON出力先の親が通常フォルダーではありません: {parent}")


@dataclass(frozen=True, slots=True)
class PdfOrganizationRequest:
    input_dir: Path
    aliases_path: Path | None = None
    job_info_json: Path | None = None
    report_json: Path | None = None
    import_main_csv: Path | None = None
    comparison_guide_path: Path | None = None
    candidate_profile: CandidateProfile | None = field(default=None, repr=False)


@dataclass(slots=True)
class PdfOrganizationPlan:
    request: PdfOrganizationRequest
    results: list[DocumentResult]
    job_info: JobInfoDocument
    summary: dict[str, int]
    job_info_json: Path
    report_json: Path
    level: RunLevel
    pdf_inventory: tuple[PdfInventoryItem, ...]
    file_fingerprints: tuple[FileFingerprint, ...]
    comparison_guide_path: Path | None = None
    comparison_guide_content: str | None = field(default=None, repr=False)
    candidate_applied: bool = False


@dataclass(slots=True)
class PdfOrganizationResult:
    plan: PdfOrganizationPlan
    job_info: JobInfoDocument
    applied: bool
    level: RunLevel


def resolve_job_info_json(input_dir: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    candidates = sorted(input_dir.glob("求人情報*.json"), key=lambda path: path.name.casefold())
    for candidate in candidates:
        _validate_output_target(candidate)
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise ValueError(f"求人情報JSONが複数あります。--output-json で1件を指定してください: {names}")
    return (candidates[0] if candidates else input_dir / "求人情報.json").resolve()


def summarize(results: list[DocumentResult]) -> dict[str, int]:
    summary = {
        "成功": 0,
        "部分成功": 0,
        "改名保留": 0,
        "エラー": 0,
        "JSON出力対象": 0,
        "JSON除外": 0,
        "必須項目除外": 0,
        "リネーム対象": 0,
        "変更なし": 0,
    }
    for result in results:
        summary[result.status] = summary.get(result.status, 0) + 1
        if result.is_job_info_eligible:
            summary["JSON出力対象"] += 1
        else:
            summary["JSON除外"] += 1
            if result.required_issues:
                summary["必須項目除外"] += 1
        if result.is_rename_eligible:
            if result.source_path.name == result.proposed_name:
                summary["変更なし"] += 1
            else:
                summary["リネーム対象"] += 1
    return summary


def result_level(results: list[DocumentResult], stale_count: int = 0) -> RunLevel:
    if not stale_count and all(result.status == "成功" for result in results):
        return RunLevel.SUCCESS
    return RunLevel.WARNING


def prepare_pdf_organization(request: PdfOrganizationRequest) -> PdfOrganizationPlan:
    if request.candidate_profile is not None and request.comparison_guide_path is None:
        raise ValueError("転職者条件を使用する場合は比較指示書の出力先が必要です。")
    input_dir = request.input_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"入力フォルダーが存在しません: {input_dir}")
    aliases_path = request.aliases_path.resolve() if request.aliases_path else None
    if aliases_path and not aliases_path.is_file():
        raise ValueError(f"見出し別名JSONが存在しません: {aliases_path}")
    import_main_csv = request.import_main_csv.resolve() if request.import_main_csv else None
    if import_main_csv and not import_main_csv.is_file():
        raise ValueError(f"移行元の求人情報CSVが存在しません: {import_main_csv}")
    raw_job_info = request.job_info_json or input_dir / "求人情報.json"
    raw_report = request.report_json or input_dir / "処理レポート.json"
    if request.job_info_json is not None:
        _validate_output_target(raw_job_info)
    if request.report_json is not None:
        _validate_output_target(raw_report)
    job_info_json = resolve_job_info_json(input_dir, request.job_info_json)
    report_json = raw_report.resolve()
    _validate_output_target(job_info_json)
    _validate_output_target(report_json)
    if job_info_json == report_json:
        raise ValueError("求人情報JSONと処理レポートJSONには異なるパスを指定してください。")
    protected_inputs = {
        path.resolve()
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.casefold() == ".pdf"
    }
    if aliases_path:
        protected_inputs.add(aliases_path)
    if import_main_csv:
        protected_inputs.add(import_main_csv)
    overlap = [path for path in (job_info_json, report_json) if path in protected_inputs]
    if overlap:
        raise ValueError(f"入力ファイルをJSON出力先には指定できません: {overlap[0]}")
    guide_path = request.comparison_guide_path
    guide_content = None
    if guide_path is not None:
        _validate_output_target(guide_path)
        for parent in guide_path.parents:
            if _is_reparse_like(parent):
                raise ValueError("比較指示書の出力先にリンクは指定できません。")
        guide_path = guide_path.resolve()
        if guide_path.suffix.casefold() != ".md":
            raise ValueError("比較指示書の出力先はMarkdownにしてください。")
        if guide_path in protected_inputs | {job_info_json, report_json, template_path().resolve()}:
            raise ValueError("比較指示書の出力先が入力または他の出力と重なっています。")
        guide_content = render_pdf_comparison_guide(
            job_info_filename=job_info_json.name, candidate_profile=request.candidate_profile,
        )
    results = analyze(input_dir, aliases_path)
    job_info = prepare_job_info_document(results, job_info_json, import_main_csv)
    normalized = PdfOrganizationRequest(
        input_dir,
        aliases_path,
        job_info_json,
        report_json,
        import_main_csv,
        guide_path,
        request.candidate_profile,
    )
    fingerprint_paths = [job_info_json, report_json]
    if guide_path is not None:
        fingerprint_paths.append(guide_path)
    if aliases_path:
        fingerprint_paths.append(aliases_path)
    if import_main_csv:
        fingerprint_paths.append(import_main_csv)
    return PdfOrganizationPlan(
        request=normalized,
        results=results,
        job_info=job_info,
        summary=summarize(results),
        job_info_json=job_info_json,
        report_json=report_json,
        level=result_level(results, len(job_info.stale_jobs)),
        pdf_inventory=fingerprint_pdf_inventory(input_dir),
        file_fingerprints=fingerprint_files(fingerprint_paths),
        comparison_guide_path=guide_path,
        comparison_guide_content=guide_content,
        candidate_applied=request.candidate_profile is not None,
    )


def _create_output_directories(paths: tuple[Path, ...]) -> list[Path]:
    created: list[Path] = []
    try:
        for directory in sorted(
            {path.resolve() for path in paths},
            key=lambda path: (len(path.parts), str(path).casefold()),
        ):
            if directory.exists():
                if not directory.is_dir():
                    raise OSError(f"JSON出力先の親がフォルダーではありません: {directory}")
                continue
            missing: list[Path] = []
            current = directory
            while not current.exists():
                missing.append(current)
                parent = current.parent
                if parent == current:
                    break
                current = parent
            if current.exists() and not current.is_dir():
                raise OSError(f"JSON出力先の親がフォルダーではありません: {current}")
            directory.mkdir(parents=True, exist_ok=True)
            created.extend(path for path in reversed(missing) if path not in created)
    except Exception:
        for directory in sorted(created, key=lambda path: len(path.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
    return created


def _remove_empty_directories(paths: list[Path]) -> None:
    for directory in sorted(paths, key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


def commit_pdf_organization(plan: PdfOrganizationPlan) -> PdfOrganizationResult:
    input_dir = plan.request.input_dir
    output_directories = (plan.job_info_json.parent, plan.report_json.parent)
    if plan.comparison_guide_path is not None:
        if plan.comparison_guide_content is None:
            raise ValueError("比較指示書の本文が準備されていません。")
        _validate_output_target(plan.comparison_guide_path)
        for parent in plan.comparison_guide_path.parents:
            if _is_reparse_like(parent):
                raise ValueError("比較指示書の出力先にリンクは指定できません。")
        output_directories += (plan.comparison_guide_path.parent,)
    created_directories = _create_output_directories(output_directories)
    lock_directories = [input_dir, *output_directories]
    if plan.request.import_main_csv is not None:
        lock_directories.append(plan.request.import_main_csv.parent)
    completed = False
    try:
        with directory_locks(lock_directories):
            ensure_pdf_inventory_unchanged(input_dir, plan.pdf_inventory)
            ensure_fingerprints_unchanged(plan.file_fingerprints)
            target_paths = {plan.job_info_json.resolve(), plan.report_json.resolve()}
            if plan.comparison_guide_path is not None:
                target_paths.add(plan.comparison_guide_path.resolve())
            output_fingerprints = tuple(
                fingerprint
                for fingerprint in plan.file_fingerprints
                if fingerprint.path in target_paths
            )
            expected_existing = {
                fingerprint.path: fingerprint.exists for fingerprint in output_fingerprints
            }
            job_info = apply_transaction(
                plan.results,
                input_dir,
                plan.job_info_json,
                plan.report_json,
                job_info=plan.job_info,
                import_main_csv=plan.request.import_main_csv,
                expected_existing=expected_existing,
                verify_outputs=lambda: ensure_fingerprints_unchanged(output_fingerprints),
                comparison_guide_path=plan.comparison_guide_path,
                comparison_guide_content=plan.comparison_guide_content,
            )
        completed = True
    finally:
        if not completed:
            _remove_empty_directories(created_directories)
    return PdfOrganizationResult(plan=plan, job_info=job_info, applied=True, level=plan.level)


__all__ = [
    "PdfOrganizationPlan",
    "PdfOrganizationRequest",
    "PdfOrganizationResult",
    "commit_pdf_organization",
    "prepare_pdf_organization",
    "resolve_job_info_json",
    "result_level",
    "summarize",
]

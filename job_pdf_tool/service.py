from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from job_comparison_app.fingerprints import (
    ensure_fingerprints_unchanged,
    ensure_pdf_inventory_unchanged,
    fingerprint_files,
    fingerprint_pdf_inventory,
)
from job_comparison_app.models import FileFingerprint, PdfInventoryItem, RunLevel

from .analysis import analyze
from .models import DocumentResult
from .output import MainCsvTable, prepare_main_csv_table
from .transaction import apply_transaction, directory_locks


@dataclass(frozen=True, slots=True)
class PdfOrganizationRequest:
    input_dir: Path
    aliases_path: Path | None = None
    output_csv: Path | None = None
    report_csv: Path | None = None


@dataclass(slots=True)
class PdfOrganizationPlan:
    request: PdfOrganizationRequest
    results: list[DocumentResult]
    table: MainCsvTable
    summary: dict[str, int]
    output_csv: Path
    report_csv: Path
    level: RunLevel
    pdf_inventory: tuple[PdfInventoryItem, ...]
    file_fingerprints: tuple[FileFingerprint, ...]


@dataclass(slots=True)
class PdfOrganizationResult:
    plan: PdfOrganizationPlan
    table: MainCsvTable
    applied: bool
    level: RunLevel


def resolve_output_csv(input_dir: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    candidates = sorted(input_dir.glob("求人情報*.csv"), key=lambda path: path.name.casefold())
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise ValueError(f"求人情報CSVが複数あります。--output-csv で1件を指定してください: {names}")
    return (candidates[0] if candidates else input_dir / "求人情報.csv").resolve()


def summarize(results: list[DocumentResult]) -> dict[str, int]:
    summary = {
        "成功": 0,
        "部分成功": 0,
        "改名保留": 0,
        "エラー": 0,
        "CSV出力対象": 0,
        "CSV除外": 0,
        "必須項目除外": 0,
        "リネーム対象": 0,
        "変更なし": 0,
    }
    for result in results:
        summary[result.status] = summary.get(result.status, 0) + 1
        if result.is_main_csv_eligible:
            summary["CSV出力対象"] += 1
        else:
            summary["CSV除外"] += 1
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
    input_dir = request.input_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"入力フォルダが存在しません: {input_dir}")
    aliases_path = request.aliases_path.resolve() if request.aliases_path else None
    if aliases_path and not aliases_path.is_file():
        raise ValueError(f"見出し別名JSONが存在しません: {aliases_path}")
    output_csv = resolve_output_csv(input_dir, request.output_csv)
    report_csv = (request.report_csv or input_dir / "処理レポート.csv").resolve()
    if output_csv == report_csv:
        raise ValueError("求人情報CSVと処理レポートCSVには異なるパスを指定してください。")
    protected_inputs = {path.resolve() for path in input_dir.glob("*.pdf")}
    if aliases_path:
        protected_inputs.add(aliases_path)
    overlap = [path for path in (output_csv, report_csv) if path in protected_inputs]
    if overlap:
        raise ValueError(f"入力ファイルをCSV出力先には指定できません: {overlap[0]}")
    results = analyze(input_dir, aliases_path)
    table = prepare_main_csv_table(results, output_csv)
    normalized = PdfOrganizationRequest(input_dir, aliases_path, output_csv, report_csv)
    fingerprint_paths = [output_csv, report_csv]
    if aliases_path:
        fingerprint_paths.append(aliases_path)
    return PdfOrganizationPlan(
        request=normalized,
        results=results,
        table=table,
        summary=summarize(results),
        output_csv=output_csv,
        report_csv=report_csv,
        level=result_level(results, len(table.stale_rows)),
        pdf_inventory=fingerprint_pdf_inventory(input_dir),
        file_fingerprints=fingerprint_files(fingerprint_paths),
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
                    raise OSError(f"CSV出力先の親がフォルダーではありません: {directory}")
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
                raise OSError(f"CSV出力先の親がフォルダーではありません: {current}")
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
    output_directories = (plan.output_csv.parent, plan.report_csv.parent)
    created_directories = _create_output_directories(output_directories)
    completed = False
    try:
        with directory_locks((input_dir, *output_directories)):
            ensure_pdf_inventory_unchanged(input_dir, plan.pdf_inventory)
            ensure_fingerprints_unchanged(plan.file_fingerprints)
            target_paths = {plan.output_csv.resolve(), plan.report_csv.resolve()}
            output_fingerprints = tuple(
                fingerprint
                for fingerprint in plan.file_fingerprints
                if fingerprint.path in target_paths
            )
            expected_existing = {
                fingerprint.path: fingerprint.exists for fingerprint in output_fingerprints
            }
            table = apply_transaction(
                plan.results,
                input_dir,
                plan.output_csv,
                plan.report_csv,
                expected_existing=expected_existing,
                verify_outputs=lambda: ensure_fingerprints_unchanged(output_fingerprints),
            )
        completed = True
    finally:
        if not completed:
            _remove_empty_directories(created_directories)
    return PdfOrganizationResult(plan=plan, table=table, applied=True, level=plan.level)

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Mapping

from job_comparison_app.errors import InputChangedError

from .models import DocumentResult
from .output import MainCsvTable, prepare_main_csv_table, stage_main_csv, stage_report_csv
from .pdf_reader import sha256_file


class ApplyError(RuntimeError):
    pass


@contextmanager
def directory_lock(input_dir: Path):
    with directory_locks((input_dir,)):
        yield


@contextmanager
def directory_locks(directories: Iterable[Path]):
    resolved = sorted(
        {Path(directory).resolve() for directory in directories},
        key=lambda path: str(path).casefold(),
    )
    acquired: list[tuple[Path, int]] = []
    try:
        for directory in resolved:
            lock_path = directory / ".job_pdf_tool.lock"
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except OSError as exc:
                raise ApplyError(f"別の処理が実行中か、ロックを作成できません: {lock_path}") from exc
            try:
                os.write(descriptor, str(os.getpid()).encode("ascii"))
            except OSError:
                os.close(descriptor)
                lock_path.unlink(missing_ok=True)
                raise
            acquired.append((lock_path, descriptor))
        yield
    finally:
        for lock_path, descriptor in reversed(acquired):
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def _preflight(results: list[DocumentResult], output_csv: Path, report_csv: Path) -> None:
    if output_csv.resolve() == report_csv.resolve():
        raise ApplyError("求人情報CSVと処理レポートCSVには異なるパスを指定してください。")
    for target in (output_csv, report_csv):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            try:
                with target.open("a", encoding="utf-8"):
                    pass
            except OSError as exc:
                raise ApplyError(f"出力ファイルを更新できません: {target}") from exc
    for result in results:
        if not result.is_rename_eligible or result.source_path.name == result.proposed_name:
            continue
        try:
            with result.source_path.open("rb+"):
                pass
        except OSError as exc:
            raise ApplyError(f"PDFが使用中または変更不可です: {result.source_path.name}") from exc


def apply_transaction(
    results: list[DocumentResult],
    input_dir: Path,
    output_csv: Path,
    report_csv: Path,
    *,
    expected_existing: Mapping[Path, bool] | None = None,
    verify_outputs: Callable[[], None] | None = None,
) -> MainCsvTable:
    table = prepare_main_csv_table(results, output_csv)
    _preflight(results, output_csv, report_csv)
    staged_main: Path | None = None
    staged_report: Path | None = None
    output_backups: dict[Path, Path] = {}
    published_targets: set[Path] = set()
    rename_state: list[tuple[DocumentResult, Path, Path, Path]] = []
    expected = {
        Path(path).resolve(): exists
        for path, exists in (expected_existing or {}).items()
    }

    try:
        staged_main = stage_main_csv(table, output_csv)
        staged_report = stage_report_csv(results, report_csv, table.stale_rows)

        for result in results:
            if not result.is_rename_eligible:
                continue
            source = result.source_path
            final = input_dir / result.proposed_name
            if source.resolve() == final.resolve():
                if sha256_file(source) != result.sha256:
                    raise ApplyError(f"解析後にPDF内容が変化しました: {source.name}")
                continue
            temporary = input_dir / f".__jobtool__{uuid.uuid4().hex}.pdf"
            source.replace(temporary)
            rename_state.append((result, source, temporary, final))

        for result, source, temporary, final in rename_state:
            if final.exists():
                raise ApplyError(f"改名先が既に存在します: {final.name}")
            temporary.replace(final)
            if sha256_file(final) != result.sha256:
                raise ApplyError(f"リネーム後のSHA-256が一致しません: {final.name}")

        if verify_outputs is not None:
            verify_outputs()
        for target in (output_csv, report_csv):
            resolved_target = target.resolve()
            if resolved_target in expected and target.exists() != expected[resolved_target]:
                raise InputChangedError(
                    f"確認後にCSV出力先が変更されました: {target.name}"
                )

        for target in (output_csv, report_csv):
            if target.exists():
                backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.bak")
                target.replace(backup)
                output_backups[target] = backup

        staged_main.rename(output_csv)
        published_targets.add(output_csv)
        staged_main = None
        staged_report.rename(report_csv)
        published_targets.add(report_csv)
        staged_report = None

        for backup in output_backups.values():
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                pass
        return table
    except Exception as exc:
        rollback_errors: list[str] = []
        for result, source, temporary, final in reversed(rename_state):
            current = final if final.exists() else temporary if temporary.exists() else None
            if current is None:
                rollback_errors.append(f"現在位置不明: {source.name}")
                continue
            try:
                if source.exists():
                    rollback_errors.append(f"復元先が既に存在: {source.name}")
                else:
                    current.replace(source)
            except OSError as rollback_exc:
                rollback_errors.append(f"{source.name}: {rollback_exc}")

        for target in reversed((output_csv, report_csv)):
            backup = output_backups.get(target)
            try:
                if target in published_targets:
                    target.unlink(missing_ok=True)
                if backup is not None and backup.exists():
                    if target.exists():
                        rollback_errors.append(f"復元先が既に存在: {target.name}")
                    else:
                        backup.replace(target)
            except OSError as rollback_exc:
                rollback_errors.append(f"{target.name}: {rollback_exc}")

        if isinstance(exc, InputChangedError) and not rollback_errors:
            raise
        detail = f"処理をロールバックしました: {exc}"
        if rollback_errors:
            detail += " / ロールバック失敗: " + " | ".join(rollback_errors)
        raise ApplyError(detail) from exc
    finally:
        if staged_main:
            staged_main.unlink(missing_ok=True)
        if staged_report:
            staged_report.unlink(missing_ok=True)

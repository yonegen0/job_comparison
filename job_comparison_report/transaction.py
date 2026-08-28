from __future__ import annotations

import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


class OutputTransactionError(RuntimeError):
    pass


@contextmanager
def output_lock(output_pdf: Path):
    lock_path = output_pdf.parent / ".job_comparison_report.lock"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise OutputTransactionError(f"別の比較資料生成処理が実行中です: {lock_path}") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        lock_path.unlink(missing_ok=True)


def staged_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    return Path(raw_path)


def preflight_targets(targets: list[Path]) -> None:
    resolved = [target.resolve() for target in targets]
    if len(resolved) != len(set(resolved)):
        raise OutputTransactionError("出力先が重複しています。")
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        probe = staged_path(target)
        probe.unlink(missing_ok=True)
        if target.exists():
            try:
                with target.open("r+b"):
                    pass
            except OSError as exc:
                raise OutputTransactionError(f"出力ファイルを更新できません: {target}") from exc


def commit_outputs(staged: dict[Path, Path]) -> None:
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for target, temporary in staged.items():
            if not temporary.is_file():
                raise OutputTransactionError(f"一時成果物がありません: {temporary}")
            if target.exists():
                backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.bak")
                os.replace(target, backup)
                backups[target] = backup
        for target, temporary in staged.items():
            os.replace(temporary, target)
            committed.append(target)
        for backup in backups.values():
            backup.unlink(missing_ok=True)
    except Exception as exc:
        rollback_errors: list[str] = []
        for target in reversed(committed):
            try:
                target.unlink(missing_ok=True)
            except OSError as rollback_exc:
                rollback_errors.append(f"{target}: {rollback_exc}")
        for target, backup in backups.items():
            try:
                if backup.exists():
                    os.replace(backup, target)
            except OSError as rollback_exc:
                rollback_errors.append(f"{target}: {rollback_exc}")
        message = f"成果物の確定に失敗し、既存成果物を復元しました: {exc}"
        if rollback_errors:
            message += " / 復元失敗: " + " | ".join(rollback_errors)
        raise OutputTransactionError(message) from exc
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)

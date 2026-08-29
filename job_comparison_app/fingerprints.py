from __future__ import annotations

import hashlib
from pathlib import Path

from .errors import InputChangedError
from .models import FileFingerprint, PdfInventoryItem


def fingerprint_file(path: Path) -> FileFingerprint:
    resolved = Path(path).resolve(strict=False)
    if not resolved.exists():
        return FileFingerprint(path=resolved, exists=False)
    if not resolved.is_file():
        raise OSError(f"通常ファイルではありません: {resolved}")
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    stat = resolved.stat()
    return FileFingerprint(
        path=resolved,
        exists=True,
        size=stat.st_size,
        modified_ns=stat.st_mtime_ns,
        sha256=digest.hexdigest(),
    )


def fingerprint_files(paths: list[Path] | tuple[Path, ...]) -> tuple[FileFingerprint, ...]:
    unique = sorted({Path(path).resolve(strict=False) for path in paths}, key=lambda item: str(item).casefold())
    return tuple(fingerprint_file(path) for path in unique)


def fingerprint_pdf_inventory(input_dir: Path) -> tuple[PdfInventoryItem, ...]:
    return tuple(
        PdfInventoryItem(path.name, fingerprint_file(path))
        for path in sorted(Path(input_dir).glob("*.pdf"), key=lambda item: item.name.casefold())
    )


def ensure_fingerprints_unchanged(expected: tuple[FileFingerprint, ...]) -> None:
    for before in expected:
        try:
            current = fingerprint_file(before.path)
        except OSError as exc:
            raise InputChangedError(f"確認後にファイルを再確認できません: {before.path.name}") from exc
        if current != before:
            raise InputChangedError(f"確認後にファイルが変更されました: {before.path.name}")


def ensure_pdf_inventory_unchanged(
    input_dir: Path,
    expected: tuple[PdfInventoryItem, ...],
) -> None:
    try:
        current = fingerprint_pdf_inventory(input_dir)
    except OSError as exc:
        raise InputChangedError("確認後に入力フォルダを再確認できません。") from exc
    if current != expected:
        raise InputChangedError("確認後に入力フォルダ内のPDFが追加、削除、または変更されました。")

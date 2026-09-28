"""Read-only preparation and per-file atomic publication of selected PDFs."""
from __future__ import annotations

import html
import os
import shutil
import tempfile
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from .errors import InputChangedError, InputValidationError
from .fingerprints import fingerprint_file
from .models import FileFingerprint


class CopyStatus(str, Enum):
    READY = "コピー予定"
    CONFLICT = "同名あり"
    MISSING = "未発見"
    INVALID = "不正入力"
    DUPLICATE = "重複除外"
    FAILED = "失敗"
    NEW = "新規コピー済み"
    REPLACED = "置換済み"
    RENAMED = "別名保存済み"


@dataclass(frozen=True)
class PdfCopyRequest:
    input_dir: Path
    text: str


@dataclass(frozen=True)
class PdfCopyEntry:
    line: int
    raw: str
    name: str
    status: CopyStatus
    reason: str = ""
    destination: str = ""
    source_fingerprint: FileFingerprint | None = None
    destination_fingerprint: FileFingerprint | None = None


@dataclass(frozen=True)
class PdfCopyPlan:
    request: PdfCopyRequest
    output_dir: Path
    entries: tuple[PdfCopyEntry, ...]
    reserved: frozenset[str]


@dataclass(frozen=True)
class PdfCopyDecision:
    line: int
    destination: str
    replace_existing: bool = False


@dataclass(frozen=True)
class PdfCopyResult:
    output_dir: Path
    entries: tuple[PdfCopyEntry, ...]

    @property
    def warning(self) -> bool:
        return not self.entries or any(
            e.status in (CopyStatus.MISSING, CopyStatus.INVALID, CopyStatus.FAILED)
            or (bool(e.reason) and e.status != CopyStatus.DUPLICATE)
            for e in self.entries
        )


def linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def validate_name(name: str) -> bool:
    return (not os.path.isreserved(name) and not any(c in name for c in '/\\:*?"<>|')
            and not any(ord(c) < 32 for c in name) and Path(name).suffix.lower() == '.pdf')


def prepare_pdf_copy(request: PdfCopyRequest) -> PdfCopyPlan:
    source = request.input_dir.resolve()
    if not source.is_dir():
        raise InputValidationError("コピー元フォルダーが存在しません。")
    output = source / "対象pdf"
    if linked(output) or (output.exists() and not output.is_dir()):
        raise InputValidationError("対象pdfは通常のフォルダーである必要があります。")
    inventory = {p.name: p for p in source.iterdir()}
    destinations = {p.name.casefold(): p for p in output.iterdir()} if output.exists() else {}
    entries = []
    seen: set[str] = set()
    for line, raw in enumerate(request.text.splitlines(), 1):
        name = html.unescape(raw).strip()
        if not name:
            continue
        entry = PdfCopyEntry(line, raw, name, CopyStatus.READY, destination=name)
        if not validate_name(name):
            entry = replace(entry, status=CopyStatus.INVALID, reason="拡張子.pdfを含むファイル名だけを指定してください。", destination="")
        elif name in seen:
            entry = replace(entry, status=CopyStatus.DUPLICATE, reason="先行行と同じファイル名です。", destination="")
        else:
            seen.add(name)
            path = inventory.get(name)
            target = destinations.get(name.casefold(), output / name)
            if path is None:
                entry = replace(entry, status=CopyStatus.MISSING, destination="")
            elif linked(path) or not path.is_file():
                entry = replace(entry, status=CopyStatus.FAILED, reason="コピー元が通常ファイルではありません。")
            elif linked(target) or (target.exists() and not target.is_file()):
                entry = replace(entry, status=CopyStatus.FAILED, reason="コピー先が通常ファイルではありません。")
            else:
                entry = replace(entry, source_fingerprint=fingerprint_file(path),
                                destination_fingerprint=fingerprint_file(target),
                                destination=target.name,
                                status=CopyStatus.CONFLICT if target.exists() else CopyStatus.READY)
        entries.append(entry)
    return PdfCopyPlan(replace(request, input_dir=source), output, tuple(entries), frozenset(destinations))


def resolve_decisions(plan: PdfCopyPlan, choices: dict[int, bool]) -> tuple[PdfCopyDecision, ...]:
    """choices maps conflict rows to True (replace) or False (numbered copy)."""
    reserved = set(plan.reserved) | {e.name.casefold() for e in plan.entries}
    decisions = []
    for entry in plan.entries:
        if entry.status not in (CopyStatus.READY, CopyStatus.CONFLICT):
            continue
        name = entry.destination
        overwrite = entry.status == CopyStatus.CONFLICT and choices[entry.line]
        if entry.status == CopyStatus.CONFLICT and not overwrite:
            number = 1
            while True:
                name = f"{Path(entry.name).stem} ({number}){Path(entry.name).suffix}"
                if name.casefold() not in reserved:
                    break
                number += 1
        reserved.add(name.casefold())
        decisions.append(PdfCopyDecision(entry.line, name, overwrite))
    return tuple(decisions)


def commit_pdf_copy(plan: PdfCopyPlan, decisions: tuple[PdfCopyDecision, ...]) -> PdfCopyResult:
    if prepare_pdf_copy(plan.request) != plan:
        raise InputChangedError("確認後にPDFまたはコピー先が変更されました。事前確認してください。")
    by_line = {d.line: d for d in decisions}
    eligible = {e.line for e in plan.entries if e.status in (CopyStatus.READY, CopyStatus.CONFLICT)}
    if set(by_line) != eligible or len(by_line) != len(decisions):
        raise InputValidationError("コピー対象と保存先の指定が一致しません。")
    names: set[str] = set()
    for entry in plan.entries:
        decision = by_line.get(entry.line)
        if decision is None:
            continue
        if (not validate_name(decision.destination) or decision.destination.casefold() in names
                or (decision.replace_existing and (entry.status != CopyStatus.CONFLICT or decision.destination != entry.destination))):
            raise InputValidationError("コピー先の指定が不正です。")
        names.add(decision.destination.casefold())
        target = plan.output_dir / decision.destination
        if not decision.replace_existing and (target.exists() or linked(target)):
            raise InputChangedError("コピー先に同名項目が作成されました。")
    if not decisions:
        return PdfCopyResult(plan.output_dir, plan.entries)
    plan.output_dir.mkdir(exist_ok=True)
    results = []
    for entry in plan.entries:
        decision = by_line.get(entry.line)
        if decision is None:
            results.append(entry)
            continue
        temporary: Path | None = None
        result = entry
        try:
            source = plan.request.input_dir / entry.name
            target = plan.output_dir / decision.destination
            if linked(plan.output_dir) or linked(source) or linked(target):
                raise OSError("リンクを介したコピーはできません。")
            if fingerprint_file(source) != entry.source_fingerprint:
                raise OSError("コピー元が変更されました。")
            fd, name = tempfile.mkstemp(prefix=".pdf-copy-", suffix=".tmp", dir=plan.output_dir)
            temporary = Path(name)
            os.close(fd)
            shutil.copy2(source, temporary)
            if fingerprint_file(temporary).sha256 != entry.source_fingerprint.sha256:
                raise OSError("コピー中にコピー元が変更されました。")
            if linked(plan.output_dir) or linked(target):
                raise OSError("コピー先がリンクに変更されました。")
            if decision.replace_existing:
                if fingerprint_file(target) != entry.destination_fingerprint:
                    raise OSError("置換対象が変更されました。")
                os.replace(temporary, target)
            else:
                # On Windows rename fails if another writer created the destination.
                os.rename(temporary, target)
            result = replace(entry, destination=decision.destination,
                             status=CopyStatus.REPLACED if decision.replace_existing else
                             CopyStatus.RENAMED if decision.destination != entry.name else CopyStatus.NEW)
        except OSError as exc:
            result = replace(entry, status=CopyStatus.FAILED, destination=decision.destination, reason=str(exc))
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    result = replace(result, reason=f"{result.reason} 一時ファイルを削除できません: {temporary}")
        results.append(result)
    return PdfCopyResult(plan.output_dir, tuple(results))

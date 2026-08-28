from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from .models import DocumentResult


MAX_ABSOLUTE_PATH = 240

_ILLEGAL_TRANSLATION = str.maketrans(
    {
        "<": "＜",
        ">": "＞",
        ":": "：",
        '"': "”",
        "/": "／",
        "\\": "￥",
        "|": "｜",
        "?": "？",
        "*": "＊",
    }
)
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _strip_invalid_unicode(value: str) -> str:
    output: list[str] = []
    for char in value.replace("\x00", "").replace("\ufffd", "").replace("\uffff", ""):
        if unicodedata.category(char) in {"Cc", "Cs", "Cn"}:
            continue
        output.append(char)
    return "".join(output)


def clean_identity(value: str) -> str:
    return re.sub(r"\s+", " ", _strip_invalid_unicode(value)).strip()


def resolve_positions(results: list[DocumentResult]) -> None:
    for result in results:
        if result.error or result.raw_position.is_missing:
            continue
        result.resolved_position = clean_identity(result.raw_position.value)
        if result.raw_position.source_heading == "職種分類（中分類）":
            result.position_resolution = "middle_category"
        else:
            result.position_resolution = result.raw_position.classification


def sanitize_component(value: str) -> str:
    value = clean_identity(value).translate(_ILLEGAL_TRANSLATION).rstrip(". ")
    if not value:
        return ""
    if value.upper() in _RESERVED_NAMES:
        value = f"_{value}"
    return value


def _identifier(result: DocumentResult, length: int = 8) -> str:
    if result.job_id:
        compact = re.sub(r"[^0-9A-Za-z]", "", result.job_id)
        if compact:
            return compact[-length:]
    if result.sha256:
        return result.sha256[:length]
    return "document"


def _identifier_candidates(result: DocumentResult) -> list[str]:
    values = [_identifier(result, 8)]
    if result.sha256:
        values.extend(result.sha256[:length] for length in (8, 12, 16, 24, 32, 64))
    return list(dict.fromkeys(value for value in values if value))


def _trim_end(value: str) -> str:
    return value.rstrip(" ._-–—｜／")


def _fit_path(
    directory: Path,
    company: str,
    position: str,
    identifier: str,
    suffix: str = "",
) -> tuple[str, bool]:
    suffix_text = f"-{suffix}" if suffix else ""
    candidate = f"{company}（{position}{suffix_text}）.pdf"
    if len(str(directory / candidate)) <= MAX_ABSOLUTE_PATH:
        return candidate, False

    if not suffix_text:
        suffix_text = f"-{identifier}"
    available = MAX_ABSOLUTE_PATH - len(str(directory)) - 1
    fixed = len("（）.pdf") + len(suffix_text)
    if available <= fixed + 1:
        return "", True

    minimum_position = min(8, len(position)) or 1
    company_value = company
    position_budget = available - fixed - len(company_value)
    if position_budget < minimum_position:
        company_budget = max(1, available - fixed - minimum_position)
        company_value = _trim_end(company_value[:company_budget]) or company_value[:1]
        position_budget = available - fixed - len(company_value)
    position_value = _trim_end(position[: max(1, position_budget)]) or position[:1]
    return f"{company_value}（{position_value}{suffix_text}）.pdf", True


def _filename_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _select_unique_name(
    result: DocumentResult,
    directory: Path,
    company: str,
    position: str,
    *,
    force_identifier: bool,
    blocked_names: set[str],
    used_names: set[str],
) -> tuple[str, bool, bool]:
    identifier = _identifier(result)
    tokens = _identifier_candidates(result)
    if not force_identifier:
        tokens.insert(0, "")

    for token in tokens:
        candidate, path_shortened = _fit_path(directory, company, position, identifier, token)
        if not candidate:
            continue
        key = _filename_key(candidate)
        if key not in blocked_names and key not in used_names:
            return candidate, bool(token), path_shortened

    stable = result.sha256[:16] or identifier
    for number in range(2, 10_000):
        token = f"{stable}-{number}"
        candidate, path_shortened = _fit_path(directory, company, position, identifier, token)
        if not candidate:
            continue
        key = _filename_key(candidate)
        if key not in blocked_names and key not in used_names:
            return candidate, True, path_shortened
    return "", True, True


def assign_proposed_names(results: list[DocumentResult], input_dir: Path) -> None:
    resolve_positions(results)
    for result in results:
        result.validate_required_identity()

    prepared: list[tuple[DocumentResult, str, str]] = []
    groups: dict[tuple[str, str], list[DocumentResult]] = defaultdict(list)
    for result in results:
        if not result.is_main_csv_eligible:
            continue
        # This row is the single source of truth for both displayed values and naming.
        row = result.main_csv_row()
        raw_company = row["会社名"]
        raw_position = row["募集ポジション"]
        company = sanitize_component(raw_company)
        position = sanitize_component(raw_position)
        if not company or not position:
            result.add_report("新ファイル名", "warning", "安全なファイル名を生成できません。")
            continue
        if company != raw_company or position != raw_position:
            result.add_report(
                "新ファイル名",
                "warning",
                "Windowsで使用できない文字または空白を安全な表記へ変換しました。",
            )
        prepared.append((result, company, position))
        groups[(normalize_key(company), normalize_key(position))].append(result)

    eligible_source_names = {
        _filename_key(result.source_path.name)
        for result, _, _ in prepared
    }
    blocked_names = {
        _filename_key(path.name)
        for path in input_dir.glob("*.pdf")
        if _filename_key(path.name) not in eligible_source_names
    }
    used_names: set[str] = set()

    for result, company, position in sorted(
        prepared,
        key=lambda item: (item[0].source_path.name.casefold(), item[0].sha256),
    ):
        duplicate_group = len(groups[(normalize_key(company), normalize_key(position))]) > 1
        candidate, identifier_added, path_shortened = _select_unique_name(
            result,
            input_dir,
            company,
            position,
            force_identifier=duplicate_group,
            blocked_names=blocked_names,
            used_names=used_names,
        )
        if not candidate:
            result.add_report(
                "新ファイル名",
                "warning",
                "パス長と同名衝突の制約内で安全なファイル名を生成できません。",
            )
            continue
        if identifier_added:
            result.add_report(
                "新ファイル名",
                "warning",
                "同名衝突を避けるため求人IDまたはSHA-256由来の識別子を付けました。",
            )
        if path_shortened:
            result.add_report(
                "新ファイル名",
                "warning",
                f"絶対パスを {MAX_ABSOLUTE_PATH} 文字以内に収めるためファイル名を短縮しました。",
            )
        result.proposed_name = candidate
        used_names.add(_filename_key(candidate))


def normalize_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()

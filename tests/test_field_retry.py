import csv
from pathlib import Path

import pytest

import job_pdf_tool.cli as cli
from job_pdf_tool.config import load_aliases
from job_pdf_tool.models import DocumentResult, EXTRACTED_FIELDS, FieldValue, PdfDocument
from job_pdf_tool.naming import assign_proposed_names
from job_pdf_tool.output import load_job_info_json
from job_pdf_tool.pdf_reader import PdfToolReadError, sha256_file
from job_pdf_tool.transaction import apply_transaction


BASE_SHA = "a" * 64
CHANGED_SHA = "b" * 64


def _document(path: Path, digest: str = BASE_SHA) -> PdfDocument:
    return PdfDocument(
        path=path,
        sha256=digest,
        page_count=1,
        lines=[],
        text="extracted text",
        plain_text="extracted text",
    )


def _complete_result(
    path: Path,
    *,
    digest: str = BASE_SHA,
    profile: str = "jobins",
) -> DocumentResult:
    result = DocumentResult(source_path=path, sha256=digest, page_count=1, profile=profile)
    result.company = FieldValue("例示株式会社", "direct", "採用企業名", 1)
    result.raw_position = FieldValue("事務スタッフ", "direct", "職種分類（中分類）", 1)
    result.job_id = "job-original"
    for field_name in EXTRACTED_FIELDS:
        result.fields[field_name] = FieldValue(
            f"{field_name}の値",
            "direct",
            field_name,
            1,
        )
    return result


def _set_field(result: DocumentResult, field_name: str, value: FieldValue) -> None:
    if field_name == "会社名":
        result.company = value
    elif field_name == "募集ポジション":
        result.raw_position = value
    else:
        result.fields[field_name] = value


def _get_field(result: DocumentResult, field_name: str) -> FieldValue:
    if field_name == "会社名":
        return result.company
    if field_name == "募集ポジション":
        return result.raw_position
    return result.fields[field_name]


def _make_missing(result: DocumentResult, field_name: str) -> None:
    _set_field(result, field_name, FieldValue())
    result.reports = [report for report in result.reports if report.target_field != field_name]
    if field_name not in {"会社名", "募集ポジション"}:
        result.add_report(
            field_name,
            "missing",
            "PDF内に該当項目の記載を確認できません。",
        )


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    read_outcomes: list[PdfDocument | Exception],
    extract_outcomes: list[DocumentResult | Exception],
    post_hashes: list[str] | None = None,
) -> dict[str, int]:
    reads = list(read_outcomes)
    extracts = list(extract_outcomes)
    hashes = list(post_hashes or [])
    calls = {"read": 0, "extract": 0, "hash": 0, "extractor_init": 0}

    def fake_read_pdf(path: Path) -> PdfDocument:
        calls["read"] += 1
        outcome = reads.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    class FakeExtractor:
        def __init__(self, aliases: dict[str, list[str]]):
            calls["extractor_init"] += 1

        def extract(self, document: PdfDocument) -> DocumentResult:
            calls["extract"] += 1
            outcome = extracts.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    def fake_sha256_file(path: Path) -> str:
        calls["hash"] += 1
        return hashes.pop(0) if hashes else BASE_SHA

    monkeypatch.setattr(cli, "read_pdf", fake_read_pdf)
    monkeypatch.setattr(cli, "Extractor", FakeExtractor)
    monkeypatch.setattr(cli, "sha256_file", fake_sha256_file)
    return calls


@pytest.mark.parametrize("field_name", cli._RETRY_FIELD_NAMES)
def test_each_missing_field_is_retried_three_times(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
) -> None:
    path = tmp_path / "source.pdf"
    initial = _complete_result(path)
    _make_missing(initial, field_name)
    candidates = []
    for _ in range(3):
        candidate = _complete_result(path)
        _make_missing(candidate, field_name)
        candidates.append(candidate)

    calls = _install_fakes(
        monkeypatch,
        read_outcomes=[_document(path) for _ in range(3)],
        extract_outcomes=candidates,
        post_hashes=[BASE_SHA] * 3,
    )

    retried = cli._retry_initially_missing_fields(path, initial, load_aliases(None))

    assert _get_field(retried, field_name).is_missing
    assert calls == {"read": 3, "extract": 3, "hash": 3, "extractor_init": 3}
    field_reports = [report for report in retried.reports if report.target_field == field_name]
    assert len(field_reports) == (0 if field_name in {"会社名", "募集ポジション"} else 1)


@pytest.mark.parametrize("success_attempt", [1, 2, 3])
def test_retry_stops_when_target_field_is_obtained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    success_attempt: int,
) -> None:
    path = tmp_path / "source.pdf"
    initial = _complete_result(path)
    _make_missing(initial, "給与")
    candidates: list[DocumentResult] = []
    for attempt in range(1, success_attempt + 1):
        candidate = _complete_result(path)
        if attempt < success_attempt:
            _make_missing(candidate, "給与")
        else:
            candidate.fields["給与"] = FieldValue("35万円", "direct", "月給", 2)
        candidates.append(candidate)
    calls = _install_fakes(
        monkeypatch,
        read_outcomes=[_document(path) for _ in range(success_attempt)],
        extract_outcomes=candidates,
        post_hashes=[BASE_SHA] * success_attempt,
    )

    retried = cli._retry_initially_missing_fields(path, initial, load_aliases(None))

    assert retried.fields["給与"] == FieldValue("35万円", "direct", "月給", 2)
    assert calls["read"] == success_attempt
    assert calls["extract"] == success_attempt
    reports = [report for report in retried.reports if report.target_field == "給与"]
    assert len(reports) == 1
    assert reports[0].classification == "direct"
    assert reports[0].source_heading == "月給"
    assert reports[0].page == 2
    assert "回目" not in reports[0].message


def test_missing_fields_are_retried_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.pdf"
    initial = _complete_result(path)
    _make_missing(initial, "給与")
    _make_missing(initial, "残業時間")

    salary_candidate = _complete_result(path)
    salary_candidate.fields["給与"] = FieldValue("40万円", "direct", "月給", 2)
    salary_candidate.fields["残業時間"] = FieldValue("999時間", "direct", "残業時間", 2)
    salary_candidate.job_id = "candidate-job"
    salary_candidate.add_report("残業時間", "direct", "混入させないレポート")

    overtime_candidates = []
    for _ in range(3):
        candidate = _complete_result(path)
        candidate.fields["給与"] = FieldValue("1円", "direct", "月給", 9)
        _make_missing(candidate, "残業時間")
        overtime_candidates.append(candidate)

    calls = _install_fakes(
        monkeypatch,
        read_outcomes=[_document(path) for _ in range(4)],
        extract_outcomes=[salary_candidate, *overtime_candidates],
        post_hashes=[BASE_SHA] * 4,
    )

    retried = cli._retry_initially_missing_fields(path, initial, load_aliases(None))

    assert retried.fields["給与"].value == "40万円"
    assert retried.fields["残業時間"].is_missing
    assert retried.job_id == "job-original"
    assert calls["read"] == 4
    assert not any(report.message == "混入させないレポート" for report in retried.reports)


def test_retry_exceptions_are_silent_and_later_success_is_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.pdf"
    initial = _complete_result(path)
    _make_missing(initial, "給与")
    success = _complete_result(path)
    success.fields["給与"] = FieldValue("30万円", "direct", "月給", 1)
    calls = _install_fakes(
        monkeypatch,
        read_outcomes=[PdfToolReadError("read failure"), _document(path), _document(path)],
        extract_outcomes=[RuntimeError("extract failure"), success],
        post_hashes=[BASE_SHA],
    )

    retried = cli._retry_initially_missing_fields(path, initial, load_aliases(None))

    assert retried.fields["給与"].value == "30万円"
    assert calls["read"] == 3
    assert calls["extract"] == 2
    assert calls["hash"] == 1
    assert not any("failure" in report.message for report in retried.reports)


def test_retry_profile_mismatch_is_not_adopted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.pdf"
    initial = _complete_result(path, profile="jobins")
    _make_missing(initial, "会社名")
    candidates = [_complete_result(path, profile="generic") for _ in range(3)]
    calls = _install_fakes(
        monkeypatch,
        read_outcomes=[_document(path) for _ in range(3)],
        extract_outcomes=candidates,
        post_hashes=[BASE_SHA] * 3,
    )

    retried = cli._retry_initially_missing_fields(path, initial, load_aliases(None))

    assert retried.company.is_missing
    assert calls["read"] == 3


def test_retry_conflict_stops_and_is_rejected_by_identity_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.pdf"
    initial = _complete_result(path)
    _make_missing(initial, "会社名")
    candidate = _complete_result(path)
    candidate.company = FieldValue(
        "第一株式会社\n第二株式会社",
        "conflict",
        "採用企業名 / 会社名",
        1,
    )
    calls = _install_fakes(
        monkeypatch,
        read_outcomes=[_document(path)],
        extract_outcomes=[candidate],
        post_hashes=[BASE_SHA],
    )

    retried = cli._retry_initially_missing_fields(path, initial, load_aliases(None))
    assign_proposed_names([retried], tmp_path)
    retried.finalize_status()

    assert calls["read"] == 1
    assert retried.company.classification == "conflict"
    assert not retried.is_main_csv_eligible
    assert retried.status == "エラー"


def test_final_missing_identity_report_is_not_duplicated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.pdf"
    initial = _complete_result(path)
    _make_missing(initial, "会社名")
    candidates = []
    for _ in range(3):
        candidate = _complete_result(path)
        _make_missing(candidate, "会社名")
        candidates.append(candidate)
    _install_fakes(
        monkeypatch,
        read_outcomes=[_document(path) for _ in range(3)],
        extract_outcomes=candidates,
        post_hashes=[BASE_SHA] * 3,
    )

    retried = cli._retry_initially_missing_fields(path, initial, load_aliases(None))
    assign_proposed_names([retried], tmp_path)
    retried.finalize_status()

    company_reports = [report for report in retried.reports if report.target_field == "会社名"]
    assert len(company_reports) == 1
    assert len(retried.required_issues) == 1
    assert company_reports[0].classification == "missing"


def test_sha_change_before_retry_extraction_is_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.pdf"
    initial = _complete_result(path)
    _make_missing(initial, "給与")
    calls = _install_fakes(
        monkeypatch,
        read_outcomes=[_document(path, CHANGED_SHA)],
        extract_outcomes=[],
    )

    retried = cli._retry_initially_missing_fields(path, initial, load_aliases(None))
    retried.finalize_status()

    assert retried.sha256 == BASE_SHA
    assert retried.status == "エラー"
    assert BASE_SHA in retried.error and CHANGED_SHA in retried.error
    assert calls["read"] == 1
    assert calls["extract"] == 0
    assert len(retried.reports) == 1
    assert retried.reports[0].target_field == "ファイル"
    assert retried.reports[0].classification == "error"


def test_sha_change_after_retry_extraction_is_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.pdf"
    initial = _complete_result(path)
    _make_missing(initial, "給与")
    candidate = _complete_result(path)
    candidate.fields["給与"] = FieldValue("30万円", "direct", "月給", 1)
    calls = _install_fakes(
        monkeypatch,
        read_outcomes=[_document(path)],
        extract_outcomes=[candidate],
        post_hashes=[CHANGED_SHA],
    )

    retried = cli._retry_initially_missing_fields(path, initial, load_aliases(None))

    assert retried.error
    assert retried.fields["給与"].is_missing
    assert calls["extract"] == 1
    assert calls["hash"] == 1


def test_non_missing_conflict_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.pdf"
    initial = _complete_result(path)
    initial.fields["年間休日"] = FieldValue("120日", "conflict", "年間休日", 1)

    def unexpected_call(*args, **kwargs):
        raise AssertionError("取得済み項目は再取得しない")

    monkeypatch.setattr(cli, "read_pdf", unexpected_call)

    retried = cli._retry_initially_missing_fields(path, initial, load_aliases(None))

    assert retried.fields["年間休日"].classification == "conflict"


def test_analyze_performs_retry_without_writing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.pdf"
    original_bytes = b"%PDF-test-content"
    path.write_bytes(original_bytes)
    digest = sha256_file(path)
    initial = _complete_result(path, digest=digest)
    _make_missing(initial, "募集ポジション")
    candidate = _complete_result(path, digest=digest)
    candidate.raw_position = FieldValue("営業、事務、管理", "direct", "職種分類（中分類）", 1)
    calls = _install_fakes(
        monkeypatch,
        read_outcomes=[_document(path, digest), _document(path, digest)],
        extract_outcomes=[initial, candidate],
        post_hashes=[digest],
    )

    results = cli.analyze(tmp_path)

    assert len(results) == 1
    assert results[0].raw_position.value == "営業、事務、管理"
    assert results[0].proposed_name == "例示株式会社（営業、事務、管理）.pdf"
    assert path.read_bytes() == original_bytes
    assert not list(tmp_path.glob("*.csv"))
    assert calls["read"] == 2


def test_retried_identity_drives_csv_and_pdf_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-retry-identity")
    digest = sha256_file(source)
    initial = _complete_result(source, digest=digest)
    _make_missing(initial, "会社名")
    candidate = _complete_result(source, digest=digest)
    candidate.company = FieldValue("再取得株式会社", "direct", "採用企業名", 1)
    calls = _install_fakes(
        monkeypatch,
        read_outcomes=[_document(source, digest)],
        extract_outcomes=[candidate],
        post_hashes=[digest],
    )

    retried = cli._retry_initially_missing_fields(source, initial, load_aliases(None))
    assign_proposed_names([retried], tmp_path)
    retried.finalize_status()
    output_json = tmp_path / "求人情報.json"
    report_json = tmp_path / "処理レポート.json"
    apply_transaction([retried], tmp_path, output_json, report_json)

    target = tmp_path / "再取得株式会社（事務スタッフ）.pdf"
    assert calls["read"] == 1
    assert target.exists()
    assert not source.exists()
    assert sha256_file(target) == digest
    row = load_job_info_json(output_json).jobs[0]
    assert row["会社名"] == "再取得株式会社"
    assert row["新ファイル名"] == target.name


def test_retried_duplicate_identities_add_identifiers_to_every_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_path = tmp_path / "first.pdf"
    second_path = tmp_path / "second.pdf"
    first_path.write_bytes(b"%PDF-first")
    second_path.write_bytes(b"%PDF-second")
    first = _complete_result(first_path)
    second = _complete_result(second_path)
    first.job_id = "job-00000001"
    second.job_id = "job-00000002"
    _make_missing(first, "会社名")
    _make_missing(second, "会社名")
    first_candidate = _complete_result(first_path)
    second_candidate = _complete_result(second_path)
    first_candidate.company = FieldValue("共通株式会社", "direct", "採用企業名", 1)
    second_candidate.company = FieldValue("共通株式会社", "direct", "採用企業名", 1)
    _install_fakes(
        monkeypatch,
        read_outcomes=[_document(first_path), _document(second_path)],
        extract_outcomes=[first_candidate, second_candidate],
        post_hashes=[BASE_SHA, BASE_SHA],
    )

    cli._retry_initially_missing_fields(first_path, first, load_aliases(None))
    cli._retry_initially_missing_fields(second_path, second, load_aliases(None))
    assign_proposed_names([first, second], tmp_path)

    assert first.proposed_name == "共通株式会社（事務スタッフ-00000001）.pdf"
    assert second.proposed_name == "共通株式会社（事務スタッフ-00000002）.pdf"


def test_initial_read_error_does_not_enter_field_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-broken")
    calls = {"read": 0, "extractor": 0}

    def failed_read(source: Path) -> PdfDocument:
        calls["read"] += 1
        raise PdfToolReadError("initial failure")

    class UnexpectedExtractor:
        def __init__(self, aliases: dict[str, list[str]]):
            calls["extractor"] += 1

    monkeypatch.setattr(cli, "read_pdf", failed_read)
    monkeypatch.setattr(cli, "Extractor", UnexpectedExtractor)
    monkeypatch.setattr(cli, "sha256_file", lambda source: BASE_SHA)

    results = cli.analyze(tmp_path)

    assert calls == {"read": 1, "extractor": 0}
    assert len(results) == 1
    assert results[0].status == "エラー"
    assert "initial failure" in results[0].error

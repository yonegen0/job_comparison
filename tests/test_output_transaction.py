import json
from pathlib import Path

import pytest

from job_comparison_app.errors import InputChangedError
from job_pdf_tool.models import DocumentResult, FieldValue, JOB_INFO_FIELDS
from job_pdf_tool.output import (
    load_job_info_json,
    load_processing_report_json,
    prepare_job_info_document,
    serialize_job_info,
    stage_job_info_json,
)
from job_pdf_tool.pdf_reader import sha256_file
from job_pdf_tool.service import resolve_job_info_json
from job_pdf_tool.transaction import ApplyError, apply_transaction


def prepared_result(source: Path, proposed: str) -> DocumentResult:
    result = DocumentResult(source_path=source, sha256=sha256_file(source))
    result.company = FieldValue("例示株式会社", "direct", "採用企業名", 1)
    result.raw_position = FieldValue("事務スタッフ", "direct", "職種分類（中分類）", 1)
    result.resolved_position = "事務スタッフ"
    result.position_resolution = "middle_category"
    result.validate_required_identity()
    result.proposed_name = proposed
    result.status = "部分成功"
    return result


def test_job_info_json_is_deterministic_utf8_without_bom(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"; source.write_bytes(b"%PDF-test")
    result = prepared_result(source, "例示株式会社（事務スタッフ）.pdf")
    result.fields["業務内容"] = FieldValue("1行目\n2行目", "direct", "仕事内容", 1)
    document = prepare_job_info_document([result], tmp_path / "求人情報.json")
    first = serialize_job_info(document)
    second = serialize_job_info(document)
    assert first == second and first.endswith("\n")
    assert "例示株式会社" in first and "\\u4f8b" not in first
    staged = stage_job_info_json(document, tmp_path / "求人情報.json")
    try:
        assert not staged.read_bytes().startswith(b"\xef\xbb\xbf")
        assert load_job_info_json(staged).jobs[0]["業務内容"] == "1行目\n2行目"
    finally:
        staged.unlink(missing_ok=True)


def test_apply_renames_pdf_and_publishes_two_valid_json_files(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"; source.write_bytes(b"%PDF-content")
    result = prepared_result(source, "例示株式会社（事務スタッフ）.pdf")
    job = tmp_path / "求人情報.json"; report = tmp_path / "処理レポート.json"
    apply_transaction([result], tmp_path, job, report)
    target = tmp_path / result.proposed_name
    assert target.exists() and sha256_file(target) == result.sha256
    assert load_job_info_json(job).jobs[0]["新ファイル名"] == target.name
    assert load_processing_report_json(report)


def test_existing_extra_fields_and_stale_jobs_are_preserved(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"; source.write_bytes(b"%PDF-content")
    result = prepared_result(source, "例示株式会社（事務スタッフ）.pdf")
    target = tmp_path / "求人情報.json"
    fields = [*JOB_INFO_FIELDS, "応募メモ"]
    current = {field: "" for field in fields}
    current.update({"元ファイル名": "source.pdf", "新ファイル名": "old.pdf", "処理状態": "成功", "応募メモ": "保持"})
    stale = {field: "" for field in fields}
    stale.update({"元ファイル名": "missing.pdf", "処理状態": "成功", "応募メモ": "残す"})
    target.write_text(json.dumps({"format": "job-comparison/job-info", "format_version": 1,
        "field_order": fields, "jobs": [current, stale]}, ensure_ascii=False), encoding="utf-8")
    document = prepare_job_info_document([result], target)
    assert document.jobs[0]["応募メモ"] == "保持"
    assert document.stale_jobs[0]["応募メモ"] == "残す"
    assert document.stale_jobs[0]["処理状態"] == "PDF未検出"


def test_report_publish_failure_removes_new_job_json_and_restores_pdf(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.pdf"; source.write_bytes(b"%PDF-content")
    result = prepared_result(source, "例示株式会社（事務スタッフ）.pdf")
    job = tmp_path / "求人情報.json"; report = tmp_path / "処理レポート.json"
    real_rename = Path.rename
    def flaky(self: Path, target: Path):
        if Path(target) == report:
            raise OSError("simulated")
        return real_rename(self, target)
    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(ApplyError):
        apply_transaction([result], tmp_path, job, report)
    assert source.exists() and not job.exists() and not report.exists()


def test_late_output_creation_is_not_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"; source.write_bytes(b"%PDF-content")
    result = prepared_result(source, "例示株式会社（事務スタッフ）.pdf")
    job = tmp_path / "求人情報.json"; report = tmp_path / "処理レポート.json"
    document = prepare_job_info_document([result], job)
    job.write_text("external", encoding="utf-8")
    with pytest.raises(InputChangedError):
        apply_transaction([result], tmp_path, job, report,
            job_info=document,
            expected_existing={job.resolve(): False, report.resolve(): False})
    assert job.read_text(encoding="utf-8") == "external"


def test_json_auto_detection_rejects_multiple_candidates(tmp_path: Path) -> None:
    first = tmp_path / "求人情報.json"; first.write_text("{}", encoding="utf-8")
    assert resolve_job_info_json(tmp_path, None) == first.resolve()
    (tmp_path / "求人情報(1).json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="複数"):
        resolve_job_info_json(tmp_path, None)

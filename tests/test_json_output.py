import json
from pathlib import Path

import pytest

from job_pdf_tool.models import JOB_INFO_FIELDS, PROCESS_REPORT_FIELDS
from job_pdf_tool.output import load_job_info_json, load_processing_report_json


def _job_document(**updates):
    job = {field: "" for field in JOB_INFO_FIELDS}
    value = {"format": "job-comparison/job-info", "format_version": 1,
             "field_order": JOB_INFO_FIELDS, "jobs": [job]}
    value.update(updates); return value


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "求人情報.json"
    path.write_text('{"format":"a","format":"b"}', encoding="utf-8")
    with pytest.raises(ValueError, match="重複キー"):
        load_job_info_json(path)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_numbers_are_rejected(tmp_path: Path, constant: str) -> None:
    path = tmp_path / "求人情報.json"
    path.write_text(json.dumps(_job_document(), ensure_ascii=False).replace('"format_version": 1', f'"format_version": {constant}'), encoding="utf-8")
    with pytest.raises(ValueError): load_job_info_json(path)


def test_job_key_outside_field_order_is_rejected(tmp_path: Path) -> None:
    data = _job_document(); data["jobs"][0]["未登録"] = "x"
    path = tmp_path / "求人情報.json"; path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="未登録"):
        load_job_info_json(path)


def test_recruit_analysis_json_is_rejected_by_pdf_job_info_loader(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recruit-analysis.json"
    path.write_text(
        json.dumps(
            {
                "format": "job-comparison-jobs-v4",
                "provider": "jobins",
                "generated_at": "2026-09-02T00:00:00+09:00",
                "jobs": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_job_info_json(path)


def test_processing_report_page_accepts_positive_int_or_null(tmp_path: Path) -> None:
    record = {field: "" for field in PROCESS_REPORT_FIELDS}; record["ページ"] = None
    data = {"format": "job-comparison/processing-report", "format_version": 1,
            "field_order": PROCESS_REPORT_FIELDS, "reports": [record]}
    path = tmp_path / "処理レポート.json"; path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert load_processing_report_json(path)[0]["ページ"] is None
    data["reports"][0]["ページ"] = 0; path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="ページ"): load_processing_report_json(path)

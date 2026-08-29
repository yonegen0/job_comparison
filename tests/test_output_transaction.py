import csv
from pathlib import Path

import pytest

from job_comparison_app.errors import InputChangedError
from job_pdf_tool.cli import resolve_output_csv
from job_pdf_tool.models import DocumentResult, FieldValue
from job_pdf_tool.output import prepare_main_csv_table, stage_main_csv, stage_report_csv
from job_pdf_tool.pdf_reader import sha256_file
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


def test_main_csv_is_excel_compatible(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test")
    result = prepared_result(source, "例示株式会社（事務スタッフ）.pdf")
    result.fields["業務内容"] = FieldValue("1行目\n2行目", "direct", "仕事内容", 1)

    target = tmp_path / "求人情報.csv"
    staged = stage_main_csv(prepare_main_csv_table([result], target), target)
    try:
        raw = staged.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        with staged.open("r", encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        assert row["業務内容"] == "1行目\n2行目"
    finally:
        staged.unlink(missing_ok=True)


def test_apply_renames_without_changing_content(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test-content")
    result = prepared_result(source, "例示株式会社（事務スタッフ）.pdf")

    apply_transaction([result], tmp_path, tmp_path / "求人情報.csv", tmp_path / "処理レポート.csv")

    target = tmp_path / result.proposed_name
    assert not source.exists()
    assert target.exists()
    assert sha256_file(target) == result.sha256
    assert (tmp_path / "求人情報.csv").exists()
    assert (tmp_path / "処理レポート.csv").exists()


def test_apply_rolls_back_when_final_rename_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test-content")
    result = prepared_result(source, "例示株式会社（事務スタッフ）.pdf")
    real_replace = Path.replace

    def flaky_replace(self: Path, target: Path):
        if Path(target).name == result.proposed_name:
            raise OSError("simulated failure")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    with pytest.raises(ApplyError):
        apply_transaction([result], tmp_path, tmp_path / "求人情報.csv", tmp_path / "処理レポート.csv")

    assert source.exists()
    assert sha256_file(source) == result.sha256


def test_new_main_csv_is_removed_when_report_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test-content")
    result = prepared_result(source, "例示株式会社（事務スタッフ）.pdf")
    output_csv = tmp_path / "求人情報.csv"
    report_csv = tmp_path / "処理レポート.csv"
    real_rename = Path.rename

    def flaky_rename(self: Path, target: Path):
        if Path(target) == report_csv:
            raise OSError("simulated report publish failure")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)
    with pytest.raises(ApplyError):
        apply_transaction([result], tmp_path, output_csv, report_csv)

    assert not output_csv.exists()
    assert not report_csv.exists()
    assert source.exists()
    assert sha256_file(source) == result.sha256


def test_external_file_created_before_publish_is_preserved(tmp_path: Path) -> None:
    output_csv = tmp_path / "求人情報.csv"
    report_csv = tmp_path / "処理レポート.csv"

    def create_external_output() -> None:
        output_csv.write_text("external", encoding="utf-8")

    with pytest.raises(InputChangedError):
        apply_transaction(
            [],
            tmp_path,
            output_csv,
            report_csv,
            expected_existing={output_csv.resolve(): False, report_csv.resolve(): False},
            verify_outputs=create_external_output,
        )

    assert output_csv.read_text(encoding="utf-8") == "external"
    assert not report_csv.exists()


def test_invalid_identity_is_excluded_from_main_csv_and_rename(tmp_path: Path) -> None:
    valid_source = tmp_path / "valid.pdf"
    invalid_source = tmp_path / "invalid.pdf"
    valid_source.write_bytes(b"%PDF-valid")
    invalid_source.write_bytes(b"%PDF-invalid")
    valid = prepared_result(valid_source, "例示株式会社（事務スタッフ）.pdf")

    invalid = DocumentResult(source_path=invalid_source, sha256=sha256_file(invalid_source))
    invalid.raw_position = FieldValue("営業職", "direct", "職種分類（中分類）", 1)
    invalid.resolved_position = "営業職"
    invalid.position_resolution = "middle_category"
    invalid.validate_required_identity()
    invalid.proposed_name = "誤設定された名前.pdf"
    invalid.finalize_status()

    output_csv = tmp_path / "求人情報.csv"
    report_csv = tmp_path / "処理レポート.csv"
    apply_transaction([valid, invalid], tmp_path, output_csv, report_csv)

    assert (tmp_path / valid.proposed_name).exists()
    assert invalid_source.exists()
    assert sha256_file(invalid_source) == invalid.sha256
    assert not (tmp_path / invalid.proposed_name).exists()
    with output_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["会社名"] == "例示株式会社"
    with report_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        report_rows = list(csv.DictReader(handle))
    assert {row["元ファイル名"] for row in report_rows} == {"valid.pdf", "invalid.pdf"}


def test_all_invalid_results_create_header_only_main_csv(tmp_path: Path) -> None:
    source = tmp_path / "invalid.pdf"
    source.write_bytes(b"%PDF-invalid")
    result = DocumentResult(source_path=source, sha256=sha256_file(source))
    result.validate_required_identity()
    result.finalize_status()

    output_csv = tmp_path / "求人情報.csv"
    staged_main = stage_main_csv(prepare_main_csv_table([result], output_csv), output_csv)
    staged_report = stage_report_csv([result], tmp_path / "処理レポート.csv")
    try:
        with staged_main.open("r", encoding="utf-8-sig", newline="") as handle:
            assert list(csv.DictReader(handle)) == []
        with staged_report.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 2
        assert {row["対象項目"] for row in rows} == {"会社名", "募集ポジション"}
    finally:
        staged_main.unlink(missing_ok=True)
        staged_report.unlink(missing_ok=True)


def test_stale_csv_row_and_extra_column_are_preserved(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test")
    result = prepared_result(source, "例示株式会社（事務スタッフ）.pdf")
    output_csv = tmp_path / "求人情報.csv"
    fieldnames = [
        "会社名",
        "募集ポジション",
        "元ファイル名",
        "新ファイル名",
        "処理状態",
        *result.fields.keys(),
        "メモ",
    ]
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "会社名": "旧会社",
                "募集ポジション": "旧職種",
                "元ファイル名": "missing.pdf",
                "新ファイル名": "旧会社（旧職種）.pdf",
                "処理状態": "成功",
                "メモ": "残す",
            }
        )

    table = prepare_main_csv_table([result], output_csv)

    assert table.fieldnames[-1] == "メモ"
    assert len(table.stale_rows) == 1
    assert table.stale_rows[0]["処理状態"] == "PDF未検出"
    assert table.stale_rows[0]["メモ"] == "残す"


def test_invalid_existing_csv_header_is_rejected(tmp_path: Path) -> None:
    output_csv = tmp_path / "求人情報.csv"
    output_csv.write_text("会社名,募集ポジション\n例示株式会社,事務\n", encoding="utf-8-sig")

    with pytest.raises(ValueError, match="必須ヘッダー"):
        prepare_main_csv_table([], output_csv)


def test_output_csv_is_auto_detected_and_multiple_candidates_are_rejected(tmp_path: Path) -> None:
    first = tmp_path / "求人情報_営業.csv"
    first.write_text("", encoding="utf-8")
    assert resolve_output_csv(tmp_path, None) == first.resolve()

    (tmp_path / "求人情報_事務.csv").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="複数"):
        resolve_output_csv(tmp_path, None)

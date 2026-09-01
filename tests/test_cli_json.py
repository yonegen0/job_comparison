from pathlib import Path

import pytest

from job_pdf_tool.cli import build_parser, main


def test_cli_parser_exposes_json_flags_and_rejects_old_csv_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(["--input", "input", "--output-json", "jobs.json",
                              "--report-json", "report.json", "--import-main-csv", "old.csv"])
    assert args.output_json == Path("jobs.json")
    assert args.report_json == Path("report.json")
    assert args.import_main_csv == Path("old.csv")
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--input", "input", "--output-csv", "jobs.csv"])
    assert exc.value.code == 2


def test_cli_dry_run_does_not_create_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *args: [])
    code = main(["--input", str(tmp_path)])
    assert code == 0
    assert not (tmp_path / "求人情報.json").exists()
    assert not (tmp_path / "処理レポート.json").exists()


def test_cli_apply_creates_two_json_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *args: [])
    code = main(["--input", str(tmp_path), "--apply"])
    assert code == 0
    assert (tmp_path / "求人情報.json").is_file()
    assert (tmp_path / "処理レポート.json").is_file()

from __future__ import annotations

from pathlib import Path

import pytest

from job_comparison_app.errors import InputValidationError
from job_comparison_app.gui.pdf_outputs import (
    inspect_csv_conflict,
    resolve_base_csv_paths,
    resolve_numbered_csv_paths,
)


def test_default_output_is_input_output_without_creating_it(tmp_path: Path) -> None:
    paths = resolve_base_csv_paths(tmp_path, None)

    assert paths.output_dir == (tmp_path / "output").resolve()
    assert paths.output_csv == (tmp_path / "output" / "求人情報.csv").resolve()
    assert paths.report_csv == (tmp_path / "output" / "処理レポート.csv").resolve()
    assert not paths.output_dir.exists()


def test_explicit_output_directory_is_used_for_both_csvs(tmp_path: Path) -> None:
    selected = tmp_path / "日本語 出力先"
    paths = resolve_base_csv_paths(tmp_path, selected)

    assert paths.output_dir == selected.resolve()
    assert paths.output_csv.parent == paths.report_csv.parent == selected.resolve()


def test_conflict_reports_existing_files_and_first_available_pair(tmp_path: Path) -> None:
    paths = resolve_base_csv_paths(tmp_path, tmp_path)
    paths.output_csv.write_text("existing", encoding="utf-8")

    conflict = inspect_csv_conflict(paths)

    assert conflict.existing_paths == (paths.output_csv,)
    assert conflict.suggested_paths is not None
    assert conflict.suggested_paths.output_csv.name == "求人情報(1).csv"
    assert conflict.suggested_paths.report_csv.name == "処理レポート(1).csv"


def test_numbered_outputs_use_the_smallest_pair_where_both_are_free(tmp_path: Path) -> None:
    paths = resolve_base_csv_paths(tmp_path, tmp_path)
    (tmp_path / "求人情報(1).csv").write_text("used", encoding="utf-8")
    (tmp_path / "処理レポート(2).csv").write_text("used", encoding="utf-8")
    (tmp_path / "求人情報(3).csv").write_text("used", encoding="utf-8")

    numbered = resolve_numbered_csv_paths(paths)

    assert numbered.output_csv.name == "求人情報(4).csv"
    assert numbered.report_csv.name == "処理レポート(4).csv"


def test_numbered_outputs_fill_a_gap(tmp_path: Path) -> None:
    paths = resolve_base_csv_paths(tmp_path, tmp_path)
    (tmp_path / "求人情報(2).csv").write_text("used", encoding="utf-8")

    numbered = resolve_numbered_csv_paths(paths)

    assert numbered.output_csv.name == "求人情報(1).csv"
    assert numbered.report_csv.name == "処理レポート(1).csv"


def test_output_directory_must_not_be_a_file(tmp_path: Path) -> None:
    selected = tmp_path / "output-file"
    selected.write_text("not a directory", encoding="utf-8")

    with pytest.raises(InputValidationError) as raised:
        resolve_base_csv_paths(tmp_path, selected)

    assert raised.value.code == "INPUT001"


def test_csv_target_must_not_be_a_directory(tmp_path: Path) -> None:
    (tmp_path / "求人情報.csv").mkdir()

    with pytest.raises(InputValidationError) as raised:
        resolve_base_csv_paths(tmp_path, tmp_path)

    assert raised.value.code == "INPUT001"

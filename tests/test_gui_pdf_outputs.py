from pathlib import Path

import pytest

from job_comparison_app.gui.pdf_outputs import (
    discover_legacy_main_csv_candidates,
    inspect_output_conflict,
    resolve_base_output_paths,
    resolve_numbered_output_paths,
)


def test_default_output_is_input_output_and_resolver_does_not_create_it(tmp_path: Path) -> None:
    paths = resolve_base_output_paths(tmp_path, None)
    assert paths.output_dir == (tmp_path / "output").resolve()
    assert paths.job_info_json.name == "求人情報.json"
    assert paths.report_json.name == "処理レポート.json"
    assert not paths.output_dir.exists()


def test_explicit_output_and_conflict_suggest_common_number(tmp_path: Path) -> None:
    selected = tmp_path / "日本語 出力"
    selected.mkdir()
    paths = resolve_base_output_paths(tmp_path, selected)
    paths.job_info_json.write_text("{}", encoding="utf-8")
    (selected / "処理レポート(1).json").write_text("{}", encoding="utf-8")
    conflict = inspect_output_conflict(paths)
    assert conflict.existing_paths == (paths.job_info_json,)
    assert conflict.suggested_paths is not None
    assert conflict.suggested_paths.job_info_json.name == "求人情報(2).json"
    assert conflict.suggested_paths.report_json.name == "処理レポート(2).json"


def test_numbering_uses_first_pair_with_both_names_free(tmp_path: Path) -> None:
    paths = resolve_base_output_paths(tmp_path, tmp_path)
    (tmp_path / "求人情報(1).json").write_text("{}", encoding="utf-8")
    (tmp_path / "処理レポート(3).json").write_text("{}", encoding="utf-8")
    numbered = resolve_numbered_output_paths(paths)
    assert numbered.job_info_json.name == "求人情報(2).json"
    assert numbered.report_json.name == "処理レポート(2).json"


def test_output_directory_must_not_be_a_file(tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(Exception, match="通常のフォルダー"):
        resolve_base_output_paths(tmp_path, target)


def test_legacy_csv_exact_output_candidate_has_priority(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    preferred = output / "求人情報.csv"
    preferred.write_text("header", encoding="utf-8")
    (tmp_path / "求人情報_old.csv").write_text("header", encoding="utf-8")
    assert discover_legacy_main_csv_candidates(tmp_path, output) == (preferred.resolve(),)

from __future__ import annotations

from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).parents[1]


def test_release_inputs_exist() -> None:
    for name in (
        "JobComparisonTool.spec",
        "build_release.bat",
        "requirements-win11.lock",
        "requirements-build-win11.lock",
        "release_checks.ps1",
        "求人比較ツールを起動.bat",
        "はじめにお読みください.txt",
    ):
        assert (ROOT / name).is_file(), name


def test_spec_is_windowed_onedir_and_bundles_templates() -> None:
    text = (ROOT / "JobComparisonTool.spec").read_text(encoding="utf-8")
    assert 'name="JobComparisonTool"' in text
    assert "console=False" in text
    assert "upx=False" in text
    assert "COLLECT(" in text
    assert "report.html" in text
    assert "report.css" in text


def test_project_and_package_versions_match() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    init_text = (ROOT / "job_comparison_app" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', init_text, re.MULTILINE)
    assert match is not None
    assert project["project"]["version"] == "0.1.2"
    assert match.group(1) == project["project"]["version"]
    assert project["project"]["dependencies"] == ["pypdf==6.16.1"]


def test_build_dependencies_are_pinned() -> None:
    build = (ROOT / "requirements-build-win11.lock").read_text(encoding="utf-8")
    runtime = (ROOT / "requirements-win11.lock").read_text(encoding="utf-8")
    assert "pyinstaller==6.22.2" in build
    assert "pytest==8.3.5" in build
    assert "pypdf==6.16.1" in runtime


def test_launcher_lookup_encoding_and_line_endings() -> None:
    raw = (ROOT / "求人比較ツールを起動.bat").read_bytes()
    text = raw.decode("utf-8")
    adjacent = 'set "candidate_exe=%candidate_dir%JobComparisonTool.exe"'
    current = 'set "candidate_dir=%launcher_dir%release\\current\\"'
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.count(b"\n") == raw.count(b"\r\n")
    assert text.index(adjacent) < text.index(current)
    assert 'if not exist "%candidate_dir%_internal\\."' in text
    assert "dist\\JobComparisonTool" not in text
    assert "pause" in text
    assert "exit /b 1" in text


def test_release_contract_excludes_user_data() -> None:
    checks = (ROOT / "release_checks.ps1").read_text(encoding="utf-8")
    for token in (
        "release\\current",
        "failed-builds",
        "report.html",
        "report.css",
        '".pdf", ".csv", ".md", ".json"',
    ):
        assert token in checks
    readme = (ROOT / "はじめにお読みください.txt").read_text(encoding="utf-8")
    assert "求人比較ツール 0.1.2" in readme


def test_release_zip_has_allowlisted_files_at_archive_root() -> None:
    checks = (ROOT / "release_checks.ps1").read_text(encoding="utf-8")

    assert "$packageDirectory," in checks
    assert "Assert-Package -Path $verificationRoot" in checks
    assert "$packageContainer" not in checks
    assert "$verifiedPackage" not in checks


def test_binary_outputs_are_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in ("dist/", "release/", "build/", ".venv-build/"):
        assert entry in ignore

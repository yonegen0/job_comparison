from __future__ import annotations

from pathlib import Path
import re
import struct
import tomllib


ROOT = Path(__file__).parents[1]
ICON_PATH = ROOT / "job_comparison_app" / "assets" / "JobComparisonTool.ico"


def test_release_inputs_exist() -> None:
    for name in (
        "JobComparisonTool.spec",
        "build_release.bat",
        "requirements-win11.lock",
        "requirements-build-win11.lock",
        "release_checks.ps1",
        "求人比較ツールを起動.bat",
        "はじめにお読みください.txt",
        "docs/user-guide/求人比較ツール_使い方.md",
        "tools/user_guide_capture/capture_scenes.py",
        "tools/user_guide_capture/capture_window.ps1",
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
    assert '"pdf_job_exporter"' in text
    assert '"recruit_job_tool"' in text
    assert '"_cffi_backend"' in text
    assert "cryptography.hazmat.bindings._rust" in text
    assert '("job_comparison_app/assets/JobComparisonTool.ico", "job_comparison_app/assets")' in text
    assert 'icon="job_comparison_app/assets/JobComparisonTool.ico"' in text


def test_application_icon_is_a_multisize_windows_icon() -> None:
    raw = ICON_PATH.read_bytes()
    reserved, image_type, image_count = struct.unpack_from("<HHH", raw)
    assert (reserved, image_type) == (0, 1)
    assert image_count >= 9

    sizes: set[tuple[int, int]] = set()
    for index in range(image_count):
        width, height = struct.unpack_from("<BB", raw, 6 + index * 16)
        sizes.add((width or 256, height or 256))

    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes


def test_project_and_package_versions_match() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    init_text = (ROOT / "job_comparison_app" / "__init__.py").read_text(encoding="utf-8")
    recruit_init_text = (ROOT / "recruit_job_tool" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', init_text, re.MULTILINE)
    recruit_match = re.search(r'^__version__ = "([^"]+)"$', recruit_init_text, re.MULTILINE)
    assert match is not None
    assert recruit_match is not None
    assert project["project"]["version"] == "0.4.3"
    assert match.group(1) == project["project"]["version"]
    assert recruit_match.group(1) == project["project"]["version"]
    assert project["project"]["gui-scripts"] == {
        "JobComparisonTool": "job_comparison_app.gui.__main__:main"
    }
    assert project["project"]["dependencies"] == [
        "pypdf[crypto]==6.16.1", "cryptography==50.0.1", "cffi==2.1.1", "pycparser==3.0"
    ]
    assert project["tool"]["setuptools"]["package-data"]["job_comparison_app"] == [
        "assets/*.ico"
    ]
    assert "recruit_job_tool*" in project["tool"]["setuptools"]["packages"]["find"]["include"]


def test_build_dependencies_are_pinned() -> None:
    build = (ROOT / "requirements-build-win11.lock").read_text(encoding="utf-8")
    runtime = (ROOT / "requirements-win11.lock").read_text(encoding="utf-8")
    assert "pyinstaller==6.22.2" in build
    assert "pytest==8.3.5" in build
    assert "pypdf[crypto]==6.16.1" in runtime
    assert "cryptography==50.0.1" in runtime
    assert "cffi==2.1.1" in runtime
    assert "pycparser==3.0" in runtime


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
        '".pdf", ".csv", ".json", ".jsonl"',
        '$guideDirectoryName',
        '$guideMarkdownName',
        '$guideImageNames',
        '".md" -and $_.FullName -ine $allowedGuideMarkdown',
        '".png" -and $_.FullName -notin $allowedGuidePngs',
    ):
        assert token in checks
    readme = (ROOT / "はじめにお読みください.txt").read_text(encoding="utf-8")
    assert "求人比較ツール 0.4.3" in readme
    assert "JoBins／CareerBankごとにアプリ終了まで保持" in readme
    assert "使用済みHTMLの選択だけを解除" in readme
    assert "取り込み完了後は安全のため「置換」へ戻ります" not in readme


def test_release_contract_bundles_only_the_allowlisted_user_guide() -> None:
    checks = (ROOT / "release_checks.ps1").read_text(encoding="utf-8")
    assert '$guideSource = Join-Path $repoRoot "docs\\user-guide"' in checks
    assert '$guideDirectoryName = "使い方ガイド"' in checks
    assert 'Copy-Item -LiteralPath $guideSource' in checks
    assert 'Assert-UserGuide -Path $guideSource' in checks
    assert 'Assert-UserGuide -Path $guide' in checks
    for name in (
        "00-workflow-overview.png",
        "01-start-candidate.png",
        "02-candidate-example.png",
        "03-recruit-initial-setup.png",
        "04-recruit-ready.png",
        "05-replace-confirmation.png",
        "06-general-comparison-confirmation.png",
        "07-candidate-analysis-complete.png",
        "08-pdf-organize-preview.png",
        "09-pdf-text-preview.png",
        "10-report-preview.png",
    ):
        assert checks.count(f'"{name}"') == 1


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

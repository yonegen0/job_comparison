from dataclasses import replace
from pathlib import Path

import pytest

from job_comparison_app.errors import InputChangedError
from job_comparison_app.gui.pdf_outputs import resolve_base_output_paths, inspect_output_conflict
from job_comparison_app.pdf_comparison_guide import COMPARISON_GUIDE_NAME, render_pdf_comparison_guide
from job_pdf_tool.service import PdfOrganizationRequest, prepare_pdf_organization, commit_pdf_organization
from job_pdf_tool.transaction import ApplyError
from recruit_job_tool.candidate_profile import CandidateProfile
from tests.test_output_transaction import prepared_result


def profile(**values):
    return CandidateProfile(**dict(dict(age="34歳", gender="回答しない", education="その他",
                                       work_history="独自の職歴サンプル", qualifications="独自の資格サンプル"), **values))


@pytest.mark.parametrize("candidate", [None, profile(), profile(work_history="", qualifications="")])
def test_renderer_is_deterministic_and_preserves_reference_structure(candidate):
    content = render_pdf_comparison_guide(job_info_filename="求人情報(2).json", candidate_profile=candidate)
    assert content == render_pdf_comparison_guide(job_info_filename="求人情報(2).json", candidate_profile=candidate)
    assert "\r" not in content and "\x00" not in content and content.endswith("\n")
    assert "{{PDF_GUIDE_" not in content
    for heading in ["## 比較一覧", "## ヘッダ", "## ボディ", "# 整合性チェック"]:
        assert heading in content
    assert "source_file" in content and "新ファイル名" in content
    if candidate is None:
        assert "個人との適合度・応募可否は判定せず" in content
        assert "年齢との相性" not in content
        assert "独自の" not in content
    else:
        assert "34歳" in content and "回答しない" in content and "その他" in content
        assert (candidate.work_history or "記載なし") in content


def test_profile_cannot_create_headings_html_or_fences():
    content = render_pdf_comparison_guide(job_info_filename="求人情報.json", candidate_profile=profile(
        work_history="# 役割変更\n```\n<script>evil</script>\n{{PDF_GUIDE_PROFILE}}", qualifications=""))
    assert "\n# 役割変更" not in content and "\n```" not in content
    assert "<script>" not in content and "&lt;script&gt;" in content
    assert "> \\# 役割変更" in content and "> 記載なし" in content


@pytest.mark.parametrize("template", ["", "{{PDF_GUIDE_PROFILE}}" * 2, "\x00"])
def test_invalid_template_fails_before_output(monkeypatch, template):
    class Resource:
        def joinpath(self, _): return self
        def read_text(self, **_): return template
    monkeypatch.setattr("job_comparison_app.pdf_comparison_guide.files", lambda _: Resource())
    with pytest.raises(ValueError, match="テンプレート"):
        render_pdf_comparison_guide(job_info_filename="求人情報.json", candidate_profile=None)


def request(root, candidate=None):
    output = root / "output"
    return PdfOrganizationRequest(root, job_info_json=output / "求人情報.json",
                                  report_json=output / "処理レポート.json",
                                  comparison_guide_path=output / COMPARISON_GUIDE_NAME,
                                  candidate_profile=candidate)


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_prepare_has_no_side_effects_commit_writes_three_files_and_clears_old_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *_: [])
    req = request(tmp_path, profile())
    plan = prepare_pdf_organization(req)
    assert not req.job_info_json.parent.exists()
    assert "独自の" not in repr(plan) and "独自の" not in repr(req)
    commit_pdf_organization(plan)
    assert "独自の職歴サンプル" in req.comparison_guide_path.read_text(encoding="utf-8")
    for path in [req.job_info_json, req.report_json]:
        assert "独自の" not in path.read_text(encoding="utf-8")
    commit_pdf_organization(prepare_pdf_organization(replace(req, candidate_profile=None)))
    assert "独自の" not in req.comparison_guide_path.read_text(encoding="utf-8")
    assert sorted(p.suffix for p in req.job_info_json.parent.iterdir()) == [".json", ".json", ".md"]


def test_legacy_api_still_outputs_only_json(tmp_path, monkeypatch):
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *_: [])
    req = replace(request(tmp_path), comparison_guide_path=None)
    commit_pdf_organization(prepare_pdf_organization(req))
    assert not (req.job_info_json.parent / COMPARISON_GUIDE_NAME).exists()
    with pytest.raises(ValueError, match="出力先"):
        prepare_pdf_organization(replace(req, candidate_profile=profile()))


@pytest.mark.parametrize("change", ["create", "edit", "delete"])
def test_changed_guide_is_rejected_without_changing_other_files(tmp_path, monkeypatch, change):
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *_: [])
    req = request(tmp_path)
    req.comparison_guide_path.parent.mkdir()
    if change != "create":
        req.comparison_guide_path.write_text("old", encoding="utf-8")
    plan = prepare_pdf_organization(req)
    if change == "delete":
        req.comparison_guide_path.unlink()
    else:
        req.comparison_guide_path.write_text("new", encoding="utf-8")
    before = snapshot(tmp_path)
    with pytest.raises(InputChangedError):
        commit_pdf_organization(plan)
    assert snapshot(tmp_path) == before


def test_guide_only_conflict_reserves_common_suffix_and_json_reference(tmp_path, monkeypatch):
    paths = resolve_base_output_paths(tmp_path, None)
    paths.output_dir.mkdir()
    paths.comparison_guide_path.write_text("old", encoding="utf-8")
    (paths.output_dir / "求人PDFから比較資料に(1).md").write_text("old1", encoding="utf-8")
    conflict = inspect_output_conflict(paths)
    numbered = conflict.suggested_paths
    assert conflict.existing_paths == (paths.comparison_guide_path,)
    assert all("(2)" in p.name for p in numbered.targets)
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *_: [])
    plan = prepare_pdf_organization(PdfOrganizationRequest(tmp_path, job_info_json=numbered.job_info_json,
        report_json=numbered.report_json, comparison_guide_path=numbered.comparison_guide_path))
    assert "求人情報\\(2\\)\\.json" in plan.comparison_guide_content


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["stage", "stage_verify", "pdf", "job", "report", "guide", "verify"])
def test_failures_restore_pdf_and_all_outputs(tmp_path, monkeypatch, existing, failure):
    req = request(tmp_path, profile())
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *_: [])
    if existing:
        commit_pdf_organization(prepare_pdf_organization(req))
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-synthetic-data")
    result = prepared_result(source, "renamed.pdf")
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *_: [result])
    plan = prepare_pdf_organization(req)
    before = snapshot(tmp_path)
    if failure == "stage":
        monkeypatch.setattr("job_pdf_tool.transaction.stage_comparison_guide", lambda *_: (_ for _ in ()).throw(OSError("stage failure")))
    elif failure in {"stage_verify", "verify"}:
        from job_pdf_tool import transaction
        original = transaction.verify_comparison_guide
        def verify(path, content):
            if failure == "stage_verify" or path == req.comparison_guide_path:
                raise OSError("verify failure")
            return original(path, content)
        monkeypatch.setattr(transaction, "verify_comparison_guide", verify)
    else:
        target = {"pdf": tmp_path / "renamed.pdf", "job": req.job_info_json,
                  "report": req.report_json, "guide": req.comparison_guide_path}[failure]
        method = "replace" if failure == "pdf" else "rename"
        original = getattr(Path, method)
        def move(self, dest):
            if Path(dest) == target:
                raise OSError("publish failure")
            return original(self, dest)
        monkeypatch.setattr(Path, method, move)
    with pytest.raises(ApplyError):
        commit_pdf_organization(plan)
    assert snapshot(tmp_path) == before


def test_guide_cannot_replace_template_or_other_outputs(tmp_path, monkeypatch):
    from job_comparison_app.pdf_comparison_guide import template_path
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *_: [])
    for target in [template_path(), tmp_path / "source.pdf", request(tmp_path).job_info_json]:
        with pytest.raises(ValueError):
            prepare_pdf_organization(replace(request(tmp_path), comparison_guide_path=target))

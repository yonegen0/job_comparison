from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from job_comparison_app.gui.controller import Controller as PdfController
from job_comparison_app.gui.pdf_outputs import resolve_base_output_paths, inspect_output_conflict, resolve_numbered_output_paths
from job_comparison_app.gui.view import TkView
from job_comparison_app.pdf_comparison_guide import COMPARISON_GUIDE_NAME, render_pdf_comparison_guide
from job_pdf_tool.service import PdfOrganizationRequest, prepare_pdf_organization, commit_pdf_organization
from job_pdf_tool.transaction import ApplyError
from recruit_job_tool.analysis_prompt.csv_input import load_prompt_jobs, load_prompt_jobs_from_bytes
from recruit_job_tool.analysis_prompt.renderer import render_analysis_guide, render_candidate_analysis_guide
from recruit_job_tool.analysis_prompt.service import generate_analysis_files
from recruit_job_tool.app_paths import AppPaths
from recruit_job_tool.application import RecruitJobApplication
from recruit_job_tool.csv_exporter import CsvExporter, serialize_jobs_csv
from recruit_job_tool.errors import CsvExportError, PromptInputError, PromptOutputError
from recruit_job_tool.gui.controller import Controller as RecruitController, CandidateProfileDecision, CANDIDATE_AI_REQUEST_TEXT, GENERAL_AI_REQUEST_TEXT
from recruit_job_tool.models import AnalysisOutputMode, JobSource
from recruit_job_tool.workflow import ImportMode
from tests import test_services_gui as pdf_support
from tests.recruit_feature import test_gui_controller as recruit_support
from tests.recruit_feature.support import BASE_TIME, make_job, make_candidate_profile
from tests.test_output_transaction import prepared_result


def file_state(path):
    return path.read_bytes(), path.stat().st_mtime_ns


def valid_job(source=JobSource.JOBINS, index=123, **kwargs):
    url = (f"https://jobins.jp/agent/jobs/{index}" if source is JobSource.JOBINS
           else f"https://careerbank-jobsearch.com/jobs/{index}")
    return make_job(source=source, source_job_id=str(index), source_url=url, **kwargs)


@pytest.mark.parametrize("source", list(JobSource))
def test_memory_conversion_matches_csv_file_and_preserves_complex_text(tmp_path, source):
    jobs = [valid_job(source, fields={
        "job_title": "日本語の求人\n引用\"と,カンマ",
        "required_conditions": "'経験\n資格不問",
        "preferred_conditions": "歓迎：\"営業\"",
    }), valid_job(source, index=124)]
    raw = serialize_jobs_csv(source, jobs)
    path = CsvExporter(tmp_path).export(source, jobs)
    assert raw == path.read_bytes()
    assert load_prompt_jobs_from_bytes(raw, expected_source=source) == load_prompt_jobs(path, expected_source=source)
    memory = generate_analysis_files(input_jobs=jobs, json_output_path=tmp_path / "memory.json",
                                     provider=source, include_guide=False, generated_at=BASE_TIME)
    disk = generate_analysis_files(input_path=path, json_output_path=tmp_path / "disk.json",
                                   provider=source, include_guide=False, generated_at=BASE_TIME)
    assert memory.json_path.read_bytes() == disk.json_path.read_bytes()


@pytest.mark.parametrize("source", list(JobSource))
def test_application_json_only_never_exports_csv_or_touches_markdown(tmp_path, monkeypatch, source):
    paths = AppPaths.from_root((tmp_path / "state").resolve())
    paths.initialize()
    app = RecruitJobApplication(paths, clock=lambda: BASE_TIME)
    html = tmp_path / f"{source.value.lower()}_search_results.html"
    shutil.copyfile(Path(__file__).parent / "recruit_feature" / "fixtures" / html.name, html)
    imported = app.import_saved_html(provider=source, input_path=html, mode=ImportMode.REPLACE)
    output = imported.csv_path.parent
    guide = output / "existing.md"
    guide.write_text("既存の指示書", encoding="utf-8")
    before = {p: file_state(p) for p in output.iterdir()}
    monkeypatch.setattr(app._workflow, "export_current", lambda *a, **kw: pytest.fail("CSV export was called"))
    first = app.create_analysis_files(provider=source, include_guide=False)
    second = app.create_analysis_files(provider=source, include_guide=False)
    assert first.guide_path is None and first.guide_character_count is None and not first.warnings
    assert first.json_path != second.json_path
    assert {p: file_state(p) for p in before} == before
    assert set(output.iterdir()) - set(before) == {first.json_path, second.json_path}
    payload = json.loads(first.json_path.read_text(encoding="utf-8"))
    assert payload["format"] == "job-comparison-jobs-v4" and len(payload["jobs"]) == imported.current_count
    assert "candidate_profile" not in payload


@pytest.mark.parametrize("invalid", ["empty", "too_many", "duplicate", "provider", "url", "salary"])
def test_invalid_memory_jobs_do_not_create_output(tmp_path, invalid):
    job = valid_job()
    jobs = [job]
    if invalid == "empty": jobs = []
    elif invalid == "too_many": jobs = [valid_job(index=i) for i in range(101)]
    elif invalid == "duplicate": jobs = [job, job]
    elif invalid == "provider": jobs = [valid_job(JobSource.CAREERBANK)]
    elif invalid == "url": jobs = [replace(job, source_url="https://example.invalid/job/123")]
    elif invalid == "salary": jobs = [replace(job, salary_min=8_000_000, salary_max=4_000_000)]
    target = tmp_path / "output" / "jobs.json"
    with pytest.raises((PromptInputError, CsvExportError)):
        generate_analysis_files(input_jobs=jobs, json_output_path=target, provider=JobSource.JOBINS, include_guide=False)
    assert not target.parent.exists()


@pytest.mark.parametrize("both", [False, True])
def test_exactly_one_analysis_input_is_required(tmp_path, both):
    with pytest.raises(PromptInputError):
        generate_analysis_files(input_path=tmp_path / "jobs.csv" if both else None,
                                input_jobs=[valid_job()] if both else None,
                                json_output_path=tmp_path / "jobs.json", provider=JobSource.JOBINS, include_guide=False)
    assert not (tmp_path / "jobs.json").exists()


def recruit_controller():
    view, app = recruit_support.FakeView(), recruit_support.FakeApplication()
    control = RecruitController(view=view, file_picker=recruit_support.FakePicker(None), application=app,
                                output_folder_opener=recruit_support.FakeOpener(), task_runner=recruit_support.ImmediateRunner())
    control.start()
    return control, view, app


@pytest.mark.parametrize("source", list(JobSource))
def test_json_only_controller_skips_candidate_confirmation_and_tracks_mode(source):
    control, view, app = recruit_controller()
    control.select_tab(source)
    control.change_output_mode(AnalysisOutputMode.JSON_ONLY)
    view.request_candidate_profile = lambda: pytest.fail("candidate confirmation was called")
    control.generate_analysis()
    spec = view.dialogs[-1]
    assert not spec.guide_requested and not spec.candidate_applied and spec.guide_basename is None
    assert spec.title == "求人JSONを作成しました"
    assert app.calls[-1][2] is None
    control.copy_ai_request()
    assert view.copied == GENERAL_AI_REQUEST_TEXT
    other = JobSource.CAREERBANK if source is JobSource.JOBINS else JobSource.JOBINS
    assert control.model.providers[other].output_mode is AnalysisOutputMode.JSON_WITH_GUIDE
    control.select_tab(other)
    control.select_tab(source)
    assert control.model.providers[source].output_mode is AnalysisOutputMode.JSON_ONLY


def test_copy_request_is_provider_specific_and_failed_generation_keeps_previous(monkeypatch):
    control, view, app = recruit_controller()
    view.candidate_decision = CandidateProfileDecision(True, make_candidate_profile())
    control.generate_analysis()
    control.select_tab(JobSource.CAREERBANK)
    control.change_output_mode(AnalysisOutputMode.JSON_ONLY)
    control.generate_analysis()
    control.copy_ai_request()
    assert view.copied == GENERAL_AI_REQUEST_TEXT
    control.select_tab(JobSource.JOBINS)
    control.copy_ai_request()
    assert view.copied == CANDIDATE_AI_REQUEST_TEXT
    control.change_output_mode(AnalysisOutputMode.JSON_ONLY)
    def fail(**kwargs): raise PromptOutputError("failed")
    monkeypatch.setattr(app, "create_analysis_files", fail)
    control.generate_analysis()
    control.copy_ai_request()
    assert view.copied == CANDIDATE_AI_REQUEST_TEXT


def test_recruit_output_mode_cannot_change_during_generation():
    control, view, app = recruit_controller()
    control.change_output_mode(AnalysisOutputMode.JSON_ONLY)
    runner = recruit_support.PendingRunner()
    control.task_runner = runner
    control.generate_analysis()
    assert control.model.busy
    control.change_output_mode(AnalysisOutputMode.JSON_WITH_GUIDE)
    assert control.model.providers[JobSource.JOBINS].output_mode is AnalysisOutputMode.JSON_ONLY


def test_pdf_json_only_ignores_existing_guide_for_conflicts_and_numbering(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / COMPARISON_GUIDE_NAME).mkdir()  # A non-file guide must also be ignored.
    (output / "求人PDFから比較資料に(1).md").write_text("existing", encoding="utf-8")
    paths = resolve_base_output_paths(tmp_path, None, include_guide=False)
    assert paths.comparison_guide_path is None and len(paths.targets) == 2
    assert not inspect_output_conflict(paths).existing_paths
    paths.job_info_json.write_text("{}", encoding="utf-8")
    numbered = resolve_numbered_output_paths(paths)
    assert numbered.comparison_guide_path is None
    assert [p.name for p in numbered.targets] == ["求人情報(1).json", "処理レポート(1).json"]


def test_pdf_json_only_controller_skips_profile_and_clears_mode_conflict_selection(tmp_path):
    req = pdf_support.request(tmp_path, tmp_path / "output")
    view, app = pdf_support.FakeView(req), pdf_support.RecordingApplication()
    view.request_candidate_profile = lambda: pytest.fail("candidate confirmation was called")
    control = PdfController(view=view, application=app, output_opener=pdf_support.FakeOpener(), task_runner=pdf_support.ImmediateRunner())
    control.preview_pdf()
    control.apply_pdf()
    assert app.commits == 1 and app.requests[-1].candidate_profile is None
    assert app.requests[-1].comparison_guide_path is None
    assert "比較指示書" not in view.notices[-1][2]
    assert not (tmp_path / "output").exists()
    control.invalidate_pdf()
    assert control.model.pdf_plan is None and control.model.pdf_result is None
    assert control._pdf_choice_key is None and control._migration_key is None


@pytest.mark.parametrize("failure", [None, "job", "report", "pdf"])
@pytest.mark.parametrize("existing", [False, True])
def test_pdf_json_only_renames_and_restores_without_touching_existing_guide(tmp_path, monkeypatch, failure, existing):
    output = tmp_path / "output"
    output.mkdir()
    guide = output / COMPARISON_GUIDE_NAME
    guide.write_text("existing", encoding="utf-8")
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-synthetic")
    result = prepared_result(source, "renamed.pdf")
    req = pdf_support.request(tmp_path, output)
    if existing:
        monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *_: [])
        commit_pdf_organization(prepare_pdf_organization(req))
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *_: [result])
    plan = prepare_pdf_organization(req)
    guide_state = file_state(guide)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    if failure:
        target = {"job": req.job_info_json, "report": req.report_json, "pdf": tmp_path / "renamed.pdf"}[failure]
        method = "replace" if failure == "pdf" else "rename"
        original = getattr(Path, method)
        def fail_publish(self, dest):
            if Path(dest) == target: raise OSError("publish failure")
            return original(self, dest)
        monkeypatch.setattr(Path, method, fail_publish)
        with pytest.raises(ApplyError): commit_pdf_organization(plan)
        assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    else:
        # Unrelated guide edits after preparation do not invalidate a JSON-only plan.
        guide.write_text("changed independently", encoding="utf-8")
        guide_state = file_state(guide)
        commit_pdf_organization(plan)
        assert (tmp_path / "renamed.pdf").exists() and not source.exists()
        assert json.loads(req.job_info_json.read_text(encoding="utf-8"))["jobs"]
        assert req.report_json.is_file()
    assert file_state(guide) == guide_state


def test_pdf_output_mode_updates_request_invalidates_preview_and_disables_while_busy(tk_window, tmp_path, monkeypatch):
    view = TkView(tk_window)
    monkeypatch.setattr(view, "post", lambda callback: callback())
    view.pdf_inputs["jobins"]["input_dir"].set(str(tmp_path))
    app = pdf_support.RecordingApplication()
    control = PdfController(view=view, application=app, output_opener=pdf_support.FakeOpener(), task_runner=pdf_support.ImmediateRunner())
    view.bind_controller(control)
    control.preview_pdf()
    assert control.model.pdf_plan.comparison_guide_path is not None
    view.pdf_inputs["jobins"]["output_mode"].set(AnalysisOutputMode.JSON_ONLY.value)
    assert control.model.pdf_plan is None and control._pdf_choice_key is None
    assert view.current_pdf_request().comparison_guide_path is None
    assert "比較指示Markdown" not in view.pdf_widgets["jobins"]["output_hint"].get()
    control.preview_pdf()
    assert control.model.pdf_plan.comparison_guide_path is None
    view.render(replace(control.model, busy=True))
    for widget in view._input_widgets:
        assert str(widget.cget("state")) == "disabled"


def test_recruit_radio_buttons_change_each_provider_mode_and_disable_while_busy(tk_window):
    view = TkView(tk_window)
    panel = view.recruit_panel
    control, _, _ = recruit_controller()
    control.view = panel
    panel.bind_controller(control)
    panel.render(control.model)
    for source in JobSource:
        panel._output_modes[source].set(AnalysisOutputMode.JSON_ONLY.value)
        panel._output_mode_changed(source)
        assert control.model.providers[source].output_mode is AnalysisOutputMode.JSON_ONLY
        assert panel._controls[source]["output_json"].instate(["selected"])
    panel.set_host_busy(True)
    for source in JobSource:
        assert panel._controls[source]["output_json"].instate(["disabled"])
        assert panel._controls[source]["output_guide"].instate(["disabled"])


def test_generated_guides_explain_pending_questions_and_link_contract():
    general = render_analysis_guide()
    candidate = render_candidate_analysis_guide(candidate_profile=make_candidate_profile())
    for content in (general, candidate):
        assert "上位候補のリンク一覧" in content and "要確認求人のリンク一覧" in content
        assert "リンクなし" in content and "source_url" in content and "重複掲載しません" in content
        assert content.rfind("## 確認質問") > content.rfind("## 求人リンク一覧")
    assert "job-comparison-v10" in general and "job-comparison-v11" in candidate
    assert "応募者個人の情報を質問" in general
    for rule in ("全件が保留でも分析不能とせず", "記載なし", "未回答", "再計算", "求人リンク一覧も更新", "曖昧さだけを理由に除外"):
        assert rule in candidate
    for profile in (None, make_candidate_profile()):
        pdf = render_pdf_comparison_guide(job_info_filename="求人情報.json", candidate_profile=profile)
        assert "確認質問" in pdf and "必要な10求人" in pdf
        assert "求人リンク一覧" not in pdf
        assert "可能性が高い求人は、原則として順位を下げ" not in pdf



def test_pdf_json_mode_is_kept_separately_for_jobins_and_circus(tk_window, tmp_path):
    view = TkView(tk_window)
    tk_window.deiconify()
    tk_window.update()
    assert all(
        view.pdf_inputs[provider]["output_mode"].get() == AnalysisOutputMode.JSON_WITH_GUIDE.value
        for provider in ("jobins", "circus")
    )
    view.pdf_inputs["jobins"]["input_dir"].set(str(tmp_path))
    view.pdf_inputs["jobins"]["output_mode"].set(AnalysisOutputMode.JSON_ONLY.value)
    assert view.current_pdf_request().comparison_guide_path is None
    view.pdf_provider_notebook.select(1)
    tk_window.update()
    assert view.current_pdf_request().provider == "circus"
    assert view.current_pdf_request().comparison_guide_path is not None
    view.pdf_inputs["circus"]["output_mode"].set(AnalysisOutputMode.JSON_ONLY.value)
    assert view.current_pdf_request().comparison_guide_path is None
    view.pdf_provider_notebook.select(0)
    tk_window.update()
    assert view.pdf_inputs["jobins"]["output_mode"].get() == AnalysisOutputMode.JSON_ONLY.value
    assert view.current_pdf_request().comparison_guide_path is None

from dataclasses import replace

import pytest

from job_comparison_app.gui.controller import Controller
from job_comparison_app.gui.view import TkView
from recruit_job_tool.gui.controller import CandidateProfileDecision
from tests import test_services_gui as support
from tests.test_pdf_comparison_guide import profile
from job_comparison_app.pdf_comparison_guide import COMPARISON_GUIDE_NAME


def controller(tmp_path):
    req = replace(support.request(tmp_path, tmp_path / "output"),
                  comparison_guide_path=tmp_path / "output" / COMPARISON_GUIDE_NAME)
    view = support.FakeView(req)
    app = support.RecordingApplication()
    control = Controller(view=view, application=app, output_opener=support.FakeOpener(), task_runner=support.ImmediateRunner())
    return control, view, app


def test_preview_never_requests_conditions_and_apply_uses_latest_values(tmp_path):
    control, view, app = controller(tmp_path)
    decisions = []
    def resolve():
        decisions.append(True)
        return CandidateProfileDecision(proceed=True, profile=profile(work_history="最新の入力"))
    view.request_candidate_profile = resolve
    control.preview_pdf()
    assert not decisions and app.requests[0].candidate_profile is None
    assert not (tmp_path / "output").exists()
    control.apply_pdf()
    assert len(decisions) == 1 and app.commits == 1
    assert app.requests[-1].candidate_profile.work_history == "最新の入力"
    assert "最新の入力" not in str(view.notices)
    assert "比較指示書" in view.notices[-1][2]


def test_cancel_profile_never_starts_worker_and_general_choice_is_not_cached(tmp_path):
    control, view, app = controller(tmp_path)
    view.request_candidate_profile = lambda: CandidateProfileDecision(proceed=False)
    control.apply_pdf()
    assert not app.requests and not app.commits and not (tmp_path / "output").exists()
    count = []
    def general():
        count.append(True)
        return CandidateProfileDecision(proceed=True)
    view.request_candidate_profile = general
    control.apply_pdf()
    control.apply_pdf()
    assert len(count) == 2 and app.commits == 2
    assert all(req.candidate_profile is None for req in app.requests)


@pytest.mark.parametrize("choice", ["general", "candidate", None])
def test_pdf_profile_resolution_matches_recruit_missing_fields(tk_window, monkeypatch, choice):
    view = TkView(tk_window)
    view.candidate_panel.widgets["work_history"].insert("1.0", "一部だけの入力")
    monkeypatch.setattr(view.dialogs, "choose", lambda *_args, **_kwargs: choice)
    decision = view.request_candidate_profile()
    assert decision.profile is None
    assert decision.proceed is (choice == "general")
    if choice != "general":
        assert view._selected_top_tab() == 0
        assert view.candidate_panel.errors["age"].get()


def test_pdf_profile_supports_empty_optional_fields(tk_window):
    view = TkView(tk_window)
    for field, value in [("age", "34歳"), ("gender", "回答しない"), ("education", "その他")]:
        view.candidate_panel.variables[field].set(value)
    view.candidate_panel.widgets["qualifications"].delete("1.0", "end")
    decision = view.request_candidate_profile()
    assert decision.proceed and decision.profile is not None
    assert decision.profile.work_history == decision.profile.qualifications == ""

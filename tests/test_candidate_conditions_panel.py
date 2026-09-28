from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import pytest

from job_comparison_app.gui.candidate_conditions import CandidateConditionsPanel
from job_comparison_app.gui.visuals import VisualManager
from recruit_job_tool.candidate_profile import (
    CANDIDATE_AGE_CHOICES,
    CANDIDATE_EDUCATION_CHOICES,
    CANDIDATE_GENDER_CHOICES,
    _CandidateProfileFieldError,
)


def _panel(tk_window) -> CandidateConditionsPanel:
    parent = ttk.Frame(tk_window)
    parent.pack(fill="both", expand=True)
    return CandidateConditionsPanel(
        root=tk_window,
        parent=parent,
        visuals=VisualManager(tk_window),
    )


def test_candidate_panel_defaults_choices_and_busy_restore(tk_window) -> None:
    panel = _panel(tk_window)
    assert panel.variables["age"].get() == ""
    assert panel.variables["gender"].get() == ""
    assert panel.variables["education"].get() == ""
    assert panel._text_value("work_history") == ""
    assert panel._text_value("qualifications") == "普通運転免許"
    assert tuple(panel.widgets["age"].cget("values")) == CANDIDATE_AGE_CHOICES
    assert tuple(panel.widgets["gender"].cget("values")) == CANDIDATE_GENDER_CHOICES
    assert tuple(panel.widgets["education"].cget("values")) == CANDIDATE_EDUCATION_CHOICES
    assert all(str(panel.widgets[field].cget("state")) == "readonly" for field in ("age", "gender", "education"))

    panel.variables["age"].set("34歳")
    panel.widgets["work_history"].insert("1.0", "営業")
    panel.widgets["work_history"].mark_set("insert", "1.1")
    panel.set_host_busy(True)
    assert all(str(widget.cget("state")) == "disabled" for widget in panel.widgets.values())
    panel.set_host_busy(False)
    assert str(panel.widgets["age"].cget("state")) == "readonly"
    assert str(panel.widgets["work_history"].cget("state")) == "normal"
    assert panel.variables["age"].get() == "34歳"
    assert panel._text_value("work_history") == "営業"
    assert panel.widgets["work_history"].index("insert") == "1.1"


def test_candidate_panel_reports_all_missing_required_fields(tk_window) -> None:
    panel = _panel(tk_window)
    missing = panel.missing_required_fields()
    assert missing == ("age", "gender", "education")
    panel.show_missing_required(missing)
    assert panel.errors["age"].get() == "年齢を選択してください。"
    assert panel.errors["gender"].get() == "性別を選択してください。"
    assert panel.errors["education"].get() == "学歴を選択してください。"


def test_candidate_panel_normalizes_and_rejects_invalid_free_text(tk_window) -> None:
    panel = _panel(tk_window)
    panel.variables["age"].set("34歳")
    panel.variables["gender"].set("回答しない")
    panel.variables["education"].set("4年制大学卒業")
    panel.widgets["work_history"].insert("1.0", "  営業\r\n\r経理  ")
    profile = panel.profile()
    assert profile.work_history == "営業\n\n経理"

    panel.widgets["work_history"].delete("1.0", "end")
    panel.widgets["work_history"].insert("1.0", "bad\x00value")
    with pytest.raises(_CandidateProfileFieldError):
        panel.profile()
    assert panel.errors["work_history"].get() == "職歴に使用できない文字が含まれています。"


def test_candidate_panel_tab_bindings_move_focus_without_inserting_tab(tk_window) -> None:
    panel = _panel(tk_window)
    editor = panel.widgets["work_history"]
    editor.focus_set()
    before = panel._text_value("work_history")
    assert panel._focus_next(type("Event", (), {"widget": editor})()) == "break"
    assert panel._text_value("work_history") == before


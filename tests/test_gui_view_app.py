from __future__ import annotations

import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from tkinter import ttk

import pytest

from job_comparison_app.gui.controller import ViewModel
from job_comparison_app.gui.view import TkView, WindowsOutputOpener
from recruit_job_tool.application import ProviderHealth, ProviderStatus
from recruit_job_tool.gui.controller import (
    GlobalPhase as RecruitGlobalPhase,
    ProviderViewModel,
    ViewModel as RecruitViewModel,
)
from recruit_job_tool.models import JobSource
from recruit_job_tool.workflow import ImportMode


def _mapped_candidate_view(tk_window) -> TkView:
    view = TkView(tk_window)
    tk_window.geometry("760x600")
    tk_window.deiconify()
    tk_window.update()
    assert view.candidate_tab_canvas.yview()[1] < 1.0
    return view


def test_view_constructs_six_tabs_and_platform_adapter(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        view = TkView(root)
        assert view.notebook.index("end") == 6
        assert view.notebook.tab(0, "text") == "転職者条件"
        assert view.notebook.tab(1, "text") == "求人取得"
        assert view.notebook.tab(2, "text") == "求人PDF整理"
        assert view.notebook.tab(3, "text") == "求人PDF全文統合テキスト化"
        assert view.notebook.tab(4, "text") == "対象PDFコピー"
        assert view.notebook.tab(5, "text") == "比較資料PDF生成"
        assert view.notebook.select()
        assert view.notebook.index(view.notebook.select()) == 0
        assert view.recruit_panel.provider_notebook.index("end") == 2
        assert view.pdf_provider_notebook.index("end") == 2
        assert view.pdf_provider_notebook.tab(0, "text") == "JoBins"
        assert view.pdf_provider_notebook.tab(1, "text") == "circus"
        assert view.selected_pdf_provider() == "jobins"
        assert view.recruit_panel.provider_notebook.select()
        assert view.recruit_panel.provider_notebook.index(
            view.recruit_panel.provider_notebook.select()
        ) == 0
        assert isinstance(WindowsOutputOpener(), WindowsOutputOpener)
    finally:
        root.destroy()


def test_view_matches_shared_layout_and_initial_state(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        view = TkView(root)
        root.update_idletasks()

        assert root.geometry().startswith("960x700")
        assert root.minsize() == (760, 600)
        assert root.winfo_reqwidth() <= 760
        assert root.winfo_reqheight() <= 600
        assert len(view.pdf_widgets["jobins"]["primary_cards"]) == 3
        assert len(view._report_primary_cards) == 3
        assert len(view._export_primary_cards) == 3
        assert len(view.pdf_widgets["jobins"]["secondary_labels"]) == 7
        assert len(view._report_secondary_labels) == 7
        assert len(view._export_secondary_labels) == 4
        assert "output_dir" in view.pdf_inputs["jobins"]
        assert not hasattr(view, "pdf_output_csv")
        assert not hasattr(view, "pdf_report_csv")
        assert view.pdf_widgets["jobins"]["tree"].cget("height") == 4
        assert view.report_tree.cget("height") == 4
        assert view.export_tree.cget("height") == 4
        assert view.progress.winfo_manager() == ""
        assert all(view.pdf_widgets["jobins"]["stat_vars"][name].get() == "未確認" for name in view.PDF_PRIMARY)
        assert all(view.pdf_widgets["jobins"]["stat_vars"][name].get() == "—" for name in view.PDF_SECONDARY)
        assert view.pdf_widgets["jobins"]["empty"].winfo_manager() == "place"
        assert view.report_empty.winfo_manager() == "place"
        assert view.export_empty.winfo_manager() == "place"
    finally:
        root.destroy()


def test_minimum_window_keeps_four_result_rows_reachable_by_tab_scroll(tk_window) -> None:
    root = tk_window
    try:
        view = TkView(root)
        root.geometry("760x600")
        view.notebook.select(2)
        root.update()

        rowheight = int(view.style.lookup("App.Treeview", "rowheight"))
        assert view.pdf_widgets["jobins"]["tree"].winfo_height() >= rowheight * 4
        view.notebook.select(5)
        root.update()
        assert view.report_tree.winfo_height() >= rowheight * 4
    finally:
        root.destroy()


def test_candidate_mousewheel_over_non_input_scrolls_outer_canvas(
    tk_window,
) -> None:
    view = _mapped_candidate_view(tk_window)
    canvas = view.candidate_tab_canvas
    canvas.yview_moveto(0)
    before = canvas.yview()

    view.candidate_tab.event_generate("<MouseWheel>", delta=-120)
    tk_window.update()
    after_down = canvas.yview()
    assert after_down[0] > before[0]

    view.candidate_tab.event_generate("<MouseWheel>", delta=1)
    tk_window.update()
    assert canvas.yview()[0] < after_down[0]


@pytest.mark.parametrize("field", ("work_history", "qualifications"))
def test_candidate_text_mousewheel_keeps_native_scroll_without_outer_chaining(
    tk_window,
    field: str,
) -> None:
    view = _mapped_candidate_view(tk_window)
    canvas = view.candidate_tab_canvas
    editor = view.candidate_panel.widgets[field]
    assert isinstance(editor, tk.Text)
    editor.configure(state="normal")
    editor.delete("1.0", "end")
    editor.insert("1.0", "\n".join(f"line {index}" for index in range(100)))
    editor.yview_moveto(0)
    canvas.yview_moveto(0)
    tk_window.update()
    outer_before = canvas.yview()

    editor.event_generate("<MouseWheel>", delta=-120)
    tk_window.update()
    assert editor.yview()[0] > 0
    assert canvas.yview() == outer_before

    editor.yview_moveto(1)
    canvas.yview_moveto(0.2)
    tk_window.update()
    outer_at_text_end = canvas.yview()
    editor.event_generate("<MouseWheel>", delta=-120)
    tk_window.update()
    assert canvas.yview() == outer_at_text_end


def test_candidate_combobox_and_scrollbar_retain_native_wheel_ownership(
    tk_window,
) -> None:
    view = _mapped_candidate_view(tk_window)
    canvas = view.candidate_tab_canvas
    combo = view.candidate_panel.widgets["age"]
    scrollbar = next(
        widget
        for widget in view.candidate_tab_shell.winfo_children()
        if isinstance(widget, ttk.Scrollbar)
    )
    canvas.yview_moveto(0)
    tk_window.update()
    outer_before_combo = canvas.yview()

    combo.event_generate("<MouseWheel>", delta=-120)
    tk_window.update()
    assert canvas.yview() == outer_before_combo
    assert view._route_candidate_mousewheel(
        SimpleNamespace(widget=combo, delta=-120)
    ) is None

    scrollbar.event_generate("<MouseWheel>", delta=-120)
    tk_window.update()
    assert canvas.yview()[0] > outer_before_combo[0]
    assert view._route_candidate_mousewheel(
        SimpleNamespace(widget=scrollbar, delta=-120)
    ) is None


def test_candidate_mousewheel_ignores_other_tabs_outside_zero_and_busy(
    tk_window,
) -> None:
    view = _mapped_candidate_view(tk_window)
    canvas = view.candidate_tab_canvas
    canvas.yview_moveto(0)
    baseline = canvas.yview()

    assert view._route_candidate_mousewheel(
        SimpleNamespace(widget=view.candidate_tab, delta=0)
    ) is None
    assert canvas.yview() == baseline
    assert view._route_candidate_mousewheel(
        SimpleNamespace(widget=view.status_label, delta=-120)
    ) is None
    assert canvas.yview() == baseline

    view._select_top_tab(1)
    assert view._route_candidate_mousewheel(
        SimpleNamespace(widget=view.candidate_tab, delta=-120)
    ) is None
    assert canvas.yview() == baseline

    view._select_top_tab(0)
    view.render(ViewModel(busy=True, status="処理中です。"))
    assert view._route_candidate_mousewheel(
        SimpleNamespace(widget=view.candidate_tab, delta=-120)
    ) is None
    assert canvas.yview() == baseline


def test_render_busy_hides_interaction_but_keeps_exit_available(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        view = TkView(root)
        view.render(ViewModel(busy=True, status="処理中です。"))
        root.update_idletasks()

        assert view.progress.winfo_manager() == "grid"
        assert str(view.help_button.cget("state")) == "disabled"
        assert str(view.close_button.cget("state")) == "normal"
        assert view.help_menu.entrycget(0, "state") == "disabled"
        assert view.notebook.instate(["disabled"])
        assert all(view.notebook.tab(index, "state") == "normal" for index in range(6))
        selected = view.notebook.select()
        assert selected
        view._shortcut_tab(1)
        assert view.notebook.select() == selected

        view.render(ViewModel())
        root.update_idletasks()
        assert view.progress.winfo_manager() == ""
        assert view.help_menu.entrycget(0, "state") == "normal"
        assert view.notebook.instate(["!disabled"])
        assert view.notebook.select() == selected
    finally:
        root.destroy()


@pytest.mark.parametrize("selected_index", range(6))
def test_top_tab_selection_survives_busy_round_trip(
    tk_window, selected_index: int
) -> None:
    root = tk_window
    root.withdraw()
    try:
        view = TkView(root)
        view._shortcut_tab(selected_index)
        selected = view.notebook.select()

        view.render(ViewModel(busy=True, status="処理中です。"))
        root.update_idletasks()
        assert view.notebook.select() == selected
        assert view.notebook.instate(["disabled"])

        view.render(ViewModel())
        root.update_idletasks()
        assert view.notebook.select() == selected
        assert view.notebook.index(view.notebook.select()) == selected_index
        assert view.notebook.instate(["!disabled"])
    finally:
        root.destroy()


def test_render_repairs_an_empty_top_tab_selection(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        view = TkView(root)
        for index in range(6):
            view.notebook.tab(index, state="disabled")
        root.update_idletasks()
        assert view.notebook.select() == ""

        view.render(ViewModel())
        root.update_idletasks()

        assert view.notebook.select()
        assert view.notebook.index(view.notebook.select()) == 0
        assert view._shortcut_preview() == "break"
        assert view._shortcut_recruit("choose_html") == "break"
    finally:
        root.destroy()


def test_candidate_form_resolution_handles_general_cancel_and_valid_profile(
    tk_window,
) -> None:
    view = TkView(tk_window)
    view._select_top_tab(1)
    view.dialogs.choose = lambda *args, **kwargs: "candidate"
    decision = view._resolve_candidate_profile()
    assert not decision.proceed
    assert view.notebook.index(view.notebook.select()) == 0
    assert view.candidate_panel.errors["age"].get()
    assert view.candidate_panel.errors["gender"].get()
    assert view.candidate_panel.errors["education"].get()

    view.dialogs.choose = lambda *args, **kwargs: "general"
    decision = view._resolve_candidate_profile()
    assert decision.proceed and decision.profile is None

    view.candidate_panel.variables["age"].set("34歳")
    view.candidate_panel.variables["gender"].set("回答しない")
    view.candidate_panel.variables["education"].set("4年制大学卒業")
    decision = view._resolve_candidate_profile()
    assert decision.proceed
    assert decision.profile is not None
    assert decision.profile.age == "34歳"


def test_recruit_shortcuts_only_run_on_recruit_tab_and_f5_is_explicit(
    tk_window,
) -> None:
    view = TkView(tk_window)
    calls: list[str] = []
    view.controller = SimpleNamespace(
        recruit_action=lambda action: calls.append(action),
        preview_pdf=lambda: calls.append("pdf"),
        preview_export=lambda: calls.append("export"),
        preview_copy=lambda: calls.append("copy"),
        preview_report=lambda: calls.append("report"),
    )
    view._select_top_tab(0)
    view._shortcut_recruit("generate_analysis")
    view._shortcut_preview()
    assert calls == []

    view._select_top_tab(1)
    view._shortcut_recruit("generate_analysis")
    view._shortcut_preview()
    assert calls == ["generate_analysis"]

    for index, expected in ((2, "pdf"), (3, "export"), (4, "copy"), (5, "report")):
        view._select_top_tab(index)
        view._shortcut_preview()
        assert calls[-1] == expected


def _recruit_model(*, active: JobSource, busy: bool) -> RecruitViewModel:
    return RecruitViewModel(
        phase=RecruitGlobalPhase.BUSY if busy else RecruitGlobalPhase.IDLE,
        active_provider=active,
        providers={
            provider: ProviderViewModel(provider=provider) for provider in JobSource
        },
        status_text="状態を確認しています。" if busy else "操作を選んでください。",
        busy=busy,
    )


def _recruit_status(
    provider: JobSource,
    *,
    health: ProviderHealth = ProviderHealth.READY,
    count: int | None = 0,
    output_configured: bool = False,
    output_error: bool = False,
) -> ProviderStatus:
    return ProviderStatus(
        provider=provider,
        health=health,
        current_count=count,
        csv_exists=False,
        restore_available=False,
        recovered_from_previous=False,
        output_configured=output_configured,
        output_error=output_error,
    )


def _recruit_model_with_status(
    status: ProviderStatus,
    *,
    busy: bool = False,
    mode: ImportMode = ImportMode.REPLACE,
) -> RecruitViewModel:
    providers = {
        provider: ProviderViewModel(
            provider=provider,
            mode=mode if provider is status.provider else ImportMode.REPLACE,
            status=(
                status
                if provider is status.provider
                else _recruit_status(provider)
            ),
        )
        for provider in JobSource
    }
    return RecruitViewModel(
        phase=RecruitGlobalPhase.BUSY if busy else RecruitGlobalPhase.IDLE,
        active_provider=status.provider,
        providers=providers,
        status_text="処理中です。" if busy else "操作を選んでください。",
        busy=busy,
    )


@pytest.mark.parametrize(
    ("status", "busy", "replace_state", "merge_state"),
    (
        (
            _recruit_status(
                JobSource.JOBINS,
                count=1,
                output_configured=True,
            ),
            False,
            "normal",
            "normal",
        ),
        (
            _recruit_status(
                JobSource.JOBINS,
                health=ProviderHealth.READY,
                count=0,
                output_configured=False,
            ),
            False,
            "normal",
            "normal",
        ),
        (
            _recruit_status(
                JobSource.JOBINS,
                health=ProviderHealth.REPLACE_REQUIRED,
                count=101,
                output_configured=True,
            ),
            False,
            "normal",
            "disabled",
        ),
        (
            _recruit_status(
                JobSource.JOBINS,
                output_configured=True,
                output_error=True,
            ),
            False,
            "normal",
            "disabled",
        ),
        (
            _recruit_status(
                JobSource.JOBINS,
                health=ProviderHealth.UNAVAILABLE,
                count=None,
            ),
            False,
            "disabled",
            "disabled",
        ),
        (_recruit_status(JobSource.JOBINS), True, "disabled", "disabled"),
    ),
)
def test_recruit_mode_controls_preserve_guard_states(
    tk_window,
    status: ProviderStatus,
    busy: bool,
    replace_state: str,
    merge_state: str,
) -> None:
    view = TkView(tk_window)
    panel = view.recruit_panel
    panel.render(_recruit_model_with_status(status, busy=busy))

    assert str(panel._controls[status.provider]["replace"].cget("state")) == replace_state
    assert str(panel._controls[status.provider]["merge"].cget("state")) == merge_state


def test_recruit_mode_controls_restore_after_host_busy(tk_window) -> None:
    view = TkView(tk_window)
    panel = view.recruit_panel
    panel.render(_recruit_model_with_status(_recruit_status(JobSource.JOBINS)))

    panel.set_host_busy(True)
    assert str(panel._controls[JobSource.JOBINS]["replace"].cget("state")) == "disabled"
    assert str(panel._controls[JobSource.JOBINS]["merge"].cget("state")) == "disabled"

    panel.set_host_busy(False)
    assert str(panel._controls[JobSource.JOBINS]["replace"].cget("state")) == "normal"
    assert str(panel._controls[JobSource.JOBINS]["merge"].cget("state")) == "normal"


def test_recruit_merge_selection_survives_busy_and_completion_renders(
    tk_window,
) -> None:
    view = TkView(tk_window)
    panel = view.recruit_panel
    ready = _recruit_status(
        JobSource.JOBINS,
        count=2,
        output_configured=True,
    )

    panel.render(
        _recruit_model_with_status(ready, mode=ImportMode.MERGE)
    )
    assert panel._modes[JobSource.JOBINS].get() == ImportMode.MERGE.value
    assert panel._controls[JobSource.JOBINS]["merge"].instate(["selected"])

    panel.render(
        _recruit_model_with_status(ready, busy=True, mode=ImportMode.MERGE)
    )
    assert panel._controls[JobSource.JOBINS]["merge"].instate(["disabled"])
    assert panel._controls[JobSource.JOBINS]["merge"].instate(["selected"])

    panel.render(
        _recruit_model_with_status(ready, mode=ImportMode.MERGE)
    )
    assert panel._modes[JobSource.JOBINS].get() == ImportMode.MERGE.value
    assert panel._controls[JobSource.JOBINS]["merge"].instate(["!disabled"])
    assert panel._controls[JobSource.JOBINS]["merge"].instate(["selected"])


def test_provider_tab_selection_survives_recruit_and_host_busy(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        view = TkView(root)
        panel = view.recruit_panel

        panel.render(_recruit_model(active=JobSource.JOBINS, busy=True))
        root.update_idletasks()
        assert panel.provider_notebook.instate(["disabled"])
        assert panel.notebook_provider() is JobSource.JOBINS
        assert all(
            panel.provider_notebook.tab(index, "state") == "normal"
            for index in range(2)
        )

        panel.render(_recruit_model(active=JobSource.CAREERBANK, busy=False))
        root.update_idletasks()
        assert panel.provider_notebook.instate(["!disabled"])
        assert panel.notebook_provider() is JobSource.CAREERBANK

        selected = panel.provider_notebook.select()
        panel.set_host_busy(True)
        root.update_idletasks()
        assert panel.provider_notebook.instate(["disabled"])
        assert panel.provider_notebook.select() == selected

        panel.set_host_busy(False)
        root.update_idletasks()
        assert panel.provider_notebook.instate(["!disabled"])
        assert panel.notebook_provider() is JobSource.CAREERBANK
    finally:
        root.destroy()


def test_provider_lookup_falls_back_when_selection_is_empty(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        view = TkView(root)
        panel = view.recruit_panel
        panel.render(_recruit_model(active=JobSource.CAREERBANK, busy=False))
        for index in range(2):
            panel.provider_notebook.tab(index, state="disabled")
        root.update_idletasks()
        assert panel.provider_notebook.select() == ""

        assert panel.notebook_provider() is JobSource.CAREERBANK
        panel._tab_changed(SimpleNamespace())
    finally:
        root.destroy()


def test_planless_and_prepared_metric_rendering(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        view = TkView(root)
        plan = SimpleNamespace(
            summary={
                "JSON出力対象": 2,
                "JSON除外": 1,
                "リネーム対象": 1,
                "変更なし": 1,
                "成功": 2,
                "部分成功": 1,
                "改名保留": 0,
                "エラー": 0,
            },
            results=[],
            job_info=SimpleNamespace(stale_jobs=[object()]),
            job_info_json=Path("C:/output/求人情報.json"),
            report_json=Path("C:/output/処理レポート.json"),
            comparison_guide_path=Path("C:/output/求人PDFから比較資料に.md"),
            candidate_applied=False,
        )

        view._render_pdf_plan(plan)
        assert view.pdf_widgets["jobins"]["stat_vars"]["対象PDF"].get() == "0"
        assert view.pdf_widgets["jobins"]["stat_vars"]["JSON出力対象"].get() == "2"
        assert view.pdf_widgets["jobins"]["stat_vars"]["PDF未検出"].get() == "1"
        assert view.pdf_widgets["jobins"]["empty"].winfo_manager() == ""
        assert "求人情報.json" in view.pdf_widgets["jobins"]["output_summary"].get()

        view._render_pdf_plan(None)
        assert view.pdf_widgets["jobins"]["stat_vars"]["対象PDF"].get() == "未確認"
        assert view.pdf_widgets["jobins"]["stat_vars"]["JSON除外"].get() == "—"
        assert view.pdf_widgets["jobins"]["empty"].winfo_manager() == "place"
        assert view.pdf_widgets["jobins"]["output_summary"].get() == "保存先は事前確認後に表示します。"
    finally:
        root.destroy()


def test_report_details_remain_collapsible(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        view = TkView(root)
        assert view.report_details.winfo_manager() == ""
        view.report_show_details.set(True)
        view._toggle_report_details()
        assert view.report_details.winfo_manager() == "grid"
        view.report_show_details.set(False)
        view._toggle_report_details()
        assert view.report_details.winfo_manager() == ""
    finally:
        root.destroy()


def test_pdf_request_uses_one_optional_output_directory(tk_window, tmp_path: Path) -> None:
    root = tk_window
    root.withdraw()
    try:
        view = TkView(root)
        view.pdf_inputs["jobins"]["input_dir"].set(str(tmp_path))

        default_request = view.current_pdf_request()
        assert default_request.job_info_json == (tmp_path / "output" / "求人情報.json").resolve()
        assert default_request.report_json == (tmp_path / "output" / "処理レポート.json").resolve()
        assert not (tmp_path / "output").exists()

        selected = tmp_path / "JSON 出力"
        view.pdf_inputs["jobins"]["output_dir"].set(str(selected))
        selected_request = view.current_pdf_request()
        assert selected_request.job_info_json == (selected / "求人情報.json").resolve()
        assert selected_request.report_json == (selected / "処理レポート.json").resolve()
    finally:
        root.destroy()


def test_pdf_provider_inputs_are_independent_and_only_active_changes_invalidate(tk_window, tmp_path):
    view = TkView(tk_window)
    tk_window.deiconify()
    tk_window.update()
    calls = []
    view.controller = SimpleNamespace(
        invalidate_pdf=lambda: (calls.append(view.selected_pdf_provider()), view.render(ViewModel()))
    )
    jobins = view.pdf_inputs["jobins"]
    circus = view.pdf_inputs["circus"]
    jobins["input_dir"].set(str(tmp_path / "jobins"))
    jobins["aliases"].set(str(tmp_path / "jobins.json"))
    jobins["output_dir"].set(str(tmp_path / "out-jobins"))
    circus["input_dir"].set(str(tmp_path / "circus"))
    circus["aliases"].set(str(tmp_path / "circus.json"))
    circus["output_dir"].set(str(tmp_path / "out-circus"))
    assert calls == ["jobins", "jobins", "jobins"]
    view.pdf_provider_notebook.select(1)
    tk_window.update()
    assert view.selected_pdf_provider() == "circus"
    assert calls == ["jobins", "jobins", "jobins", "circus"]
    assert view.current_pdf_request().provider == "circus"
    assert view.current_pdf_request().input_dir == tmp_path / "circus"
    view.pdf_provider_notebook.select(0)
    tk_window.update()
    assert view.selected_pdf_provider() == "jobins"
    assert view.current_pdf_request().input_dir == tmp_path / "jobins"
    assert jobins["aliases"].get() == str(tmp_path / "jobins.json")
    assert circus["aliases"].get() == str(tmp_path / "circus.json")
    assert len(calls) == 5
    view.pdf_provider_notebook.select(0)
    tk_window.update()
    assert len(calls) == 5
    view._copy_pdf_folder_to_export()
    assert view.export_input.get() == str(tmp_path / "jobins")


def test_pdf_provider_switch_clears_both_result_pages(tk_window):
    view = TkView(tk_window)
    tk_window.deiconify()
    tk_window.update()
    for provider in ("jobins", "circus"):
        widgets = view.pdf_widgets[provider]
        widgets["stat_vars"]["対象PDF"].set("10")
        widgets["output_summary"].set("以前の保存結果")
        widgets["tree"].insert("", "end", values=("old",))
        widgets["empty"].place_forget()
    calls = []
    view.controller = SimpleNamespace(
        invalidate_pdf=lambda: (calls.append("invalidate"), view.render(ViewModel()))
    )
    view.pdf_provider_notebook.select(1)
    tk_window.update()
    assert calls == ["invalidate"]
    for provider in ("jobins", "circus"):
        widgets = view.pdf_widgets[provider]
        assert widgets["stat_vars"]["対象PDF"].get() == "未確認"
        assert widgets["output_summary"].get() == "保存先は事前確認後に表示します。"
        assert not widgets["tree"].get_children()
        assert widgets["empty"].winfo_manager() == "place"
        assert str(widgets["open_button"].cget("state")) == "disabled"
    assert view.pdf_widgets["jobins"]["tree"] is not view.pdf_widgets["circus"]["tree"]
    assert view.pdf_widgets["jobins"]["stat_vars"] is not view.pdf_widgets["circus"]["stat_vars"]


def test_pdf_provider_busy_prevents_programmatic_switch_and_recovers(tk_window):
    view = TkView(tk_window)
    tk_window.deiconify()
    tk_window.update()
    view.render(ViewModel(busy=True))
    assert view.pdf_provider_notebook.instate(["disabled"])
    for widget in view._input_widgets:
        assert str(widget.cget("state")) == "disabled"
    view.pdf_provider_notebook.state(["!disabled"])
    view.pdf_provider_notebook.select(1)
    tk_window.update()
    assert view.selected_pdf_provider() == "jobins"
    assert view.pdf_provider_notebook.index(view.pdf_provider_notebook.select()) == 0
    view.render(ViewModel())
    assert view.pdf_provider_notebook.instate(["!disabled"])
    assert all(
        view.pdf_provider_notebook.tab(index, "state") == "normal"
        for index in range(2)
    )
    assert all(str(widget.cget("state")) == "normal" for widget in view._input_widgets)

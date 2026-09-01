from __future__ import annotations

import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

from job_comparison_app.gui.controller import ViewModel
from job_comparison_app.gui.view import TkView, WindowsOutputOpener


def test_view_constructs_three_tabs_and_platform_adapter(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        view = TkView(root)
        assert view.notebook.index("end") == 3
        assert view.notebook.tab(0, "text") == "求人PDF整理"
        assert view.notebook.tab(1, "text") == "求人PDF全文統合テキスト化"
        assert view.notebook.tab(2, "text") == "比較資料PDF生成"
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
        assert len(view._pdf_primary_cards) == 3
        assert len(view._report_primary_cards) == 3
        assert len(view._export_primary_cards) == 3
        assert len(view._pdf_secondary_labels) == 7
        assert len(view._report_secondary_labels) == 7
        assert len(view._export_secondary_labels) == 4
        assert hasattr(view, "pdf_output_dir")
        assert not hasattr(view, "pdf_output_csv")
        assert not hasattr(view, "pdf_report_csv")
        assert view.pdf_tree.cget("height") == 4
        assert view.report_tree.cget("height") == 4
        assert view.export_tree.cget("height") == 4
        assert view.progress.winfo_manager() == ""
        assert all(view._pdf_stat_vars[name].get() == "未確認" for name in view.PDF_PRIMARY)
        assert all(view._pdf_stat_vars[name].get() == "—" for name in view.PDF_SECONDARY)
        assert view.pdf_empty.winfo_manager() == "place"
        assert view.report_empty.winfo_manager() == "place"
        assert view.export_empty.winfo_manager() == "place"
    finally:
        root.destroy()


def test_minimum_window_keeps_four_result_rows_reachable_by_tab_scroll(tk_window) -> None:
    root = tk_window
    try:
        view = TkView(root)
        root.geometry("760x600")
        root.update()

        rowheight = int(view.style.lookup("App.Treeview", "rowheight"))
        assert view.pdf_tree.winfo_height() >= rowheight * 4
        view.notebook.select(2)
        root.update()
        assert view.report_tree.winfo_height() >= rowheight * 4
    finally:
        root.destroy()


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
        assert view.notebook.tab(0, "state") == "disabled"
        assert view.notebook.tab(1, "state") == "disabled"
        assert view.notebook.tab(2, "state") == "disabled"
        selected = view.notebook.select()
        view._shortcut_tab(1)
        assert view.notebook.select() == selected

        view.render(ViewModel())
        root.update_idletasks()
        assert view.progress.winfo_manager() == ""
        assert view.help_menu.entrycget(0, "state") == "normal"
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
        )

        view._render_pdf_plan(plan)
        assert view._pdf_stat_vars["対象PDF"].get() == "0"
        assert view._pdf_stat_vars["JSON出力対象"].get() == "2"
        assert view._pdf_stat_vars["PDF未検出"].get() == "1"
        assert view.pdf_empty.winfo_manager() == ""
        assert "求人情報.json" in view.pdf_output_summary.get()

        view._render_pdf_plan(None)
        assert view._pdf_stat_vars["対象PDF"].get() == "未確認"
        assert view._pdf_stat_vars["JSON除外"].get() == "—"
        assert view.pdf_empty.winfo_manager() == "place"
        assert view.pdf_output_summary.get() == "保存先は事前確認後に表示します。"
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
        view.pdf_input.set(str(tmp_path))

        default_request = view.current_pdf_request()
        assert default_request.job_info_json == (tmp_path / "output" / "求人情報.json").resolve()
        assert default_request.report_json == (tmp_path / "output" / "処理レポート.json").resolve()
        assert not (tmp_path / "output").exists()

        selected = tmp_path / "JSON 出力"
        view.pdf_output_dir.set(str(selected))
        selected_request = view.current_pdf_request()
        assert selected_request.job_info_json == (selected / "求人情報.json").resolve()
        assert selected_request.report_json == (selected / "処理レポート.json").resolve()
    finally:
        root.destroy()

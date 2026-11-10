"""Reproducibly capture the 0.4.3 user-guide scenes with synthetic data only."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_ROOT = REPO_ROOT / "docs" / "user-guide" / "images"
CAPTURE_TEMP = REPO_ROOT / ".venv-build" / "user-guide-capture"
CAPTURE_SCRIPT = Path(__file__).with_name("capture_window.ps1")
WINDOW_TITLE = "求人比較ツール — 使い方ガイド撮影"

# The script lives below tools/, so make the checked-out application win over
# any older editable/install copy that may be present in the build venv.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCENES = (
    "11-pdf-copy-preview",
    "00-workflow-overview",
    "01-start-candidate",
    "02-candidate-example",
    "03-recruit-initial-setup",
    "04-recruit-ready",
    "05-replace-confirmation",
    "06-general-comparison-confirmation",
    "07-candidate-analysis-complete",
    "08-pdf-organize-preview",
    "09-pdf-text-preview",
    "10-report-preview",
)


# Coordinates are relative to the captured Tk client area. They deliberately
# point into open space adjacent to controls so UI labels remain readable.
ANNOTATIONS: dict[str, tuple[tuple[int, int, int, int], ...]] = {
    "11-pdf-copy-preview": (),
    "00-workflow-overview": ((70, 205, 24, 170), (70, 465, 24, 430), (485, 335, 445, 310)),
    "01-start-candidate": (
        (415, 88, 380, 112), (880, 304, 842, 260), (105, 535, 218, 505),
        (928, 480, 880, 498), (115, 680, 170, 628), (850, 680, 798, 628),
    ),
    "02-candidate-example": (
        (90, 390, 20, 365), (90, 530, 20, 505), (880, 445, 835, 405),
        (928, 440, 875, 465), (470, 610, 440, 625),
    ),
    "03-recruit-initial-setup": ((300, 180, 740, 160), (105, 220, 170, 255), (600, 88, 650, 120)),
    "04-recruit-ready": (
        (80, 135, 20, 110), (480, 190, 450, 230), (860, 345, 820, 295),
        (185, 442, 240, 425), (145, 475, 210, 460), (480, 585, 485, 540),
        (480, 620, 485, 615),
    ),
    "05-replace-confirmation": ((480, 300, 290, 235), (480, 382, 290, 405), (500, 505, 380, 540), (635, 505, 700, 540)),
    "06-general-comparison-confirmation": ((480, 310, 255, 235), (420, 510, 305, 548), (620, 510, 710, 548), (730, 220, 775, 185)),
    "07-candidate-analysis-complete": ((500, 330, 275, 285), (500, 380, 275, 400), (500, 445, 710, 430), (720, 265, 770, 215)),
    "08-pdf-organize-preview": (
        (155, 272, 18, 250), (700, 272, 895, 240), (85, 430, 18, 405),
        (195, 430, 245, 395), (470, 150, 440, 185), (450, 570, 420, 620),
        (850, 570, 870, 620),
    ),
    "09-pdf-text-preview": (
        (155, 272, 18, 250), (145, 315, 245, 295), (620, 335, 740, 300),
        (85, 414, 18, 390), (210, 414, 250, 380), (470, 150, 440, 185),
        (510, 565, 480, 620),
    ),
    "10-report-preview": (
        (430, 272, 18, 250), (155, 355, 18, 335), (795, 355, 850, 320),
        (115, 385, 210, 370), (85, 530, 18, 505), (200, 530, 250, 495),
        (470, 150, 440, 185), (510, 620, 480, 640), (850, 530, 875, 490),
    ),
}


def _is_reparse(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _prepare_environment(output_dir: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("User-guide captures are supported only on Windows.")
    resolved = output_dir.resolve(strict=False)
    if resolved != IMAGE_ROOT.resolve(strict=False):
        raise RuntimeError("Captures may only be written to docs/user-guide/images.")
    for parent in (REPO_ROOT, REPO_ROOT / "docs", REPO_ROOT / "docs" / "user-guide"):
        if parent.exists() and _is_reparse(parent):
            raise RuntimeError(f"Capture path contains a reparse point: {parent.name}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if _is_reparse(output_dir):
        raise RuntimeError("The image output directory cannot be a reparse point.")
    CAPTURE_TEMP.mkdir(parents=True, exist_ok=True)
    if _is_reparse(CAPTURE_TEMP):
        raise RuntimeError("The capture temporary directory cannot be a reparse point.")
    os.environ["LOCALAPPDATA"] = str(CAPTURE_TEMP / "localappdata")
    Path(os.environ["LOCALAPPDATA"]).mkdir(parents=True, exist_ok=True)


def _annotation_payload(scene: str) -> str:
    return json.dumps(
        [
            {
                "number": index,
                "target_x": values[0],
                "target_y": values[1],
                "badge_x": values[2],
                "badge_y": values[3],
            }
            for index, values in enumerate(ANNOTATIONS[scene], start=1)
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _capture(
    root: tk.Tk,
    scene: str,
    output_dir: Path,
    *,
    bring_to_front: bool = True,
) -> None:
    if scene not in SCENES:
        raise ValueError(f"Unknown scene: {scene}")
    output = output_dir / f"{scene}.png"
    if output.name not in {f"{name}.png" for name in SCENES}:
        raise RuntimeError("Output filename is not allowlisted.")
    root.update_idletasks()
    root.update()
    if bring_to_front:
        root.lift()
        root.attributes("-topmost", True)
        root.update()
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(CAPTURE_SCRIPT),
            "-ProcessId",
            str(os.getpid()),
            "-WindowTitle",
            WINDOW_TITLE,
            "-OutputPath",
            str(output),
            "-AllowedRoot",
            str(output_dir),
            "-AnnotationsJson",
            _annotation_payload(scene),
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    if bring_to_front:
        root.attributes("-topmost", False)


def _new_view() -> tuple[tk.Tk, object]:
    from job_comparison_app.gui.controller import ViewModel
    from job_comparison_app.gui.view import TkView

    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.geometry("960x700+80+60")
    root.minsize(760, 600)
    view = TkView(root)
    root.title(WINDOW_TITLE)
    view.render(ViewModel(status="状態: 入力を選択してください。"))
    return root, view


def _select_tab(view: object, index: int) -> None:
    view._select_top_tab(index)  # type: ignore[attr-defined]
    view._sync_tab_layouts()  # type: ignore[attr-defined]
    view.root.update_idletasks()  # type: ignore[attr-defined]


def _ready_recruit(view: object, *, replace: bool = False) -> None:
    from recruit_job_tool.application import ProviderHealth, ProviderStatus
    from recruit_job_tool.gui.controller import (
        GlobalPhase,
        ProviderViewModel,
        ViewModel as RecruitViewModel,
    )
    from recruit_job_tool.models import JobSource
    from recruit_job_tool.workflow import ImportMode

    def status(provider: JobSource, count: int) -> ProviderStatus:
        return ProviderStatus(
            provider=provider,
            health=ProviderHealth.READY,
            current_count=count,
            csv_exists=True,
            restore_available=True,
            recovered_from_previous=False,
            output_configured=True,
            output_error=False,
        )

    jobins_mode = ImportMode.REPLACE if replace else ImportMode.MERGE
    providers = {
        JobSource.JOBINS: ProviderViewModel(
            provider=JobSource.JOBINS,
            selected_paths=tuple(Path(f"M:/manual-sample/求人HTML/求人検索結果_20260907_{index:02d}.html") for index in range(1, 6)),
            mode=jobins_mode,
            status=status(JobSource.JOBINS, 3),
        ),
        JobSource.CAREERBANK: ProviderViewModel(
            provider=JobSource.CAREERBANK,
            selected_paths=(),
            mode=ImportMode.REPLACE,
            status=status(JobSource.CAREERBANK, 3),
        ),
    }
    view.recruit_panel.render(  # type: ignore[attr-defined]
        RecruitViewModel(
            phase=GlobalPhase.IDLE,
            active_provider=JobSource.JOBINS,
            providers=providers,
            status_text="状態: JoBinsは利用できます。",
            busy=False,
        )
    )
    _select_tab(view, 1)


def _close_dialog(root: tk.Tk) -> None:
    for child in root.winfo_children():
        if isinstance(child, tk.Toplevel):
            child.destroy()


def _capture_modal(root: tk.Tk, view: object, scene: str, output_dir: Path, show) -> None:
    def capture_and_close() -> None:
        _capture(root, scene, output_dir, bring_to_front=False)
        root.after(50, lambda: _close_dialog(root))

    root.after(500, capture_and_close)
    show()


def _workflow_scene(output_dir: Path) -> None:
    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.geometry("960x640+80+60")
    root.configure(background="white")
    canvas = tk.Canvas(root, width=960, height=640, background="white", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    navy, blue, pale, gray = "#133769", "#2563EB", "#EAF2FF", "#5B6575"
    canvas.create_text(480, 38, text="求人比較ツールの2つのワークフロー", font=("Yu Gothic UI", 20, "bold"), fill=navy)

    def box(x1: int, y1: int, x2: int, y2: int, text: str) -> None:
        canvas.create_rectangle(x1, y1, x2, y2, fill=pale, outline=blue, width=2)
        canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=text, font=("Yu Gothic UI", 11, "bold"), fill=navy, width=x2 - x1 - 18)

    def arrow(x1: int, y1: int, x2: int, y2: int) -> None:
        canvas.create_line(x1, y1, x2, y2, fill=blue, width=3, arrow=tk.LAST)

    canvas.create_text(70, 105, text="HTML・AI比較系", anchor="w", font=("Yu Gothic UI", 15, "bold"), fill=navy)
    top = ((45, 145, 185, 220, "転職者条件"), (230, 145, 390, 220, "検索結果HTML"), (435, 145, 575, 220, "求人取り込み"), (620, 145, 790, 220, "求人JSON＋\n比較ガイド"), (820, 145, 945, 220, "外部AIへ\n利用者が添付"))
    for values in top:
        box(*values)
    for left, right in ((185, 230), (390, 435), (575, 620), (790, 820)):
        arrow(left, 183, right - 8, 183)

    canvas.create_line(45, 310, 915, 310, fill="#D6DEE8", width=2, dash=(7, 5))
    canvas.create_text(480, 285, text="自動連携なし", font=("Yu Gothic UI", 12, "bold"), fill=gray)
    canvas.create_text(70, 365, text="PDF・比較資料系", anchor="w", font=("Yu Gothic UI", 15, "bold"), fill=navy)
    bottom = ((45, 405, 180, 485, "求人PDF整理"), (220, 405, 365, 485, "PDF全文\nMarkdown"), (405, 405, 590, 485, "外部AIなどで\n10求人比較MD準備"), (630, 405, 790, 485, "元PDF対応JSON\nと照合"), (830, 405, 945, 485, "A4縦\n12ページPDF"))
    for values in bottom:
        box(*values)
    for left, right in ((180, 220), (365, 405), (590, 630), (790, 830)):
        arrow(left, 445, right - 8, 445)
    canvas.create_text(480, 555, text="求人取得用JSONとPDF工程用JSONは別形式です", font=("Yu Gothic UI", 12), fill=gray)
    root.update_idletasks()
    _capture(root, "00-workflow-overview", output_dir)
    root.destroy()


def _candidate_start(output_dir: Path) -> None:
    root, view = _new_view()
    _select_tab(view, 0)
    _capture(root, "01-start-candidate", output_dir)
    root.destroy()


def _candidate_example(output_dir: Path) -> None:
    root, view = _new_view()
    panel = view.candidate_panel
    panel.variables["age"].set("34歳")
    panel.variables["gender"].set("回答しない")
    panel.variables["education"].set("4年制大学卒業")
    work = panel.widgets["work_history"]
    work.delete("1.0", "end")
    work.insert("1.0", "サンプル職歴：法人向け営業を3年間担当\n顧客への提案と新人教育を担当")
    qualifications = panel.widgets["qualifications"]
    qualifications.delete("1.0", "end")
    qualifications.insert("1.0", "普通運転免許")
    _select_tab(view, 0)
    view.candidate_tab_canvas.yview_moveto(0.55)
    root.update_idletasks()
    _capture(root, "02-candidate-example", output_dir)
    root.destroy()


def _recruit_initial(output_dir: Path) -> None:
    from job_comparison_app.gui.recruit_feature import RecruitFeaturePhase

    root, view = _new_view()
    view.recruit_panel.set_phase(
        RecruitFeaturePhase.UNINITIALIZED,
        "求人取得機能はまだ初期設定されていません。\n既存データの確認または新規開始を選んでください。",
        can_initialize=True,
    )
    _select_tab(view, 1)
    _capture(root, "03-recruit-initial-setup", output_dir)
    root.destroy()


def _recruit_ready(output_dir: Path) -> None:
    root, view = _new_view()
    _ready_recruit(view)
    _capture(root, "04-recruit-ready", output_dir)
    root.destroy()


def _replace_dialog(output_dir: Path) -> None:
    from job_comparison_app.gui.dialogs import DialogAction

    root, view = _new_view()
    _ready_recruit(view, replace=True)
    _capture_modal(
        root,
        view,
        "05-replace-confirmation",
        output_dir,
        lambda: view.dialogs.choose(
            "現在の求人を置き換えますか？",
            "JoBinsの現在の求人3件を、\n選択したHTML 5個すべての求人に置き換えます。\n\nこの操作の直前状態は1世代だけ保存され、\n「直前状態へ戻す」で復元できます。\n\n続行しますか？",
            actions=(
                DialogAction("キャンセル", result=False, is_default=True),
                DialogAction("置き換える", result=True, primary=True),
            ),
            cancel_result=False,
        ),
    )
    root.destroy()


def _general_dialog(output_dir: Path) -> None:
    from job_comparison_app.gui.dialogs import DialogAction

    root, view = _new_view()
    _ready_recruit(view)
    _capture_modal(
        root,
        view,
        "06-general-comparison-confirmation",
        output_dir,
        lambda: view.dialogs.choose(
            "転職者条件が未入力です",
            "転職者条件の必須項目が未入力です。\n現在入力されている職歴・資格なども含め、\nすべての転職者条件を無視して一般比較用ファイルを作成しますか？",
            actions=(
                DialogAction("転職者条件へ戻る", result=False, is_default=True),
                DialogAction("一般比較として作成", result=True, primary=True),
            ),
            cancel_result=False,
        ),
    )
    root.destroy()


def _analysis_complete(output_dir: Path) -> None:
    from job_comparison_app.gui.dialogs import DialogKind

    root, view = _new_view()
    _ready_recruit(view)
    message = (
        "JoBinsの求人3件から転職者条件付きAI比較ファイルを作成しました。\n\n"
        "分析用求人JSON（必須）: jobins_analysis_jobs_20260907_120000.json\n"
        "転職者条件入りガイド（必須）: jobins_analysis_guide_20260907_120000.md\n\n"
        "AIへ2ファイルを一緒に添付してください。"
    )
    _capture_modal(
        root,
        view,
        "07-candidate-analysis-complete",
        output_dir,
        lambda: view.dialogs.show("AI比較ファイルを作成しました", message, DialogKind.SUCCESS),
    )
    root.destroy()


def _pdf_plan() -> object:
    from job_pdf_tool.models import DocumentResult, FieldValue, ReportItem

    def result(letter: str, position: str, status: str, *, eligible: bool = True, message: str = "") -> DocumentResult:
        item = DocumentResult(
            source_path=Path(fr"M:\manual-sample\求人PDF\sample_{letter}.pdf"),
            company=FieldValue(f"サンプル株式会社{letter}", "direct", "会社名", 1),
            raw_position=FieldValue(position, "direct", "求人名", 1),
            resolved_position=position,
            identity_validated=eligible,
            proposed_name=f"サンプル株式会社{letter}（{position}）.pdf" if eligible else "",
            status=status,
            error="" if eligible else "会社名を確定できません。",
        )
        if message:
            item.reports.append(ReportItem("年収", "warning", message))
        return item

    results = [
        result("A", "サンプル営業職", "成功"),
        result("B", "サンプル事務職", "部分成功", message="年間休日は記載なしです。"),
        result("C", "サンプル企画職", "エラー", eligible=False),
    ]
    return SimpleNamespace(
        results=results,
        job_info=SimpleNamespace(stale_jobs=[]),
        summary={"JSON出力対象": 2, "リネーム対象": 2, "JSON除外": 1, "成功": 1, "部分成功": 1, "エラー": 1},
        job_info_json=Path(r"M:\manual-sample\求人PDF\output\求人情報.json"),
        report_json=Path(r"M:\manual-sample\求人PDF\output\処理レポート.json"),
        comparison_guide_path=Path(r"M:\manual-sample\求人PDF\output\求人PDFから比較資料に.md"),
        candidate_applied=False,
    )


def _pdf_preview(output_dir: Path) -> None:
    from job_comparison_app.gui.controller import ViewModel

    root, view = _new_view()
    view.pdf_inputs["jobins"]["input_dir"].set(r"M:\manual-sample\求人PDF")
    view.pdf_inputs["jobins"]["aliases"].set(r"M:\manual-sample\設定\見出し別名.json")
    view.pdf_inputs["jobins"]["output_dir"].set(r"M:\manual-sample\求人PDF\output")
    _select_tab(view, 2)
    view.render(ViewModel(status="状態: 事前確認が完了しました。", tone="success", pdf_plan=_pdf_plan(), pdf_output_mode="新規保存"))
    view.pdf_widgets["jobins"]["preview_button"].configure(state="normal")
    view.pdf_widgets["jobins"]["apply_button"].configure(state="normal")
    view._sync_tab_layouts()
    _capture(root, "08-pdf-organize-preview", output_dir)
    root.destroy()


def _export_plan() -> object:
    from pdf_job_exporter.models import DocumentResult, ExtractionWarning, PageResult

    documents = (
        DocumentResult(1, "サンプル株式会社A（サンプル営業職）.pdf", 3, (PageResult(1, "架空本文" * 250, 0, False),)),
        DocumentResult(2, "サンプル株式会社B（サンプル事務職）.pdf", 2, (PageResult(1, "架空本文" * 180, 0, False),)),
        DocumentResult(3, "サンプル株式会社C（サンプル企画職）.pdf", 4, (PageResult(1, "架空本文" * 220, 1, False),)),
    )
    warnings = (ExtractionWarning("nul_replaced", "NULを1文字置換しました。", documents[2].filename, 1, 1),)
    return SimpleNamespace(
        documents=documents,
        document_count=3,
        page_count=9,
        character_count=sum(item.character_count for item in documents),
        byte_count=12840,
        warnings=warnings,
    )


def _export_preview(output_dir: Path) -> None:
    from job_comparison_app.gui.controller import ViewModel

    root, view = _new_view()
    view.export_input.set(r"M:\manual-sample\求人PDF")
    view.export_output.set(r"M:\manual-sample\求人PDF\output\PDF全文_統合テキスト化.md")
    _select_tab(view, 3)
    view.render(ViewModel(status="状態: 事前確認が完了しました（警告1件）。", tone="warning", export_plan=_export_plan()))
    view.export_preview_button.configure(state="normal")
    view.export_apply_button.configure(state="normal")
    view._sync_tab_layouts()
    _capture(root, "09-pdf-text-preview", output_dir)
    root.destroy()


def _report_plan() -> object:
    from job_comparison_report.models import JobEntry, ReportDocument, VerificationFinding

    jobs = [JobEntry(index, f"サンプル株式会社{chr(64 + index)}", "サンプル営業職") for index in range(1, 11)]
    document = ReportDocument("サンプル求人比較", jobs, [], {"第一優先": [1, 2, 3, 4, 5], "第二優先": [6, 7], "条件付き": [8, 9, 10]})
    verdicts = ("一致", "修正", "併記", "候補者情報未確認")
    findings = [
        VerificationFinding(
            rank=index,
            company=f"サンプル株式会社{chr(64 + index)}",
            position="サンプル営業職" if index != 2 else "サンプル事務職",
            source_pdf=fr"M:\manual-sample\求人PDF\サンプル株式会社{chr(64 + index)}（サンプル営業職）.pdf",
            field="年収",
            markdown_value="記載なし",
            pdf_value="記載なし",
            final_value="記載なし",
            verdict=verdicts[(index - 1) % len(verdicts)],
            message="架空データの照合結果です。",
        )
        for index in range(1, 5)
    ]
    counts = {name: sum(finding.verdict == name for finding in findings) for name in verdicts}
    return SimpleNamespace(document=document, findings=findings, verdict_counts=counts)


def _report_preview(output_dir: Path) -> None:
    from job_comparison_app.gui.controller import ViewModel

    root, view = _new_view()
    view.report_input.set(r"M:\manual-sample\比較資料\10求人比較.md")
    view.report_manifest.set(r"M:\manual-sample\比較資料\元PDF対応.json")
    view.report_output_pdf.set(r"M:\manual-sample\output\求人比較資料.pdf")
    view.report_verified_md.set(r"M:\manual-sample\output\求人比較資料_検証済み.md")
    view.report_verification_csv.set(r"M:\manual-sample\output\求人比較資料_検証結果.csv")
    view.report_date.set("2026-09-07")
    view.report_keep_html.set(True)
    view.report_show_details.set(True)
    view._toggle_report_details()
    _select_tab(view, 5)
    view.render(ViewModel(status="状態: 10求人の事前確認が完了しました（警告あり）。", tone="warning", report_plan=_report_plan()))
    view.report_preview_button.configure(state="normal")
    view.report_apply_button.configure(state="normal")
    view._sync_tab_layouts()
    _capture(root, "10-report-preview", output_dir)
    root.destroy()


def _copy_preview(output_dir: Path) -> None:
    from job_comparison_app.gui.controller import ViewModel
    from job_comparison_app.pdf_copy import PdfCopyRequest, PdfCopyEntry, PdfCopyPlan, CopyStatus

    root, view = _new_view()
    try:
        folder = Path(r"M:\manual-sample\求人PDF")
        names = ("サンプル株式会社A（営業職）.pdf", "サンプル株式会社B（事務職）.pdf", "サンプル株式会社C（営業職）.pdf")
        text = "\n".join(names)
        view.copy_panel.folder.set(str(folder))
        view.copy_panel.editor.insert("1.0", text)
        entries = tuple(PdfCopyEntry(i, name, name, status, destination=name if status != CopyStatus.MISSING else "")
                        for i, (name, status) in enumerate(zip(names, (CopyStatus.READY, CopyStatus.CONFLICT, CopyStatus.MISSING)), 1))
        plan = PdfCopyPlan(PdfCopyRequest(folder, text), folder / "対象pdf", entries, frozenset())
        _select_tab(view, 4)
        view.render(ViewModel(copy_plan=plan, status="事前確認が完了しました。未発見のファイル名を確認してください。", tone="warning"))
        view._sync_tab_layouts()
        _capture(root, "11-pdf-copy-preview", output_dir)
    finally:
        root.destroy()


SCENE_RUNNERS = {
    "11-pdf-copy-preview": _copy_preview,
    "00-workflow-overview": _workflow_scene,
    "01-start-candidate": _candidate_start,
    "02-candidate-example": _candidate_example,
    "03-recruit-initial-setup": _recruit_initial,
    "04-recruit-ready": _recruit_ready,
    "05-replace-confirmation": _replace_dialog,
    "06-general-comparison-confirmation": _general_dialog,
    "07-candidate-analysis-complete": _analysis_complete,
    "08-pdf-organize-preview": _pdf_preview,
    "09-pdf-text-preview": _export_preview,
    "10-report-preview": _report_preview,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=("all", *SCENES), default="all")
    parser.add_argument("--output-dir", type=Path, default=IMAGE_ROOT)
    parser.add_argument("--list-scenes", action="store_true")
    args = parser.parse_args(argv)
    if args.list_scenes:
        print("\n".join(SCENES))
        return 0
    _prepare_environment(args.output_dir)
    selected = SCENES if args.scene == "all" else (args.scene,)
    for scene in selected:
        SCENE_RUNNERS[scene](args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

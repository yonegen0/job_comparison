from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_comparison_app.errors import InputChangedError, InputValidationError
from job_comparison_app.fingerprints import ensure_fingerprints_unchanged, fingerprint_files
from job_comparison_app.gui.controller import Controller
from job_comparison_app.gui.pdf_outputs import PdfCsvConflictChoice
from job_comparison_app.models import RunLevel
from job_pdf_tool.models import DocumentResult
from job_pdf_tool.service import PdfOrganizationRequest, commit_pdf_organization, prepare_pdf_organization
from job_pdf_tool.transaction import ApplyError
from job_comparison_report.models import ReportDocument
from job_comparison_report.service import (
    ReportBuildPlan,
    ReportBuildRequest,
    commit_report_build,
)


class ImmediateRunner:
    def submit(self, callback):
        future = Future()
        try:
            future.set_result(callback())
        except BaseException as exc:
            future.set_exception(exc)
        return future

    def shutdown(self):
        pass


class PendingRunner:
    def __init__(self):
        self.future = Future()

    def submit(self, callback):
        return self.future

    def shutdown(self):
        pass


class FakeView:
    def __init__(self, *, confirm=True, conflict_choice=PdfCsvConflictChoice.REPLACE):
        self.models = []
        self.confirm = confirm
        self.notices = []
        self.closed = False
        self.conflict_choice = conflict_choice
        self.conflict_requests = []
        self.pdf_request = PdfOrganizationRequest(
            Path("C:/input"),
            output_csv=Path("C:/output/求人情報.csv"),
            report_csv=Path("C:/output/処理レポート.csv"),
        )
        self.report_request = SimpleNamespace()

    def render(self, model): self.models.append(model)
    def current_pdf_request(self): return self.pdf_request
    def current_report_request(self): return self.report_request
    def ask_pdf_csv_conflict(self, conflict):
        self.conflict_requests.append(conflict)
        return self.conflict_choice
    def ask_pdf_commit(self, plan): return self.confirm
    def ask_report_commit(self, plan): return self.confirm
    def show_info(self, title, message): self.notices.append(("info", title, message))
    def show_warning(self, title, message): self.notices.append(("warning", title, message))
    def show_error(self, title, message): self.notices.append(("error", title, message))
    def post(self, callback): callback()
    def close(self): self.closed = True


class FakeApplication:
    def __init__(self, plan):
        self.plan = plan
        self.commits = 0

    def prepare_pdf(self, request): return self.plan
    def commit_pdf(self, plan):
        self.commits += 1
        return SimpleNamespace(plan=plan, table=plan.table, level=plan.level)


class RecordingApplication:
    def __init__(self):
        self.requests = []
        self.commits = 0

    def prepare_pdf(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            request=request,
            level=RunLevel.SUCCESS,
            results=[],
            table=SimpleNamespace(stale_rows=[]),
            summary={"リネーム対象": 0, "CSV出力対象": 0, "CSV除外": 0},
            output_csv=request.output_csv,
            report_csv=request.report_csv,
            file_fingerprints=fingerprint_files([request.output_csv, request.report_csv]),
        )

    def commit_pdf(self, plan):
        self.commits += 1
        return SimpleNamespace(plan=plan, table=plan.table, level=plan.level)


class FailingThenRecordingApplication(RecordingApplication):
    def __init__(self):
        super().__init__()
        self.fail_next = True

    def prepare_pdf(self, request):
        if self.fail_next:
            self.fail_next = False
            raise InputValidationError("既存の求人情報CSVを読み込めません。")
        return super().prepare_pdf(request)


class FakeOpener:
    def open_folder(self, path): pass
    def open_file(self, path): pass


def _controller_plan() -> SimpleNamespace:
    return SimpleNamespace(
        level=RunLevel.SUCCESS,
        results=[],
        table=SimpleNamespace(stale_rows=[]),
        summary={
            "リネーム対象": 0,
            "CSV出力対象": 0,
            "CSV除外": 0,
        },
        output_csv=Path("C:/output/求人情報.csv"),
        report_csv=Path("C:/output/処理レポート.csv"),
        file_fingerprints=(),
    )


def test_file_fingerprint_detects_change(tmp_path: Path) -> None:
    target = tmp_path / "入力.md"
    target.write_text("before", encoding="utf-8")
    expected = fingerprint_files([target])
    target.write_text("after", encoding="utf-8")
    with pytest.raises(InputChangedError, match="変更"):
        ensure_fingerprints_unchanged(expected)


def test_pdf_commit_rejects_changed_inventory_before_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "求人.pdf"
    source.write_bytes(b"first")
    result = DocumentResult(source_path=source, sha256="", error="読取対象外")
    result.finalize_status()
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *args: [result])
    plan = prepare_pdf_organization(PdfOrganizationRequest(tmp_path))
    source.write_bytes(b"changed")
    monkeypatch.setattr(
        "job_pdf_tool.service.apply_transaction",
        lambda *args: pytest.fail("変更検出前に確定処理が呼ばれました"),
    )
    with pytest.raises(InputChangedError):
        commit_pdf_organization(plan)


def test_pdf_outputs_cannot_overwrite_an_input_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "求人.pdf"
    source.write_bytes(b"pdf")
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *args: pytest.fail("path validation must run first"))
    with pytest.raises(ValueError, match="入力ファイル"):
        prepare_pdf_organization(PdfOrganizationRequest(tmp_path, output_csv=source))


def test_pdf_preview_does_not_create_explicit_output_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *args: [])

    prepare_pdf_organization(
        PdfOrganizationRequest(
            tmp_path,
            output_csv=output_dir / "求人情報.csv",
            report_csv=output_dir / "処理レポート.csv",
        )
    )

    assert not output_dir.exists()


def test_pdf_commit_creates_output_directory_and_both_csvs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *args: [])
    plan = prepare_pdf_organization(
        PdfOrganizationRequest(
            tmp_path,
            output_csv=output_dir / "求人情報.csv",
            report_csv=output_dir / "処理レポート.csv",
        )
    )

    commit_pdf_organization(plan)

    assert (output_dir / "求人情報.csv").is_file()
    assert (output_dir / "処理レポート.csv").is_file()


def test_pdf_commit_honors_output_directory_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *args: [])
    plan = prepare_pdf_organization(
        PdfOrganizationRequest(
            tmp_path,
            output_csv=output_dir / "求人情報.csv",
            report_csv=output_dir / "処理レポート.csv",
        )
    )
    output_lock = output_dir / ".job_pdf_tool.lock"
    output_lock.write_text("other", encoding="ascii")

    with pytest.raises(ApplyError, match="ロック"):
        commit_pdf_organization(plan)

    assert output_lock.read_text(encoding="ascii") == "other"
    assert not (tmp_path / ".job_pdf_tool.lock").exists()


def test_report_commit_rejects_changed_input_before_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.md"
    manifest = tmp_path / "sources.json"
    source.write_text("before", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    request = ReportBuildRequest(
        source,
        manifest,
        tmp_path / "result.pdf",
        tmp_path / "result_検証済み.md",
        tmp_path / "result_検証結果.csv",
    )
    plan = ReportBuildPlan(
        request=request,
        document=ReportDocument("title", [], [], {}),
        findings=[],
        verdict_counts={},
        warning_count=0,
        level=RunLevel.SUCCESS,
        target_paths=(request.output_pdf, request.verified_markdown, request.verification_csv),
        html_target=None,
        input_fingerprints=fingerprint_files([source, manifest]),
        output_fingerprints=fingerprint_files(
            [request.output_pdf, request.verified_markdown, request.verification_csv]
        ),
    )
    source.write_text("after", encoding="utf-8")
    monkeypatch.setattr(
        "job_comparison_report.service.render_html",
        lambda *args: pytest.fail("変更検出前にレンダリングされました"),
    )
    with pytest.raises(InputChangedError):
        commit_report_build(plan)


def test_controller_preview_and_apply_use_structured_plan() -> None:
    plan = _controller_plan()
    view = FakeView(confirm=True)
    application = FakeApplication(plan)
    controller = Controller(
        view=view,
        application=application,
        output_opener=FakeOpener(),
        task_runner=ImmediateRunner(),
    )
    controller.preview_pdf()
    assert controller.model.pdf_plan is plan
    assert not controller.model.busy
    controller.apply_pdf()
    assert application.commits == 1
    assert controller.model.pdf_result is not None
    assert view.notices[-1][0] == "info"


def test_controller_cancel_does_not_commit() -> None:
    plan = _controller_plan()
    view = FakeView(confirm=False)
    application = FakeApplication(plan)
    controller = Controller(
        view=view,
        application=application,
        output_opener=FakeOpener(),
        task_runner=ImmediateRunner(),
    )
    controller.apply_pdf()
    assert application.commits == 0
    assert controller.model.status == "本実行をキャンセルしました。"


def test_controller_refuses_close_while_busy() -> None:
    view = FakeView()
    runner = PendingRunner()
    controller = Controller(
        view=view,
        application=FakeApplication(_controller_plan()),
        output_opener=FakeOpener(),
        task_runner=runner,
    )
    controller.preview_pdf()
    controller.request_close()
    assert not view.closed
    assert view.notices[-1][0] == "warning"


def test_controller_remembers_save_as_new_until_pdf_input_changes(tmp_path: Path) -> None:
    input_dir = tmp_path / "PDF"
    output_dir = tmp_path / "CSV"
    input_dir.mkdir()
    output_dir.mkdir()
    (output_dir / "求人情報.csv").write_text("existing", encoding="utf-8")
    view = FakeView(conflict_choice=PdfCsvConflictChoice.SAVE_AS_NEW)
    view.pdf_request = PdfOrganizationRequest(
        input_dir,
        output_csv=output_dir / "求人情報.csv",
        report_csv=output_dir / "処理レポート.csv",
    )
    application = RecordingApplication()
    controller = Controller(
        view=view,
        application=application,
        output_opener=FakeOpener(),
        task_runner=ImmediateRunner(),
    )

    controller.preview_pdf()
    controller.apply_pdf()

    assert len(view.conflict_requests) == 1
    assert [request.output_csv.name for request in application.requests] == [
        "求人情報(1).csv",
        "求人情報(1).csv",
    ]
    assert controller.model.pdf_output_mode == "別名で保存"

    controller.invalidate_pdf()
    controller.preview_pdf()
    assert len(view.conflict_requests) == 2


def test_controller_recomputes_numbered_pair_before_apply(tmp_path: Path) -> None:
    input_dir = tmp_path / "PDF"
    output_dir = tmp_path / "CSV"
    input_dir.mkdir()
    output_dir.mkdir()
    (output_dir / "求人情報.csv").write_text("existing", encoding="utf-8")
    view = FakeView(conflict_choice=PdfCsvConflictChoice.SAVE_AS_NEW)
    view.pdf_request = PdfOrganizationRequest(
        input_dir,
        output_csv=output_dir / "求人情報.csv",
        report_csv=output_dir / "処理レポート.csv",
    )
    application = RecordingApplication()
    controller = Controller(
        view=view,
        application=application,
        output_opener=FakeOpener(),
        task_runner=ImmediateRunner(),
    )

    controller.preview_pdf()
    (output_dir / "処理レポート(1).csv").write_text("claimed", encoding="utf-8")
    controller.apply_pdf()

    assert application.requests[0].output_csv.name == "求人情報(1).csv"
    assert application.requests[1].output_csv.name == "求人情報(2).csv"
    assert application.requests[1].report_csv.name == "処理レポート(2).csv"


def test_controller_csv_conflict_cancel_does_not_start_worker(tmp_path: Path) -> None:
    input_dir = tmp_path / "PDF"
    output_dir = tmp_path / "CSV"
    input_dir.mkdir()
    output_dir.mkdir()
    (output_dir / "処理レポート.csv").write_text("existing", encoding="utf-8")
    view = FakeView(conflict_choice=PdfCsvConflictChoice.CANCEL)
    view.pdf_request = PdfOrganizationRequest(
        input_dir,
        output_csv=output_dir / "求人情報.csv",
        report_csv=output_dir / "処理レポート.csv",
    )
    application = RecordingApplication()
    controller = Controller(
        view=view,
        application=application,
        output_opener=FakeOpener(),
        task_runner=ImmediateRunner(),
    )

    controller.preview_pdf()

    assert application.requests == []
    assert controller.model.status == "CSV出力の選択をキャンセルしました。"
    assert controller.model.pdf_plan is None


def test_controller_prepare_failure_allows_conflict_choice_again(tmp_path: Path) -> None:
    input_dir = tmp_path / "PDF"
    output_dir = tmp_path / "CSV"
    input_dir.mkdir()
    output_dir.mkdir()
    (output_dir / "求人情報.csv").write_text("invalid", encoding="utf-8")
    view = FakeView(conflict_choice=PdfCsvConflictChoice.REPLACE)
    view.pdf_request = PdfOrganizationRequest(
        input_dir,
        output_csv=output_dir / "求人情報.csv",
        report_csv=output_dir / "処理レポート.csv",
    )
    application = FailingThenRecordingApplication()
    controller = Controller(
        view=view,
        application=application,
        output_opener=FakeOpener(),
        task_runner=ImmediateRunner(),
    )

    controller.preview_pdf()
    view.conflict_choice = PdfCsvConflictChoice.SAVE_AS_NEW
    controller.preview_pdf()

    assert len(view.conflict_requests) == 2
    assert application.requests[0].output_csv.name == "求人情報(1).csv"

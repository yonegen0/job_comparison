from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_comparison_app.errors import InputChangedError
from job_comparison_app.fingerprints import ensure_fingerprints_unchanged, fingerprint_files
from job_comparison_app.gui.controller import Controller
from job_comparison_app.gui.pdf_outputs import LegacyCsvMigrationChoice, OutputConflictChoice
from job_comparison_app.models import RunLevel
from job_pdf_tool.models import DocumentResult
from job_pdf_tool.service import PdfOrganizationRequest, commit_pdf_organization, prepare_pdf_organization
from job_pdf_tool.transaction import ApplyError
from pdf_job_exporter.service import PdfTextExportRequest
from recruit_job_tool.gui.controller import CandidateProfileDecision


class ImmediateRunner:
    def submit(self, callback):
        future = Future()
        try: future.set_result(callback())
        except BaseException as exc: future.set_exception(exc)
        return future
    def shutdown(self): pass


class PendingRunner:
    def __init__(self): self.future = Future()
    def submit(self, callback): return self.future
    def shutdown(self): pass


class FakeView:
    def __init__(self, request: PdfOrganizationRequest, *, confirm=True,
                 conflict_choice=OutputConflictChoice.REPLACE,
                 migration_choice=LegacyCsvMigrationChoice.START_NEW):
        self.pdf_request = request
        self.export_request = PdfTextExportRequest(request.input_dir, request.input_dir / "output" / "PDF全文_統合テキスト化.md")
        self.report_request = SimpleNamespace()
        self.confirm = confirm; self.conflict_choice = conflict_choice; self.migration_choice = migration_choice
        self.models = []; self.notices = []; self.conflicts = []; self.closed = False
    def render(self, model): self.models.append(model)
    def current_pdf_request(self): return self.pdf_request
    def request_candidate_profile(self): return CandidateProfileDecision(proceed=True)
    def current_export_request(self): return self.export_request
    def current_report_request(self): return self.report_request
    def ask_organization_output_conflict(self, conflict): self.conflicts.append(conflict); return self.conflict_choice
    def choose_legacy_csv(self, candidates): return candidates[0]
    def ask_legacy_csv_migration(self, source, destination): return self.migration_choice
    def ask_export_output_conflict(self, conflict): return self.conflict_choice
    def ask_pdf_commit(self, plan): return self.confirm
    def ask_export_commit(self, plan): return self.confirm
    def ask_report_commit(self, plan): return self.confirm
    def show_info(self, title, message): self.notices.append(("info", title, message))
    def show_warning(self, title, message): self.notices.append(("warning", title, message))
    def show_error(self, title, message): self.notices.append(("error", title, message))
    def post(self, callback): callback()
    def close(self): self.closed = True


class RecordingApplication:
    def __init__(self): self.requests = []; self.commits = 0
    def prepare_pdf(self, request):
        self.requests.append(request)
        return SimpleNamespace(request=request, level=RunLevel.SUCCESS, results=[],
            job_info=SimpleNamespace(stale_jobs=[]), summary={"リネーム対象": 0, "JSON出力対象": 0, "JSON除外": 0},
            job_info_json=request.job_info_json, report_json=request.report_json,
            comparison_guide_path=request.comparison_guide_path, candidate_applied=request.candidate_profile is not None,
            file_fingerprints=fingerprint_files([request.job_info_json, request.report_json]))
    def commit_pdf(self, plan):
        self.commits += 1
        return SimpleNamespace(plan=plan, job_info=plan.job_info, level=plan.level)


class FakeOpener:
    def open_folder(self, path): pass
    def open_file(self, path): pass


def request(input_dir: Path, output_dir: Path) -> PdfOrganizationRequest:
    return PdfOrganizationRequest(input_dir, job_info_json=output_dir / "求人情報.json",
                                  report_json=output_dir / "処理レポート.json")


def test_file_fingerprint_detects_same_length_content_change(tmp_path: Path) -> None:
    target = tmp_path / "入力.md"; target.write_text("before", encoding="utf-8")
    expected = fingerprint_files([target]); target.write_text("after!", encoding="utf-8")
    with pytest.raises(InputChangedError, match="変更"):
        ensure_fingerprints_unchanged(expected)


def test_pdf_preview_does_not_create_output_directory(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "output"
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *args: [])
    prepare_pdf_organization(request(tmp_path, output))
    assert not output.exists()


def test_pdf_commit_creates_output_and_two_json_files(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "output"
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *args: [])
    plan = prepare_pdf_organization(request(tmp_path, output)); commit_pdf_organization(plan)
    assert (output / "求人情報.json").is_file()
    assert (output / "処理レポート.json").is_file()


def test_pdf_commit_rejects_changed_inventory(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "求人.pdf"; source.write_bytes(b"first")
    result = DocumentResult(source_path=source, sha256="", error="読取対象外"); result.finalize_status()
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *args: [result])
    plan = prepare_pdf_organization(request(tmp_path, tmp_path / "output")); source.write_bytes(b"changed")
    with pytest.raises(InputChangedError): commit_pdf_organization(plan)


def test_pdf_output_directory_lock_is_shared(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "output"; output.mkdir()
    monkeypatch.setattr("job_pdf_tool.service.analyze", lambda *args: [])
    plan = prepare_pdf_organization(request(tmp_path, output))
    lock = output / ".job_pdf_tool.lock"; lock.write_text("other", encoding="ascii")
    with pytest.raises(ApplyError, match="ロック"): commit_pdf_organization(plan)
    assert lock.read_text(encoding="ascii") == "other"


def test_controller_remembers_save_as_new_and_recomputes_pair(tmp_path: Path) -> None:
    input_dir = tmp_path / "PDF"; output = tmp_path / "JSON"; input_dir.mkdir(); output.mkdir()
    (output / "求人情報.json").write_text("existing", encoding="utf-8")
    view = FakeView(request(input_dir, output), conflict_choice=OutputConflictChoice.SAVE_AS_NEW)
    app = RecordingApplication(); controller = Controller(view=view, application=app, output_opener=FakeOpener(), task_runner=ImmediateRunner())
    controller.preview_pdf()
    (output / "処理レポート(1).json").write_text("claimed", encoding="utf-8")
    controller.apply_pdf()
    assert len(view.conflicts) == 1
    assert app.requests[0].job_info_json.name == "求人情報(1).json"
    assert app.requests[1].job_info_json.name == "求人情報(2).json"
    assert controller.model.pdf_output_mode == "別名で新規保存"


def test_controller_conflict_cancel_never_starts_worker(tmp_path: Path) -> None:
    input_dir = tmp_path / "PDF"; output = tmp_path / "JSON"; input_dir.mkdir(); output.mkdir()
    (output / "処理レポート.json").write_text("existing", encoding="utf-8")
    view = FakeView(request(input_dir, output), conflict_choice=OutputConflictChoice.CANCEL)
    app = RecordingApplication(); controller = Controller(view=view, application=app, output_opener=FakeOpener(), task_runner=ImmediateRunner())
    controller.preview_pdf()
    assert not app.requests and controller.model.status == "JSON出力の選択をキャンセルしました。"


def test_controller_apply_always_prepares_and_can_cancel_commit(tmp_path: Path) -> None:
    input_dir = tmp_path / "PDF"; output = tmp_path / "JSON"; input_dir.mkdir(); output.mkdir()
    view = FakeView(request(input_dir, output), confirm=False)
    app = RecordingApplication(); controller = Controller(view=view, application=app, output_opener=FakeOpener(), task_runner=ImmediateRunner())
    controller.apply_pdf()
    assert len(app.requests) == 1 and app.commits == 0
    assert controller.model.status == "本実行をキャンセルしました。"


def test_controller_refuses_close_while_busy(tmp_path: Path) -> None:
    input_dir = tmp_path / "PDF"; output = tmp_path / "JSON"; input_dir.mkdir(); output.mkdir()
    view = FakeView(request(input_dir, output)); runner = PendingRunner()
    controller = Controller(view=view, application=RecordingApplication(), output_opener=FakeOpener(), task_runner=runner)
    controller.preview_pdf(); controller.request_close()
    assert not view.closed and view.notices[-1][0] == "warning"

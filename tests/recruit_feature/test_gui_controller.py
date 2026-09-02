from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
import unittest

from recruit_job_tool.analysis_prompt.models import AnalysisGenerationResult
from recruit_job_tool.application import (
    ImportResult,
    ProviderHealth,
    ProviderStatus,
)
from recruit_job_tool.gui.controller import AI_REQUEST_TEXT, Controller
from recruit_job_tool.models import JobSource
from recruit_job_tool.workflow import ImportMode


def status(provider: JobSource, count: int = 1) -> ProviderStatus:
    return ProviderStatus(
        provider, ProviderHealth.READY, count, True, False, False, True, False
    )


class ImmediateRunner:
    def submit(self, callback):
        future = Future()
        try:
            future.set_result(callback())
        except BaseException as error:
            future.set_exception(error)
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
    def __init__(self):
        self.models = []
        self.dialogs = []
        self.copied = None
        self.closed = False

    def render(self, model): self.models.append(model)
    def ask_replace(self, spec): self.dialogs.append(spec); return True
    def ask_restore(self, spec): self.dialogs.append(spec); return True
    def show_info(self, spec): self.dialogs.append(spec)
    def show_warning(self, spec): self.dialogs.append(spec)
    def show_error(self, spec): self.dialogs.append(spec)
    def show_analysis_result(self, spec): self.dialogs.append(spec)
    def focus(self, control_id): pass
    def post(self, callback): callback()
    def copy_text(self, text): self.copied = text
    def close(self): self.closed = True


class FakePicker:
    def __init__(self, path): self.path = path
    def choose_html(self, *, initial_directory): return self.path


class FakeApplication:
    def __init__(self):
        self.paths = SimpleNamespace(output_dir=Path("C:/safe/output"))
        self.calls = []

    def get_provider_status(self, *, provider): return status(provider)
    def ensure_output_directory(self, *, provider):
        return Path(f"C:/safe/{provider.value.lower()}/output")
    def import_saved_html(self, *, provider, input_path, mode):
        self.calls.append((provider, input_path, mode))
        return ImportResult(provider, mode, 2, 2, Path("jobs.csv"), True, ())
    def create_analysis_files(self, *, provider):
        self.calls.append((provider, "analysis"))
        return AnalysisGenerationResult(
            provider, 1, 1, Path("safe_jobs.json"), Path("safe_guide.md"), 10, 10, ()
        )


class FakeOpener:
    def __init__(self): self.paths = []
    def open(self, path): self.paths.append(path)


class ControllerTests(unittest.TestCase):
    def make_controller(self, runner=None):
        self.view = FakeView()
        self.app = FakeApplication()
        self.opener = FakeOpener()
        return Controller(
            view=self.view,
            file_picker=FakePicker(Path("C:/input/jobs.html")),
            application=self.app,
            output_folder_opener=self.opener,
            task_runner=runner or ImmediateRunner(),
        )

    def test_start_loads_both_statuses_and_defaults_to_jobins(self) -> None:
        controller = self.make_controller()
        controller.start()
        self.assertEqual(controller.model.active_provider, JobSource.JOBINS)
        self.assertFalse(controller.model.busy)
        self.assertEqual(controller.model.providers[JobSource.CAREERBANK].status.current_count, 1)

    def test_selection_and_modes_are_independent_between_tabs(self) -> None:
        controller = self.make_controller()
        controller.start()
        controller.choose_html()
        controller.change_mode(ImportMode.MERGE)
        controller.select_tab(JobSource.CAREERBANK)
        self.assertIsNone(controller.model.providers[JobSource.CAREERBANK].selected_path)
        self.assertEqual(controller.model.providers[JobSource.JOBINS].mode, ImportMode.MERGE)

    def test_import_clears_selection_and_resets_safe_mode(self) -> None:
        controller = self.make_controller()
        controller.start()
        controller.choose_html()
        controller.change_mode(ImportMode.MERGE)
        controller.import_selected()
        state = controller.model.providers[JobSource.JOBINS]
        self.assertIsNone(state.selected_path)
        self.assertEqual(state.mode, ImportMode.REPLACE)
        self.assertEqual(len(self.app.calls), 1)

    def test_analysis_dialog_contains_basenames_only_and_clipboard_is_fixed(self) -> None:
        controller = self.make_controller()
        controller.start()
        controller.generate_analysis()
        self.assertEqual(self.app.calls[-1][1], "analysis")
        dialog = self.view.dialogs[-1]
        self.assertEqual(dialog.guide_basename, "safe_guide.md")
        self.assertEqual(dialog.json_basename, "safe_jobs.json")
        controller.copy_ai_request()
        self.assertEqual(self.view.copied, AI_REQUEST_TEXT)
        self.assertNotIn("C:/", self.view.copied)

    def test_analysis_starts_without_candidate_profile_dialog(self) -> None:
        controller = self.make_controller()
        controller.start()
        controller.generate_analysis()
        self.assertEqual(self.app.calls[-1][1], "analysis")

    def test_open_output_folder_uses_active_provider_location(self) -> None:
        controller = self.make_controller()
        controller.start()
        controller.select_tab(JobSource.CAREERBANK)
        controller.open_output_folder()
        self.assertEqual(
            self.opener.paths,
            [Path("C:/safe/careerbank/output")],
        )

    def test_busy_close_is_rejected(self) -> None:
        controller = self.make_controller(PendingRunner())
        controller.start()
        controller.request_close()
        self.assertFalse(self.view.closed)
        self.assertEqual(self.view.dialogs[-1].title, "処理中です")


if __name__ == "__main__":
    unittest.main()

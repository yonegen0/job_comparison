from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest

from recruit_job_tool.analysis_prompt.models import AnalysisGenerationResult
from recruit_job_tool.application import (
    ImportResult,
    ProviderHealth,
    ProviderStatus,
)
from recruit_job_tool.candidate_profile import CandidateProfile
from recruit_job_tool.gui.controller import (
    AI_REQUEST_TEXT,
    CANDIDATE_AI_REQUEST_TEXT,
    CandidateProfileDecision,
    Controller,
)
from recruit_job_tool.models import JobSource
from recruit_job_tool.workflow import ImportMode


def status(
    provider: JobSource,
    count: int | None = 1,
    *,
    health: ProviderHealth = ProviderHealth.READY,
    output_configured: bool = True,
    output_error: bool = False,
) -> ProviderStatus:
    return ProviderStatus(
        provider,
        health,
        count,
        True,
        False,
        False,
        output_configured,
        output_error,
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
        self.replace_prompts = []
        self.candidate_decision = CandidateProfileDecision(proceed=True)

    def render(self, model): self.models.append(model)
    def ask_replace(self, spec):
        self.replace_prompts.append(spec)
        self.dialogs.append(spec)
        return True
    def ask_restore(self, spec): self.dialogs.append(spec); return True
    def show_info(self, spec): self.dialogs.append(spec)
    def show_warning(self, spec): self.dialogs.append(spec)
    def show_error(self, spec): self.dialogs.append(spec)
    def show_analysis_result(self, spec): self.dialogs.append(spec)
    def request_candidate_profile(self): return self.candidate_decision
    def focus(self, control_id): pass
    def post(self, callback): callback()
    def copy_text(self, text): self.copied = text
    def close(self): self.closed = True


class FakePicker:
    def __init__(self, path): self.path = path
    def choose_html(self, *, initial_directory):
        return (self.path,) if isinstance(self.path, Path) else (self.path or ())


class FakeApplication:
    def __init__(self):
        self.paths = SimpleNamespace(output_dir=Path("C:/safe/output"))
        self.calls = []
        self.statuses = {provider: status(provider) for provider in JobSource}
        self.import_results = []
        self.import_error = None

    def get_provider_status(self, *, provider): return self.statuses[provider]
    def ensure_output_directory(self, *, provider):
        return Path(f"C:/safe/{provider.value.lower()}/output")
    def import_saved_html_batch(self, *, provider, input_paths, mode):
        input_path = input_paths[0]
        self.calls.append((provider, input_path, mode))
        if self.import_error is not None:
            raise self.import_error
        if self.import_results:
            return self.import_results.pop(0)
        return ImportResult(provider, mode, 2, 2, Path("jobs.csv"), True, ())
    def create_analysis_files(self, *, provider, candidate_profile=None, include_guide=True):
        self.calls.append((provider, "analysis", candidate_profile))
        return AnalysisGenerationResult(
            provider, 1, 1, Path("safe_jobs.json"), Path("safe_guide.md") if include_guide else None,
            10, 10 if include_guide else None, ()
        )


class FakeOpener:
    def __init__(self): self.paths = []
    def open(self, path): self.paths.append(path)


class ControllerTests(unittest.TestCase):
    def make_controller(self, runner=None):
        self.view = FakeView()
        self.app = FakeApplication()
        self.opener = FakeOpener()
        self.picker = FakePicker(Path("C:/input/jobs.html"))
        return Controller(
            view=self.view,
            file_picker=self.picker,
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
        self.assertEqual(controller.model.providers[JobSource.CAREERBANK].selected_paths, ())
        self.assertEqual(controller.model.providers[JobSource.JOBINS].mode, ImportMode.MERGE)

    def test_import_clears_selection_and_keeps_merge_mode(self) -> None:
        controller = self.make_controller()
        controller.start()
        controller.choose_html()
        controller.change_mode(ImportMode.MERGE)
        controller.import_selected()
        state = controller.model.providers[JobSource.JOBINS]
        self.assertEqual(state.selected_paths, ())
        self.assertEqual(state.mode, ImportMode.MERGE)
        self.assertEqual(
            self.app.calls,
            [(JobSource.JOBINS, Path("C:/input/jobs.html"), ImportMode.MERGE)],
        )
        self.assertEqual(self.view.replace_prompts, [])

    def test_zero_result_clears_selection_and_keeps_merge_mode(self) -> None:
        controller = self.make_controller()
        self.app.import_results.append(
            ImportResult(
                JobSource.JOBINS,
                ImportMode.MERGE,
                0,
                1,
                None,
                False,
                (),
            )
        )
        controller.start()
        controller.choose_html()
        controller.change_mode(ImportMode.MERGE)

        controller.import_selected()

        state = controller.model.providers[JobSource.JOBINS]
        self.assertEqual(state.selected_paths, ())
        self.assertIs(state.mode, ImportMode.MERGE)
        self.assertEqual(self.view.dialogs[-1].title, "取り込み結果")

    def test_replace_import_clears_selection_and_keeps_replace_mode(self) -> None:
        controller = self.make_controller()
        controller.start()
        controller.choose_html()

        controller.import_selected()

        state = controller.model.providers[JobSource.JOBINS]
        self.assertEqual(state.selected_paths, ())
        self.assertIs(state.mode, ImportMode.REPLACE)
        self.assertEqual(len(self.view.replace_prompts), 1)
        self.assertEqual(self.app.calls[-1][2], ImportMode.REPLACE)

    def test_consecutive_imports_keep_merge_without_reselection(self) -> None:
        controller = self.make_controller()
        controller.start()
        controller.choose_html()
        controller.change_mode(ImportMode.MERGE)
        controller.import_selected()

        self.picker.path = Path("C:/input/jobs-next.html")
        controller.choose_html()
        controller.import_selected()

        self.assertEqual(
            self.app.calls,
            [
                (JobSource.JOBINS, Path("C:/input/jobs.html"), ImportMode.MERGE),
                (JobSource.JOBINS, Path("C:/input/jobs-next.html"), ImportMode.MERGE),
            ],
        )
        self.assertIs(
            controller.model.providers[JobSource.JOBINS].mode,
            ImportMode.MERGE,
        )
        self.assertEqual(
            controller.model.providers[JobSource.JOBINS].selected_paths, ()
        )
        self.assertEqual(self.view.replace_prompts, [])

    def test_completed_import_updates_only_its_provider_mode(self) -> None:
        controller = self.make_controller()
        controller.start()
        controller.change_mode(ImportMode.MERGE)
        controller.choose_html()
        controller.select_tab(JobSource.CAREERBANK)
        controller.choose_html()

        controller.select_tab(JobSource.JOBINS)
        controller.import_selected()

        self.assertIs(
            controller.model.providers[JobSource.JOBINS].mode,
            ImportMode.MERGE,
        )
        self.assertIs(
            controller.model.providers[JobSource.CAREERBANK].mode,
            ImportMode.REPLACE,
        )
        self.assertTrue(
            controller.model.providers[JobSource.CAREERBANK].selected_paths
        )

    def test_import_failure_keeps_selected_path_and_merge_mode(self) -> None:
        controller = self.make_controller()
        self.app.import_error = RuntimeError("sensitive C:/input/jobs.html")
        controller.start()
        controller.choose_html()
        controller.change_mode(ImportMode.MERGE)

        controller.import_selected()

        state = controller.model.providers[JobSource.JOBINS]
        self.assertEqual(state.selected_paths, (Path("C:/input/jobs.html"),))
        self.assertIs(state.mode, ImportMode.MERGE)
        self.assertFalse(controller.model.busy)
        self.assertNotIn("C:/input/jobs.html", controller.model.status_text)
        self.assertNotIn("C:/input/jobs.html", self.view.dialogs[-1].message)

    def test_import_completion_normalizes_merge_for_unsafe_statuses(self) -> None:
        completion_statuses = (
            (
                status(
                    JobSource.JOBINS,
                    1,
                    output_configured=False,
                ),
                ImportMode.MERGE,
            ),
            (
                status(
                    JobSource.JOBINS,
                    101,
                    health=ProviderHealth.REPLACE_REQUIRED,
                ),
                ImportMode.REPLACE,
            ),
            (
                status(
                    JobSource.JOBINS,
                    None,
                    health=ProviderHealth.UNAVAILABLE,
                ),
                ImportMode.REPLACE,
            ),
            (
                status(JobSource.JOBINS, 1, output_error=True),
                ImportMode.REPLACE,
            ),
        )

        for completion_status, expected_mode in completion_statuses:
            with self.subTest(status=completion_status):
                controller = self.make_controller()
                controller.start()
                controller.choose_html()
                controller.change_mode(ImportMode.MERGE)
                self.app.statuses[JobSource.JOBINS] = completion_status

                controller.import_selected()

                state = controller.model.providers[JobSource.JOBINS]
                self.assertEqual(state.selected_paths, ())
                self.assertIs(state.mode, expected_mode)

    def test_new_controller_starts_each_provider_in_replace_mode(self) -> None:
        controller = self.make_controller()

        self.assertTrue(
            all(
                provider.mode is ImportMode.REPLACE
                for provider in controller.model.providers.values()
            )
        )

    def test_change_mode_keeps_safe_replace_for_restricted_statuses(self) -> None:
        controller = self.make_controller()
        controller.start()
        original = controller.model.providers[JobSource.JOBINS]
        restricted = (
            ProviderStatus(
                JobSource.JOBINS,
                ProviderHealth.REPLACE_REQUIRED,
                101,
                False,
                False,
                False,
                True,
                False,
            ),
            ProviderStatus(
                JobSource.JOBINS,
                ProviderHealth.READY,
                1,
                False,
                False,
                False,
                True,
                True,
            ),
        )

        for provider_status in restricted:
            with self.subTest(status=provider_status):
                providers = dict(controller.model.providers)
                providers[JobSource.JOBINS] = replace(
                    original,
                    mode=ImportMode.REPLACE,
                    status=provider_status,
                )
                controller.model = replace(controller.model, providers=providers)
                controller.change_mode(ImportMode.MERGE)
                self.assertIs(
                    controller.model.providers[JobSource.JOBINS].mode,
                    ImportMode.REPLACE,
                )

    def test_busy_controller_does_not_change_import_mode(self) -> None:
        controller = self.make_controller(PendingRunner())
        controller.start()
        controller.change_mode(ImportMode.MERGE)
        self.assertTrue(controller.model.busy)
        self.assertIs(
            controller.model.providers[JobSource.JOBINS].mode,
            ImportMode.REPLACE,
        )

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

    def test_candidate_snapshot_is_passed_and_changes_completion_copy(self) -> None:
        controller = self.make_controller()
        profile = CandidateProfile(
            age="34歳",
            gender="回答しない",
            education="4年制大学卒業",
            work_history="営業",
            qualifications="普通運転免許",
        )
        self.view.candidate_decision = CandidateProfileDecision(True, profile)
        controller.start()
        controller.generate_analysis()
        self.assertIs(self.app.calls[-1][2], profile)
        self.assertTrue(self.view.dialogs[-1].candidate_applied)
        controller.copy_ai_request()
        self.assertEqual(self.view.copied, CANDIDATE_AI_REQUEST_TEXT)
        self.assertNotIn("34歳", self.view.copied)

    def test_candidate_cancel_does_not_start_worker_or_busy(self) -> None:
        runner = PendingRunner()
        controller = self.make_controller(runner)
        controller.start()
        runner.future.set_result({
            provider: status(provider) for provider in JobSource
        })
        self.view.candidate_decision = CandidateProfileDecision(False)
        calls_before = list(self.app.calls)
        controller.generate_analysis()
        self.assertEqual(self.app.calls, calls_before)
        self.assertFalse(controller.model.busy)

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

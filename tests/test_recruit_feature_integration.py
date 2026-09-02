from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
import time
import uuid

from job_comparison_app.application import JobComparisonApplication
from job_comparison_app.gui.controller import Controller
import job_comparison_app.gui.recruit_feature as recruit_feature_module
from recruit_job_tool.app_paths import AppPaths
from recruit_job_tool.migration import MigrationManager
from recruit_job_tool.single_instance import SingleInstanceLock

from job_comparison_app.gui.recruit_feature import (
    RecruitFeatureCoordinator,
    RecruitFeaturePhase,
)
from job_comparison_app.gui.view import TkView, WindowsOutputOpener


class ImmediateRunner:
    def submit(self, callback):
        future = Future()
        try:
            future.set_result(callback())
        except BaseException as error:
            future.set_exception(error)
        return future


class _Dialogs:
    def __init__(self) -> None:
        self.notices = []

    def show(self, title, message, kind) -> None:
        self.notices.append((title, message, kind))


class FakePanel:
    def __init__(self) -> None:
        self.dialogs = _Dialogs()
        self.controller = None
        self.phases = []
        self.models = []
        self._initialize = None
        self._state_callback = None

    def set_initialize_command(self, callback) -> None:
        self._initialize = callback

    def set_state_callback(self, callback) -> None:
        self._state_callback = callback

    def set_phase(self, phase, message, **kwargs) -> None:
        self.phases.append((phase, message, kwargs))

    def bind_controller(self, controller) -> None:
        self.controller = controller

    def render(self, model) -> None:
        self.models.append(model)
        if self._state_callback is not None:
            self._state_callback(model)

    def post(self, callback) -> None:
        callback()

    def ask_for_legacy_folder(self) -> bool:
        return False

    def ask_adopt_existing(self) -> bool:
        return True

    def ask_migrate(self):
        return False

    def choose_legacy_folder(self):
        return None

    def ask_replace(self, spec) -> bool:
        return True

    def ask_restore(self, spec) -> bool:
        return True

    def show_info(self, spec) -> None:
        pass

    def show_warning(self, spec) -> None:
        pass

    def show_error(self, spec) -> None:
        pass

    def show_analysis_result(self, spec) -> None:
        pass

    def focus(self, control_id) -> None:
        pass

    def copy_text(self, text) -> None:
        pass

    def close(self) -> None:
        pass

    def choose_html(self, *, initial_directory):
        return None

    def open(self, path) -> None:
        pass


def _coordinator(panel: FakePanel):
    states = []
    coordinator = RecruitFeatureCoordinator(
        panel=panel,
        task_runner=ImmediateRunner(),
        on_global_state=lambda busy, status, tone: states.append((busy, status, tone)),
    )
    return coordinator, states


def test_probe_shows_in_tab_setup_without_writing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    panel = FakePanel()
    coordinator, states = _coordinator(panel)

    coordinator.probe()

    assert coordinator.model.phase is RecruitFeaturePhase.UNINITIALIZED
    assert coordinator.controller is None
    assert not (tmp_path / "RecruitJobTool").exists()
    assert panel.phases[-1][2]["can_initialize"] is True
    assert states[-1][0] is False
    coordinator.shutdown()


def test_initialize_fresh_then_probe_reuses_existing_marker(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    panel = FakePanel()
    coordinator, _ = _coordinator(panel)

    coordinator.initialize()

    paths = AppPaths.from_local_app_data()
    assert MigrationManager(paths).is_ready()
    assert coordinator.controller is not None
    assert coordinator.model.phase is RecruitFeaturePhase.READY
    coordinator.shutdown()

    second_panel = FakePanel()
    second, _ = _coordinator(second_panel)
    second.probe()
    assert second.controller is not None
    assert second.model.phase is RecruitFeaturePhase.READY
    second.shutdown()


def test_existing_standalone_mutex_locks_only_recruit_feature(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    owner = SingleInstanceLock().acquire()
    try:
        panel = FakePanel()
        coordinator, states = _coordinator(panel)
        coordinator.probe()

        assert coordinator.model.phase is RecruitFeaturePhase.LOCKED
        assert coordinator.controller is None
        assert panel.phases[-1][2]["can_retry"] is True
        assert states[-1][0] is False
        coordinator.shutdown()
    finally:
        owner.release()


def _pump_until(root, predicate, *, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("recruit feature did not reach the expected state")


def test_real_tk_start_keeps_both_notebook_selections_and_reaches_ready(
    monkeypatch, tmp_path: Path, tk_window
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    paths = AppPaths.from_local_app_data()
    manager = MigrationManager(paths)
    manager.create_fresh(declined=False)
    paths.initialize()
    mutex_name = f"Local\\RecruitJobTool-GuiTest-{uuid.uuid4().hex}"
    monkeypatch.setattr(
        recruit_feature_module,
        "SingleInstanceLock",
        lambda: SingleInstanceLock(mutex_name),
    )

    root = tk_window
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    view = TkView(root)
    controller = Controller(
        view=view,
        application=JobComparisonApplication(),
        output_opener=WindowsOutputOpener(),
    )
    view.bind_controller(controller)

    try:
        controller.start()
        assert view.notebook.select()
        assert view.notebook.index(view.notebook.select()) == 0

        _pump_until(
            root,
            lambda: (
                controller.recruit is not None
                and controller.recruit.model.phase is RecruitFeaturePhase.READY
                and not controller.recruit.model.busy
                and all(
                    status is not None
                    for status in controller.recruit.model.providers.values()
                )
            ),
        )

        assert errors == []
        assert view.notebook.select()
        assert view.notebook.index(view.notebook.select()) == 0
        assert view.notebook.instate(["!disabled"])
        assert view.recruit_panel.provider_notebook.select()
        assert view.recruit_panel.notebook_provider().value == "JOBINS"
        assert view.recruit_panel.provider_notebook.instate(["!disabled"])
        assert all(
            view.recruit_panel.provider_notebook.tab(index, "state") == "normal"
            for index in range(2)
        )
    finally:
        if controller.recruit is not None:
            controller.recruit.shutdown()
        controller.task_runner.shutdown()
        view._dispatcher.close()


def test_controller_start_failure_unbinds_controller_and_releases_lock(
    monkeypatch,
) -> None:
    panel = FakePanel()
    coordinator, states = _coordinator(panel)

    class RecordingLock:
        released = False

        def release(self) -> None:
            self.released = True

    class FailingController:
        def __init__(self, **kwargs) -> None:
            del kwargs

        def start(self) -> None:
            raise RuntimeError("start failed")

    lock = RecordingLock()
    coordinator._lock = lock
    coordinator._token = 1
    monkeypatch.setattr(recruit_feature_module, "RecruitController", FailingController)
    future = Future()
    future.set_result(object())

    coordinator._activation_complete(1, future)

    assert coordinator.controller is None
    assert panel.controller is None
    assert lock.released is True
    assert coordinator.model.phase is RecruitFeaturePhase.ERROR
    assert coordinator.model.can_retry is True
    assert coordinator.model.busy is False
    assert states[-1][0] is False

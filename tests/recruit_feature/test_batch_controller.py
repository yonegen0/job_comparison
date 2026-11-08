from pathlib import Path
from unittest.mock import patch

import pytest

from recruit_job_tool.errors import HtmlBatchInputError, JobLimitError
from recruit_job_tool.models import JobSource
from recruit_job_tool.workflow import ImportMode
from tests.recruit_feature import test_gui_controller as support


@pytest.fixture
def harness():
    harness = support.ControllerTests()
    harness.controller = harness.make_controller()
    harness.controller.start()
    return harness


def test_five_paths_sorted_and_imported_as_one_operation(harness):
    c = harness.controller
    paths = tuple(Path(f"C:/input/{i}.html") for i in range(5))
    harness.picker.path = paths[::-1]
    c.choose_html()
    assert c.model.providers[JobSource.JOBINS].selected_paths == paths
    with patch.object(harness.app, "import_saved_html_batch", wraps=harness.app.import_saved_html_batch) as run:
        c.import_selected()
    run.assert_called_once_with(provider=JobSource.JOBINS, input_paths=paths, mode=ImportMode.REPLACE)
    assert len(harness.view.replace_prompts) == 1
    assert "5個" in harness.view.replace_prompts[0].message
    assert "5個" in harness.view.dialogs[-1].message
    assert c.model.providers[JobSource.JOBINS].selected_paths == ()


def test_cancel_and_six_files_preserve_selection_and_directory(harness):
    c = harness.controller
    c.choose_html()
    before = c.model.providers[JobSource.JOBINS].selected_paths
    directory = c._last_directory
    for selection in [(), tuple(Path(f"C:/other/{i}.html") for i in range(6))]:
        harness.picker.path = selection
        c.choose_html()
        assert c.model.providers[JobSource.JOBINS].selected_paths == before
        assert c._last_directory == directory
    assert "5個まで" in harness.view.dialogs[-1].message
    harness.picker.path = (Path("C:/other/new.html"),)
    c.choose_html()
    assert c.model.providers[JobSource.JOBINS].selected_paths == harness.picker.path


@pytest.mark.parametrize("error", [HtmlBatchInputError("bad.html", "VAL001"), JobLimitError(101, 100)])
def test_batch_error_retains_selection_and_explains_failure(harness, error):
    c = harness.controller
    harness.picker.path = (Path("C:/input/a.html"), Path("C:/input/bad.html"))
    c.choose_html()
    c.change_mode(ImportMode.MERGE)
    harness.app.import_error = error
    c.import_selected()
    state = c.model.providers[JobSource.JOBINS]
    assert state.selected_paths == harness.picker.path
    assert state.mode is ImportMode.MERGE
    assert not c.model.busy
    message = harness.view.dialogs[-1].message
    assert "取り込まれていません" in message
    assert ("bad.html" if isinstance(error, HtmlBatchInputError) else "101件") in message


def test_busy_import_does_not_allow_reselection_or_second_submission(harness):
    c = harness.controller
    c.choose_html()
    runner = support.PendingRunner()
    c.task_runner = runner
    with patch.object(runner, "submit", wraps=runner.submit) as submit:
        c.import_selected()
        harness.picker.path = (Path("C:/other/new.html"),)
        c.choose_html()
        c.import_selected()
    assert submit.call_count == 1
    assert c.model.providers[JobSource.JOBINS].selected_paths == (Path("C:/input/jobs.html"),)

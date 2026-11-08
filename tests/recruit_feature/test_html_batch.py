from pathlib import Path
from unittest.mock import patch

import pytest

from recruit_job_tool.app_paths import AppPaths
from recruit_job_tool.application import RecruitJobApplication, sort_html_paths
from recruit_job_tool.errors import CsvExportError, HtmlBatchInputError, JobLimitError
from recruit_job_tool.models import JobSource
from recruit_job_tool.providers.jobins import JobinsSearchHtmlImporter
from recruit_job_tool.providers.careerbank import CareerBankSearchHtmlImporter
from recruit_job_tool.workflow import ImportMode
from tests.recruit_feature.support import make_raw_job


@pytest.fixture
def app(tmp_path):
    paths = AppPaths.from_root(tmp_path / "state")
    paths.initialize()
    return RecruitJobApplication(paths)


def html(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def snapshot(root):
    # Logs may record failed attempts; data, backups and output settings must not change.
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and "logs" not in p.parts}


def raw(provider, job_id, title="sample"):
    return make_raw_job(source=provider, source_job_id=job_id, title=title)


def importer(provider):
    return (patch.object(JobinsSearchHtmlImporter, "load_all") if provider is JobSource.JOBINS
            else patch.object(CareerBankSearchHtmlImporter, "load"))


@pytest.mark.parametrize("provider", list(JobSource))
@pytest.mark.parametrize("mode", list(ImportMode))
def test_combines_five_files_deduplicates_and_restores_once(app, tmp_path, provider, mode):
    original = html(tmp_path / "original.html")
    with importer(provider) as load:
        load.return_value = [raw(provider, "existing")]
        app.import_saved_html_batch(provider=provider, input_paths=(original,), mode=ImportMode.REPLACE)
        previous = app._workflow.repository.load_all(provider)
        paths = tuple(html(tmp_path / "new" / f"{i}.html") for i in range(5))
        load.side_effect = lambda path: [raw(provider, path.stem), raw(provider, "same", path.stem)]
        with patch.object(app._workflow.repository, "begin_run", wraps=app._workflow.repository.begin_run) as begin, \
             patch.object(app._workflow, "export_current", wraps=app._workflow.export_current) as export:
            result = app.import_saved_html_batch(provider=provider, input_paths=paths[::-1], mode=mode)
        assert result.input_count == 6
        assert result.current_count == (6 if mode is ImportMode.REPLACE else 7)
        assert begin.call_count == export.call_count == 1
        assert result.csv_path.parent == (paths[0].parent if mode is ImportMode.REPLACE else tmp_path) / "output"
        saved = app._workflow.repository.load_all(provider)
        assert next(job for job in saved if job.source_job_id == "same").job_title == "4"
        app.restore_previous(provider=provider)
        assert app._workflow.repository.load_all(provider) == previous


@pytest.mark.parametrize("provider", list(JobSource))
@pytest.mark.parametrize("bad_index", [0, 2, 4])
def test_failed_input_preserves_all_persistent_data(app, tmp_path, provider, bad_index):
    paths = tuple(html(tmp_path / f"{i}.html") for i in range(5))
    with importer(provider) as load:
        load.return_value = [raw(provider, "original")]
        app.import_saved_html_batch(provider=provider, input_paths=(paths[0],), mode=ImportMode.REPLACE)
        before = snapshot(tmp_path)
        def read(path):
            if path == paths[bad_index]:
                raise OSError("private internal detail")
            return [raw(provider, path.stem)]
        load.side_effect = read
        with pytest.raises(HtmlBatchInputError) as caught:
            app.import_saved_html_batch(provider=provider, input_paths=paths, mode=ImportMode.REPLACE)
        assert caught.value.filename == paths[bad_index].name
        assert "private" not in str(caught.value)
        assert snapshot(tmp_path) == before


@pytest.mark.parametrize("provider", list(JobSource))
@pytest.mark.parametrize("mode", list(ImportMode))
def test_limit_checks_union_before_any_output_changes(app, tmp_path, provider, mode):
    with importer(provider) as load:
        load.return_value = [raw(provider, str(i)) for i in range(100)]
        result = app.import_saved_html_batch(provider=provider, input_paths=(html(tmp_path / "a.html"),), mode=ImportMode.REPLACE)
        assert result.current_count == 100
        before = snapshot(tmp_path)
        load.return_value = ([raw(provider, "extra")] if mode is ImportMode.MERGE else
                             [raw(provider, str(i)) for i in range(101)])
        with pytest.raises(JobLimitError) as caught:
            app.import_saved_html_batch(provider=provider, input_paths=(tmp_path / "other" / "b.html",), mode=mode)
        assert caught.value.projected_count == 101
        assert snapshot(tmp_path) == before


@pytest.mark.parametrize("provider", list(JobSource))
def test_zero_results_and_csv_failure(app, tmp_path, provider):
    paths = (html(tmp_path / "a.html"), html(tmp_path / "b.html"))
    with importer(provider) as load:
        load.return_value = [raw(provider, "original")]
        app.import_saved_html_batch(provider=provider, input_paths=paths, mode=ImportMode.REPLACE)
        before = snapshot(tmp_path)
        load.return_value = []
        assert not app.import_saved_html_batch(provider=provider, input_paths=paths, mode=ImportMode.REPLACE).changed
        assert snapshot(tmp_path) == before
        load.side_effect = [[], [raw(provider, "new")]]
        with patch.object(app._workflow, "export_current", side_effect=CsvExportError()):
            result = app.import_saved_html_batch(provider=provider, input_paths=paths, mode=ImportMode.REPLACE)
        assert result.changed and result.current_count == 1
        assert result.csv_path is None and result.warnings


@pytest.mark.parametrize("count", [0, 6])
def test_rejects_invalid_batch_size_without_creating_output(app, tmp_path, count):
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        app.import_saved_html_batch(provider=JobSource.JOBINS,
                                    input_paths=tuple(tmp_path / f"{i}.html" for i in range(count)),
                                    mode=ImportMode.REPLACE)
    assert snapshot(tmp_path) == before


def test_path_order_is_case_insensitive_and_uses_full_path_for_ties():
    paths = tuple(map(Path, ["z/A.html", "b/a.html", "b/2.html", "b/10.html"]))
    assert sort_html_paths(paths) == (paths[3], paths[2], paths[1], paths[0])


@pytest.mark.parametrize("provider", list(JobSource))
def test_real_html_batch_and_wrong_media_are_atomic(app, tmp_path, provider):
    fixtures = Path(__file__).parent / "fixtures"
    fixture = fixtures / f"{provider.value.lower()}_search_results.html"
    paths = tuple(html(tmp_path / f"{i}.html") for i in range(5))
    for path in paths:
        path.write_bytes(fixture.read_bytes())
    result = app.import_saved_html_batch(provider=provider, input_paths=paths, mode=ImportMode.REPLACE)
    assert result.input_count == result.current_count == 3
    other = JobSource.CAREERBANK if provider is JobSource.JOBINS else JobSource.JOBINS
    paths[2].write_bytes((fixtures / f"{other.value.lower()}_search_results.html").read_bytes())
    before = snapshot(tmp_path)
    with pytest.raises(HtmlBatchInputError):
        app.import_saved_html_batch(provider=provider, input_paths=paths, mode=ImportMode.REPLACE)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("provider", list(JobSource))
def test_real_html_over_100_is_not_silently_truncated(app, tmp_path, provider):
    fixture = (Path(__file__).parent / "fixtures" / f"{provider.value.lower()}_search_results.html").read_text(encoding="utf-8")
    if provider is JobSource.JOBINS:
        marker = '<div class="jb-relative jb-rounded-[8px] jb-shadow-card">'
        prefix, card, *_ = fixture.split(marker)
        cards = "".join(marker + card.replace("JOB-001", f"JOB-{i:04d}").replace("job-001", f"job-{i:04d}") for i in range(101))
        document = prefix.replace("<h2>3</h2>", "<h2>101</h2>") + cards + "</section></body></html>"
    else:
        marker = '<div class="panel panel-default">'
        prefix, card, *_ = fixture.split(marker)
        cards = "".join(marker + card.replace("900001", str(900000 + i)) for i in range(101))
        document = prefix.replace("3件", "101件") + cards + "</article></body></html>"
    path = tmp_path / "large.html"
    path.write_text(document, encoding="utf-8")
    before = snapshot(tmp_path)
    with pytest.raises(JobLimitError) as caught:
        app.import_saved_html_batch(provider=provider, input_paths=(path,), mode=ImportMode.REPLACE)
    assert caught.value.projected_count == 101
    assert snapshot(tmp_path) == before

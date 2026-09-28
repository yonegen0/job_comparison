from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from recruit_job_tool.app_paths import AppPaths
from recruit_job_tool.application import ProviderHealth, RecruitJobApplication
from recruit_job_tool.candidate_profile import CandidateProfile
from recruit_job_tool.constants import MAX_CURRENT_JOBS
from recruit_job_tool.csv_exporter import CsvExporter
from recruit_job_tool.models import JobSource
from recruit_job_tool.errors import CsvExportError, OutputLocationError
from recruit_job_tool.output_location import OutputLocationStore, StagedOutputLocation
from recruit_job_tool.repository import Repository
from recruit_job_tool.workflow import ImportMode

from tests.recruit_feature.support import make_job


FIXTURES = Path(__file__).parent / "fixtures"


class ApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = AppPaths.from_root(Path(self.temporary.name).resolve())
        self.paths.initialize()
        self.app = RecruitJobApplication(self.paths)
        self.input_dir = Path(self.temporary.name).resolve() / "saved-html"
        self.input_dir.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fixture(self, name: str, *, folder: str = "saved-html") -> Path:
        directory = Path(self.temporary.name).resolve() / folder
        directory.mkdir(exist_ok=True)
        destination = directory / name
        shutil.copyfile(FIXTURES / name, destination)
        return destination

    def test_jobins_import_updates_work_csv_and_status_without_config(self) -> None:
        result = self.app.import_saved_html(
            provider=JobSource.JOBINS,
            input_path=self.fixture("jobins_search_results.html"),
            mode=ImportMode.REPLACE,
        )
        self.assertTrue(result.changed)
        self.assertEqual(result.current_count, 3)
        self.assertTrue(result.csv_path.is_file())
        self.assertEqual(result.csv_path.parent, self.input_dir / "output")
        self.assertFalse((self.paths.output_dir / "jobins_jobs.csv").exists())
        status = self.app.get_provider_status(provider=JobSource.JOBINS)
        self.assertEqual(status.health, ProviderHealth.READY)
        self.assertEqual(status.current_count, 3)
        self.assertTrue(status.csv_exists)
        self.assertTrue(status.output_configured)
        self.assertFalse(status.output_error)

    def test_replace_switches_but_merge_retains_provider_output_directory(self) -> None:
        first = self.fixture("jobins_search_results.html", folder="first")
        second = self.fixture("jobins_search_results.html", folder="second")
        self.app.import_saved_html(
            provider=JobSource.JOBINS,
            input_path=first,
            mode=ImportMode.REPLACE,
        )
        merged = self.app.import_saved_html(
            provider=JobSource.JOBINS,
            input_path=second,
            mode=ImportMode.MERGE,
        )
        self.assertEqual(merged.csv_path.parent, first.parent / "output")
        replaced = self.app.import_saved_html(
            provider=JobSource.JOBINS,
            input_path=second,
            mode=ImportMode.REPLACE,
        )
        self.assertEqual(replaced.csv_path.parent, second.parent / "output")
        restarted = RecruitJobApplication(self.paths)
        self.assertEqual(
            restarted.get_output_directory(provider=JobSource.JOBINS),
            second.parent / "output",
        )

    def test_unconfigured_output_operations_are_rejected(self) -> None:
        with self.assertRaises(OutputLocationError):
            self.app.export_csv(provider=JobSource.JOBINS)
        with self.assertRaises(OutputLocationError):
            self.app.restore_previous(provider=JobSource.JOBINS)
        with self.assertRaises(OutputLocationError):
            self.app.create_analysis_files(provider=JobSource.JOBINS)
        status = self.app.get_provider_status(provider=JobSource.JOBINS)
        self.assertFalse(status.output_configured)
        self.assertFalse(status.output_error)

    def test_explicit_analysis_paths_do_not_require_or_change_location(self) -> None:
        explicit = Path(self.temporary.name).resolve() / "explicit"
        csv_path = CsvExporter(explicit).export(
            JobSource.JOBINS,
            [
                make_job(
                    source_job_id="explicit",
                    source_url="https://jobins.jp/agent/jobs/explicit",
                )
            ],
        )
        result = self.app.create_analysis_files(
            provider=JobSource.JOBINS,
            input_path=csv_path,
            json_path=explicit / "jobs.json",
            include_guide=False,
        )
        self.assertTrue(result.json_path.is_file())
        self.assertIsNone(result.guide_path)
        self.assertFalse(
            self.app.get_provider_status(provider=JobSource.JOBINS).output_configured
        )

    def test_corrupt_location_blocks_merge_but_replace_repairs_it(self) -> None:
        state = self.paths.state_dir / "jobins-output-location-v1.json"
        state.write_text("broken\n", encoding="utf-8", newline="\n")
        html = self.fixture("jobins_search_results.html")
        with self.assertRaises(OutputLocationError):
            self.app.import_saved_html(
                provider=JobSource.JOBINS,
                input_path=html,
                mode=ImportMode.MERGE,
            )
        self.assertEqual(
            Repository(self.paths.work_dir, self.paths.backup_dir).load_all(
                JobSource.JOBINS
            ),
            [],
        )
        result = self.app.import_saved_html(
            provider=JobSource.JOBINS,
            input_path=html,
            mode=ImportMode.REPLACE,
        )
        self.assertEqual(result.csv_path.parent, html.parent / "output")
        self.assertFalse(
            self.app.get_provider_status(provider=JobSource.JOBINS).output_error
        )

    def test_output_preflight_failure_does_not_change_jobs_or_location(self) -> None:
        first = self.fixture("jobins_search_results.html", folder="first")
        second = self.fixture("jobins_search_results.html", folder="second")
        self.app.import_saved_html(
            provider=JobSource.JOBINS,
            input_path=first,
            mode=ImportMode.REPLACE,
        )
        before = Repository(self.paths.work_dir, self.paths.backup_dir).load_all(
            JobSource.JOBINS
        )
        (second.parent / "output").write_text("occupied", encoding="utf-8")
        with self.assertRaises(OutputLocationError):
            self.app.import_saved_html(
                provider=JobSource.JOBINS,
                input_path=second,
                mode=ImportMode.REPLACE,
            )
        self.assertEqual(
            Repository(self.paths.work_dir, self.paths.backup_dir).load_all(
                JobSource.JOBINS
            ),
            before,
        )
        self.assertEqual(
            self.app.get_output_directory(provider=JobSource.JOBINS),
            first.parent / "output",
        )

    def test_location_commit_failure_keeps_saved_jobs_without_publishing_csv(self) -> None:
        html = self.fixture("jobins_search_results.html")
        with patch.object(
            StagedOutputLocation,
            "commit",
            side_effect=OutputLocationError("injected"),
        ):
            result = self.app.import_saved_html(
                provider=JobSource.JOBINS,
                input_path=html,
                mode=ImportMode.REPLACE,
            )
        self.assertTrue(result.changed)
        self.assertEqual(result.current_count, 3)
        self.assertIsNone(result.csv_path)
        self.assertTrue(result.warnings)
        self.assertFalse(
            (html.parent / "output" / "jobins_jobs.csv").exists()
        )
        self.assertFalse(
            (self.paths.state_dir / "jobins-output-location-v1.json").exists()
        )
        self.assertEqual(
            list(self.paths.state_dir.glob(".*.tmp")),
            [],
        )

    def test_csv_failure_keeps_saved_jobs_and_new_location(self) -> None:
        html = self.fixture("jobins_search_results.html")
        with patch.object(
            self.app._workflow,
            "export_current",
            side_effect=CsvExportError("injected"),
        ):
            result = self.app.import_saved_html(
                provider=JobSource.JOBINS,
                input_path=html,
                mode=ImportMode.REPLACE,
            )
        self.assertEqual(result.current_count, 3)
        self.assertIsNone(result.csv_path)
        self.assertTrue(result.warnings)
        self.assertEqual(
            self.app.get_output_directory(provider=JobSource.JOBINS),
            html.parent / "output",
        )

    def test_deleted_configured_output_is_recreated_for_export(self) -> None:
        html = self.fixture("jobins_search_results.html")
        imported = self.app.import_saved_html(
            provider=JobSource.JOBINS,
            input_path=html,
            mode=ImportMode.REPLACE,
        )
        imported.csv_path.unlink()
        imported.csv_path.parent.rmdir()
        exported = self.app.export_csv(provider=JobSource.JOBINS)
        self.assertTrue(exported.path.is_file())
        self.assertEqual(exported.path.parent, html.parent / "output")

    def test_status_accepts_newly_supported_legacy_count(self) -> None:
        repository = Repository(self.paths.work_dir, self.paths.backup_dir)
        repository.replace_many(
            JobSource.JOBINS,
            [make_job(source_job_id=str(index)) for index in range(61)],
        )
        status = self.app.get_provider_status(provider=JobSource.JOBINS)
        self.assertEqual(status.health, ProviderHealth.READY)
        self.assertEqual(status.current_count, 61)

    def test_status_marks_readable_legacy_over_maximum_as_replace_required(self) -> None:
        repository = Repository(self.paths.work_dir, self.paths.backup_dir)
        repository.replace_many(
            JobSource.JOBINS,
            [
                make_job(source_job_id=str(index))
                for index in range(MAX_CURRENT_JOBS + 1)
            ],
        )
        status = self.app.get_provider_status(provider=JobSource.JOBINS)
        self.assertEqual(status.health, ProviderHealth.REPLACE_REQUIRED)
        self.assertEqual(status.current_count, MAX_CURRENT_JOBS + 1)

    def test_restore_preflights_previous_before_changing_work(self) -> None:
        repository = Repository(self.paths.work_dir, self.paths.backup_dir)
        repository.replace_many(JobSource.JOBINS, [make_job(source_job_id="old")])
        repository.begin_run(JobSource.JOBINS)
        repository.replace_many(JobSource.JOBINS, [make_job(source_job_id="new")])
        output = Path(self.temporary.name).resolve() / "restore" / "output"
        output.parent.mkdir()
        store = OutputLocationStore(self.paths.state_dir)
        store.prepare_directory(output)
        store.stage(JobSource.JOBINS, output).commit()
        result = self.app.restore_previous(provider=JobSource.JOBINS)
        self.assertEqual(result.count, 1)
        self.assertEqual(repository.load_all(JobSource.JOBINS)[0].source_job_id, "old")

    def test_analysis_uses_current_data_and_independent_default_outputs(self) -> None:
        self.app.import_saved_html(
            provider=JobSource.JOBINS,
            input_path=self.fixture("jobins_search_results.html"),
            mode=ImportMode.REPLACE,
        )
        result = self.app.create_analysis_files(provider=JobSource.JOBINS)
        self.assertEqual(result.job_count, 3)
        self.assertTrue(result.guide_path.is_file())
        self.assertTrue(result.json_path.is_file())
        self.assertIn("_analysis_guide_", result.guide_path.stem)
        self.assertIn("_analysis_jobs_", result.json_path.stem)
        self.assertEqual(result.guide_path.parent, self.input_dir / "output")

    def test_candidate_analysis_uses_common_sequence_and_keeps_json_profile_free(self) -> None:
        self.app.import_saved_html(
            provider=JobSource.JOBINS,
            input_path=self.fixture("jobins_search_results.html"),
            mode=ImportMode.REPLACE,
        )
        profile = CandidateProfile(
            age="34歳",
            gender="回答しない",
            education="4年制大学卒業",
            work_history="営業",
            qualifications="普通運転免許",
        )
        first = self.app.create_analysis_files(
            provider=JobSource.JOBINS,
            candidate_profile=profile,
        )
        second = self.app.create_analysis_files(
            provider=JobSource.JOBINS,
            candidate_profile=profile,
        )
        assert first.json_path.stem.replace("_jobs_", "_") == first.guide_path.stem.replace("_guide_", "_")
        assert second.json_path.stem.endswith("_01")
        assert second.guide_path.stem.endswith("_01")
        assert "34歳" not in first.json_path.read_text(encoding="utf-8")
        assert "34歳" in first.guide_path.read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

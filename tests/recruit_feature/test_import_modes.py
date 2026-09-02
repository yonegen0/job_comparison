from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from recruit_job_tool.app_paths import AppPaths
from recruit_job_tool.constants import MAX_CURRENT_JOBS
from recruit_job_tool.errors import JobValidationError
from recruit_job_tool.models import JobSource
from recruit_job_tool.workflow import ImportMode, JobWorkflow

from tests.recruit_feature.support import make_job


class ImportWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = AppPaths.from_root(Path(self.temporary.name).resolve())
        self.workflow = JobWorkflow(self.paths)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def jobs(self, count: int, *, start: int = 0):
        return [make_job(source_job_id=str(index)) for index in range(start, start + count)]

    def test_replace_and_merge_preserve_expected_order(self) -> None:
        self.workflow.save_import(JobSource.JOBINS, self.jobs(2), ImportMode.REPLACE)
        updated = make_job(source_job_id="1", title="更新")
        added = make_job(source_job_id="2")
        result = self.workflow.save_import(JobSource.JOBINS, [updated, added], ImportMode.MERGE)
        self.assertEqual(result.current_count, 3)
        saved = self.workflow.repository.load_all(JobSource.JOBINS)
        self.assertEqual([job.source_job_id for job in saved], ["0", "1", "2"])
        self.assertEqual(saved[1].job_title, "更新")

    def test_maximum_boundary_and_preflight_failure_do_not_rotate_backup(self) -> None:
        self.workflow.save_import(
            JobSource.JOBINS,
            self.jobs(MAX_CURRENT_JOBS),
            ImportMode.REPLACE,
        )
        work = self.paths.work_dir / "jobins.jsonl"
        backup = self.paths.backup_dir / "jobins.previous.jsonl"
        before_work = work.read_bytes()
        before_backup = backup.read_bytes()
        with self.assertRaises(JobValidationError):
            self.workflow.save_import(
                JobSource.JOBINS,
                [make_job(source_job_id="new")],
                ImportMode.MERGE,
            )
        self.assertEqual(work.read_bytes(), before_work)
        self.assertEqual(backup.read_bytes(), before_backup)

    def test_maximum_existing_id_update_succeeds(self) -> None:
        self.workflow.save_import(
            JobSource.JOBINS,
            self.jobs(MAX_CURRENT_JOBS),
            ImportMode.REPLACE,
        )
        result = self.workflow.save_import(
            JobSource.JOBINS,
            [make_job(source_job_id="0", title="更新")],
            ImportMode.MERGE,
        )
        self.assertEqual(result.current_count, MAX_CURRENT_JOBS)

    def test_merge_to_maximum_succeeds(self) -> None:
        self.workflow.save_import(
            JobSource.JOBINS,
            self.jobs(MAX_CURRENT_JOBS - 1),
            ImportMode.REPLACE,
        )
        result = self.workflow.save_import(
            JobSource.JOBINS,
            [make_job(source_job_id="new")],
            ImportMode.MERGE,
        )
        self.assertEqual(result.current_count, MAX_CURRENT_JOBS)

    def test_maximum_can_be_exported_and_restored(self) -> None:
        self.workflow.save_import(
            JobSource.JOBINS,
            self.jobs(MAX_CURRENT_JOBS),
            ImportMode.REPLACE,
        )
        csv_path, exported_count = self.workflow.export_current(
            JobSource.JOBINS, output_dir=self.paths.output_dir
        )
        self.assertTrue(csv_path.is_file())
        self.assertEqual(exported_count, MAX_CURRENT_JOBS)

        self.workflow.save_import(
            JobSource.JOBINS,
            [make_job(source_job_id="replacement")],
            ImportMode.REPLACE,
        )
        _, restored_count, _, _ = self.workflow.restore_and_sync_csv(
            JobSource.JOBINS, output_dir=self.paths.output_dir
        )
        self.assertEqual(restored_count, MAX_CURRENT_JOBS)

    def test_previous_over_maximum_is_rejected_before_restore(self) -> None:
        self.workflow.repository.replace_many(
            JobSource.JOBINS,
            self.jobs(MAX_CURRENT_JOBS + 1),
        )
        self.workflow.repository.begin_run(JobSource.JOBINS)
        self.workflow.repository.replace_many(
            JobSource.JOBINS,
            [make_job(source_job_id="current")],
        )

        with self.assertRaises(JobValidationError):
            self.workflow.restore_and_sync_csv(
                JobSource.JOBINS, output_dir=self.paths.output_dir
            )
        current = self.workflow.repository.load_all(JobSource.JOBINS)
        self.assertEqual([job.source_job_id for job in current], ["current"])


if __name__ == "__main__":
    unittest.main()

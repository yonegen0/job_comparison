from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
import json
from pathlib import Path
import tempfile
import unittest

from recruit_job_tool.app_paths import AppPaths
from recruit_job_tool.constants import MAX_CURRENT_JOBS
from recruit_job_tool.analysis_prompt.renderer import render_analysis_prompt
from recruit_job_tool.errors import MigrationError
from recruit_job_tool.migration import MARKER_NAME, MigrationManager
from recruit_job_tool.models import JobSource
from recruit_job_tool.repository import Repository

from tests.recruit_feature.support import make_candidate_profile, make_job


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.source = self.base / "legacy"
        self.destination = self.base / "destination" / "RecruitJobTool"
        self.paths = AppPaths.from_root(self.destination)
        self.manager = MigrationManager(self.paths)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def source_repository(self) -> Repository:
        return Repository(self.source / "data" / "work", self.source / "data" / "backup")

    def write_analysis_pair(self) -> tuple[Path, Path]:
        self.source.mkdir(parents=True, exist_ok=True)
        (self.source / "config.toml").write_text("", encoding="utf-8")
        output = self.source / "data" / "output"
        output.mkdir(parents=True, exist_ok=True)
        markdown = output / "jobins_analysis_prompt_20260829_120000.md"
        json_file = markdown.with_suffix(".json")
        job = {
            "job_id": "JOBINS:1",
            "source_url": None,
            "company": None,
            "job_title": "匿名求人",
            "industry": None,
            "location": None,
            "salary": {},
            "application_requirements": {},
            "referral_reward": {},
        }
        json_content = json.dumps([job], ensure_ascii=False) + "\n"
        json_file.write_text(json_content, encoding="utf-8", newline="\n")
        markdown.write_text(
            render_analysis_prompt(
                provider=JobSource.JOBINS,
                candidate_profile=make_candidate_profile(),
                job_count=1,
                company_count=0,
                generated_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
                job_data_filename=json_file.name,
                job_data_sha256=hashlib.sha256(json_content.encode("utf-8")).hexdigest(),
            ),
            encoding="utf-8",
            newline="\n",
        )
        return markdown, json_file

    def test_fresh_initialization_is_atomic_and_ready(self) -> None:
        self.destination.parent.mkdir()
        self.manager.create_fresh()
        self.assertTrue(self.manager.is_ready())
        marker = self.manager.read_marker(self.paths.state_dir / MARKER_NAME)
        self.assertEqual(marker["status"], "fresh")
        self.assertIsNone(marker["result"])

    def test_migrates_current_and_previous_without_changing_source(self) -> None:
        repository = self.source_repository()
        repository.replace_many(JobSource.JOBINS, [make_job(source_job_id="old")])
        repository.begin_run(JobSource.JOBINS)
        repository.replace_many(JobSource.JOBINS, [make_job(source_job_id="new")])
        source_work = self.source / "data" / "work" / "jobins.jsonl"
        before = (source_work.read_bytes(), source_work.stat().st_mtime_ns)
        inventory = self.manager.inspect_source(self.source)
        self.destination.parent.mkdir(exist_ok=True)
        self.manager.migrate(inventory, source_kind="selected")
        self.assertTrue(self.manager.is_ready())
        migrated = Repository(self.paths.work_dir, self.paths.backup_dir)
        self.assertEqual(migrated.load_all(JobSource.JOBINS)[0].source_job_id, "new")
        self.assertTrue((self.paths.output_dir / "jobins_jobs.csv").is_file())
        self.assertEqual((source_work.read_bytes(), source_work.stat().st_mtime_ns), before)

    def test_corrupt_current_recovers_only_inside_destination(self) -> None:
        repository = self.source_repository()
        repository.replace_many(JobSource.JOBINS, [make_job(source_job_id="old")])
        repository.begin_run(JobSource.JOBINS)
        current = self.source / "data" / "work" / "jobins.jsonl"
        current.write_bytes(b"broken\n")
        before = current.read_bytes()
        inventory = self.manager.inspect_source(self.source)
        self.assertEqual(inventory.current[JobSource.JOBINS].action, "recovered")
        self.destination.parent.mkdir(exist_ok=True)
        self.manager.migrate(inventory, source_kind="automatic")
        self.assertEqual(current.read_bytes(), before)
        restored = Repository(self.paths.work_dir, self.paths.backup_dir).load_all(JobSource.JOBINS)
        self.assertEqual(restored[0].source_job_id, "old")

    def test_current_over_maximum_is_not_truncated(self) -> None:
        self.source_repository().replace_many(
            JobSource.JOBINS,
            [
                make_job(source_job_id=str(index))
                for index in range(MAX_CURRENT_JOBS + 1)
            ],
        )
        before = (self.source / "data" / "work" / "jobins.jsonl").read_bytes()
        with self.assertRaises(MigrationError) as caught:
            self.manager.inspect_source(self.source)
        self.assertEqual(caught.exception.error_code, "MIG003")
        self.assertEqual((self.source / "data" / "work" / "jobins.jsonl").read_bytes(), before)

    def test_current_at_maximum_is_accepted(self) -> None:
        self.source_repository().replace_many(
            JobSource.JOBINS,
            [
                make_job(source_job_id=str(index))
                for index in range(MAX_CURRENT_JOBS)
            ],
        )
        inventory = self.manager.inspect_source(self.source)
        self.assertEqual(
            len(inventory.current[JobSource.JOBINS].jobs),
            MAX_CURRENT_JOBS,
        )

    def test_one_complete_crash_stage_is_committed(self) -> None:
        self.destination.parent.mkdir()
        self.manager.create_fresh()
        stage = self.destination.parent / ("RecruitJobTool.migrating-" + "a" * 32)
        os.replace(self.destination, stage)
        self.assertTrue(self.manager.recover_crash_stage())
        self.assertTrue(self.manager.is_ready())

    def test_marker_unknown_key_and_bool_count_are_rejected(self) -> None:
        self.destination.parent.mkdir()
        self.manager.create_fresh()
        marker_path = self.paths.state_dir / MARKER_NAME
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["unknown"] = True
        marker_path.write_text(
            json.dumps(marker, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        with self.assertRaises(MigrationError) as caught:
            self.manager.read_marker(marker_path)
        self.assertEqual(caught.exception.error_code, "MIG001")

    def test_v7_analysis_pair_is_accepted_and_bad_profile_hash_is_ignored(self) -> None:
        markdown, _ = self.write_analysis_pair()
        inventory = self.manager.inspect_source(self.source)
        self.assertEqual(len(inventory.analysis_pairs), 1)
        content = markdown.read_text(encoding="utf-8")
        content = content.replace(
            'candidate_profile_sha256: "',
            'candidate_profile_sha256: "0',
            1,
        )
        markdown.write_text(content, encoding="utf-8", newline="\n")
        inventory = self.manager.inspect_source(self.source)
        self.assertEqual(inventory.analysis_pairs, ())

    def test_v6_analysis_pair_remains_accepted(self) -> None:
        markdown, _ = self.write_analysis_pair()
        content = markdown.read_text(encoding="utf-8")
        content = content.replace("job-comparison-v7", "job-comparison-v6", 1)
        markdown.write_text(content, encoding="utf-8", newline="\n")
        inventory = self.manager.inspect_source(self.source)
        self.assertEqual(len(inventory.analysis_pairs), 1)


if __name__ == "__main__":
    unittest.main()

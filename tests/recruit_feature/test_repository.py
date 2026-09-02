from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from recruit_job_tool.errors import (
    JobValidationError,
    RepositoryCorruptionError,
    RepositoryError,
)
from recruit_job_tool.models import JobSource
from recruit_job_tool.repository import Repository

from tests.recruit_feature.support import make_job, later


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.work = root / "work"
        self.backup = root / "backup"
        self.repository = Repository(self.work, self.backup)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_provider_files_are_separate_and_utf8_lf(self) -> None:
        jobins = make_job()
        careerbank = make_job(
            source=JobSource.CAREERBANK,
            source_job_id="456",
            title="システムエンジニア",
            source_url="https://example.invalid/careerbank/456",
        )
        self.repository.upsert_many(JobSource.JOBINS, [jobins])
        self.repository.upsert_many(JobSource.CAREERBANK, [careerbank])

        self.assertEqual(len(self.repository.load_all("jobins")), 1)
        self.assertEqual(len(self.repository.load_all("CAREERBANK")), 1)
        jobins_bytes = (self.work / "jobins.jsonl").read_bytes()
        self.assertIn("法人営業".encode(), jobins_bytes)
        self.assertNotIn(b"\r\n", jobins_bytes)
        self.assertTrue(jobins_bytes.endswith(b"\n"))

    def test_upsert_replaces_same_source_id_with_latest_snapshot(self) -> None:
        old = make_job(title="旧タイトル", batch_id="old")
        new = make_job(
            title="新タイトル",
            batch_id="new",
            acquired_at=later(),
            last_verified_at=later(),
        )
        self.repository.upsert_many("jobins", [old])
        result = self.repository.upsert_many("jobins", [new])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].job_title, "新タイトル")
        self.assertEqual(result[0].batch_id, "new")
        self.assertEqual(result[0].acquired_at, later())

    def test_replace_many_keeps_only_incoming_jobs_in_input_order(self) -> None:
        self.repository.upsert_many(
            "jobins",
            [
                make_job(source_job_id="A", title="旧A"),
                make_job(source_job_id="B", title="旧B"),
            ],
        )
        incoming = [
            make_job(source_job_id="B", title="新B", batch_id="new"),
            make_job(source_job_id="C", title="新C", batch_id="new"),
        ]

        result = self.repository.replace_many("jobins", incoming)

        self.assertEqual(
            [(job.source_job_id, job.job_title) for job in result],
            [("B", "新B"), ("C", "新C")],
        )
        self.assertEqual(self.repository.load_all("jobins"), result)

    def test_replace_many_rejects_duplicate_ids_before_write(self) -> None:
        self.repository.upsert_many(
            "jobins", [make_job(source_job_id="A", title="既存")]
        )
        current = self.work / "jobins.jsonl"
        old_bytes = current.read_bytes()
        duplicate = make_job(source_job_id="B", title="重複")

        with self.assertRaises(JobValidationError):
            self.repository.replace_many("jobins", [duplicate, duplicate])

        self.assertEqual(current.read_bytes(), old_bytes)

    def test_begin_run_and_restore_previous_are_one_provider_only(self) -> None:
        old = make_job(title="旧")
        self.repository.upsert_many("jobins", [old])
        self.repository.begin_run("jobins")
        self.repository.upsert_many("jobins", [make_job(title="新")])
        restored = self.repository.restore_previous("jobins")
        self.assertEqual([job.job_title for job in restored], ["旧"])

    def test_first_run_can_be_rolled_back_to_empty(self) -> None:
        self.repository.begin_run("jobins")
        self.repository.upsert_many("jobins", [make_job()])
        self.assertEqual(self.repository.restore_previous("jobins"), [])
        self.assertEqual((self.work / "jobins.jsonl").read_bytes(), b"")

    def test_load_previous_is_read_only(self) -> None:
        self.repository.upsert_many("jobins", [make_job(source_job_id="OLD")])
        self.repository.begin_run("jobins")
        self.repository.upsert_many("jobins", [make_job(source_job_id="NEW")])
        work_path = self.work / "jobins.jsonl"
        before = work_path.read_bytes()
        previous = self.repository.load_previous("jobins")
        self.assertEqual(previous[0].source_job_id, "OLD")
        self.assertEqual(work_path.read_bytes(), before)

    def test_corrupt_current_is_restored_from_valid_previous(self) -> None:
        expected = make_job(title="復旧対象")
        self.repository.upsert_many("jobins", [expected])
        self.repository.begin_run("jobins")
        (self.work / "jobins.jsonl").write_text(
            '{"broken":true}', encoding="utf-8", newline="\n"
        )
        restored = self.repository.load_all("jobins")
        self.assertEqual([job.job_title for job in restored], ["復旧対象"])
        self.assertEqual(len(self.repository.load_all("jobins")), 1)

    def test_corrupt_current_without_valid_previous_fails(self) -> None:
        self.work.mkdir(parents=True)
        (self.work / "jobins.jsonl").write_text(
            "not-json\n", encoding="utf-8", newline="\n"
        )
        with self.assertRaises(RepositoryCorruptionError):
            self.repository.load_all("jobins")

    def test_corrupt_current_and_corrupt_previous_fail_closed(self) -> None:
        self.work.mkdir(parents=True)
        self.backup.mkdir(parents=True)
        (self.work / "jobins.jsonl").write_text(
            "not-json\n", encoding="utf-8", newline="\n"
        )
        (self.backup / "jobins.previous.jsonl").write_text(
            "also-not-json\n", encoding="utf-8", newline="\n"
        )
        with self.assertRaises(RepositoryCorruptionError):
            self.repository.load_all("jobins")

    def test_invalid_jsonl_variants_are_never_partially_loaded(self) -> None:
        valid_record = make_job().to_record()
        valid_line = json.dumps(valid_record, ensure_ascii=False, separators=(",", ":"))
        unknown_schema = dict(valid_record)
        unknown_schema["schema_version"] = 999
        nan_record = dict(valid_record)
        nan_record["salary_min"] = float("nan")
        variants = {
            "blank_line": f"{valid_line}\n\n",
            "duplicate_id": f"{valid_line}\n{valid_line}\n",
            "unknown_schema": json.dumps(unknown_schema, ensure_ascii=False) + "\n",
            "nan": json.dumps(nan_record, ensure_ascii=False, allow_nan=True) + "\n",
        }
        self.work.mkdir(parents=True, exist_ok=True)
        current = self.work / "jobins.jsonl"
        for name, payload in variants.items():
            with self.subTest(name=name):
                current.write_text(payload, encoding="utf-8", newline="\n")
                with self.assertRaises(RepositoryCorruptionError):
                    self.repository.load_all("jobins")

    def test_fsync_failure_keeps_old_file_and_removes_temporary(self) -> None:
        old = make_job(title="旧")
        self.repository.upsert_many("jobins", [old])
        old_bytes = (self.work / "jobins.jsonl").read_bytes()
        with patch("recruit_job_tool.repository.os.fsync", side_effect=OSError("disk")):
            with self.assertRaises(RepositoryError):
                self.repository.upsert_many("jobins", [make_job(title="新")])
        self.assertEqual((self.work / "jobins.jsonl").read_bytes(), old_bytes)
        self.assertEqual(list(self.work.glob("*.tmp")), [])

    def test_replace_failure_keeps_old_file_and_removes_temporary(self) -> None:
        self.repository.upsert_many("jobins", [make_job(title="旧")])
        old_bytes = (self.work / "jobins.jsonl").read_bytes()
        with patch("recruit_job_tool.repository.os.replace", side_effect=OSError("busy")):
            with self.assertRaises(RepositoryError):
                self.repository.upsert_many("jobins", [make_job(title="新")])
        self.assertEqual((self.work / "jobins.jsonl").read_bytes(), old_bytes)
        self.assertEqual(list(self.work.glob("*.tmp")), [])

    def test_directory_creation_failure_is_a_repository_error(self) -> None:
        with patch(
            "recruit_job_tool.repository.Path.mkdir",
            side_effect=OSError("denied"),
        ):
            with self.assertRaises(RepositoryError):
                self.repository.upsert_many("jobins", [make_job()])

    def test_cross_provider_job_is_rejected(self) -> None:
        with self.assertRaises(JobValidationError):
            self.repository.upsert_many(
                "jobins",
                [
                    make_job(
                        source=JobSource.CAREERBANK,
                        source_job_id="456",
                        source_url=None,
                    )
                ],
            )

    def test_version_1_jsonl_is_read_without_rewrite_then_saved_as_version_3(self) -> None:
        record = make_job().to_record()
        record["schema_version"] = 1
        record.pop("age_requirement")
        record.pop("success_fee_conditions")
        record.pop("referral_reward_your_company")
        record.pop("referral_reward_total")
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        self.work.mkdir(parents=True)
        current = self.work / "jobins.jsonl"
        current.write_text(payload, encoding="utf-8", newline="\n")
        original_bytes = current.read_bytes()

        jobs = self.repository.load_all("jobins")

        self.assertEqual(current.read_bytes(), original_bytes)
        self.assertEqual(jobs[0].schema_version, 3)
        self.assertEqual(jobs[0].age_requirement, "")
        self.assertEqual(jobs[0].success_fee_conditions, "")
        self.repository.upsert_many("jobins", jobs)
        rewritten = json.loads(current.read_text(encoding="utf-8"))
        self.assertEqual(rewritten["schema_version"], 3)
        self.assertIn("age_requirement", rewritten)
        self.assertIn("success_fee_conditions", rewritten)
        self.assertIn("referral_reward_your_company", rewritten)
        self.assertIn("referral_reward_total", rewritten)

    def test_version_1_backup_restore_is_persisted_as_version_3(self) -> None:
        record = make_job().to_record()
        record["schema_version"] = 1
        record.pop("age_requirement")
        record.pop("success_fee_conditions")
        record.pop("referral_reward_your_company")
        record.pop("referral_reward_total")
        self.work.mkdir(parents=True)
        self.backup.mkdir(parents=True)
        (self.work / "jobins.jsonl").write_text(
            "not-json\n", encoding="utf-8", newline="\n"
        )
        backup = self.backup / "jobins.previous.jsonl"
        backup.write_text(
            json.dumps(record, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        restored = self.repository.restore_previous("jobins")

        self.assertEqual(restored[0].schema_version, 3)
        current_record = json.loads(
            (self.work / "jobins.jsonl").read_text(encoding="utf-8")
        )
        self.assertEqual(current_record["schema_version"], 3)

    def test_version_2_jsonl_is_read_without_rewrite_then_saved_as_version_3(self) -> None:
        record = make_job().to_record()
        record["schema_version"] = 2
        record.pop("referral_reward_your_company")
        record.pop("referral_reward_total")
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        self.work.mkdir(parents=True)
        current = self.work / "jobins.jsonl"
        current.write_text(payload, encoding="utf-8", newline="\n")
        original_bytes = current.read_bytes()

        jobs = self.repository.load_all("jobins")

        self.assertEqual(current.read_bytes(), original_bytes)
        self.assertEqual(jobs[0].schema_version, 3)
        self.assertEqual(jobs[0].referral_reward_your_company, "")
        self.assertEqual(jobs[0].referral_reward_total, "")
        self.repository.upsert_many("jobins", jobs)
        rewritten = json.loads(current.read_text(encoding="utf-8"))
        self.assertEqual(rewritten["schema_version"], 3)
        self.assertEqual(rewritten["referral_reward_your_company"], "")
        self.assertEqual(rewritten["referral_reward_total"], "")


if __name__ == "__main__":
    unittest.main()

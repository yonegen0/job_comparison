from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from recruit_job_tool.csv_exporter import CSV_COLUMNS, CsvExporter
from recruit_job_tool.errors import CsvExportError
from recruit_job_tool.models import JobSource

from tests.recruit_feature.support import make_job


class CsvExporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name)
        self.exporter = CsvExporter(self.output)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_export_has_fixed_header_bom_and_all_text_cells_prefixed(self) -> None:
        job = replace(
            make_job(
                title="=HYPERLINK(\"x\")",
                source_url=None,
                fields={
                    "company_name": "+1",
                    "location": "-1",
                    "age_requirement": "=AGE()",
                    "job_description": "@先頭\r\n-次行,\"引用\"",
                    "salary_text": "応相談",
                    "benefits": "＝＋－＠",
                    "success_fee_conditions": "=FEE()\r\n+next",
                    "referral_reward_your_company": "一律 85.5万円",
                    "referral_reward_total": "理論年収の 40%",
                },
            ),
            remarks="\t=CMD",
        )
        path = self.exporter.export("jobins", [job])
        self.assertEqual(path.name, "jobins_jobs.csv")
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(tuple(reader.fieldnames or ()), CSV_COLUMNS)
            row = next(reader)
            self.assertEqual(row["schema_version"], "3")
            self.assertEqual(row["salary_min"], "")
            self.assertEqual(row["source_url"], "'")
            self.assertEqual(row["job_title"], "'=HYPERLINK(\"x\")")
            self.assertEqual(row["company_name"], "'+1")
            self.assertEqual(row["location"], "'-1")
            self.assertEqual(row["age_requirement"], "'=AGE()")
            self.assertEqual(
                row["success_fee_conditions"],
                "'=FEE()\n+next",
            )
            self.assertEqual(row["remarks"], "'\t=CMD")
            self.assertEqual(row["job_description"], "'@先頭\n-次行,\"引用\"")
            self.assertEqual(row["benefits"], "'＝＋－＠")
            self.assertEqual(row["referral_reward_your_company"], "'一律 85.5万円")
            self.assertEqual(row["referral_reward_total"], "'理論年収の 40%")
            with self.assertRaises(StopIteration):
                next(reader)

        self.assertEqual(
            CSV_COLUMNS[CSV_COLUMNS.index("education") + 1],
            "age_requirement",
        )
        self.assertEqual(
            CSV_COLUMNS[CSV_COLUMNS.index("commission") + 1],
            "success_fee_conditions",
        )
        self.assertEqual(
            CSV_COLUMNS[CSV_COLUMNS.index("success_fee_conditions") + 1 : CSV_COLUMNS.index("refund_policy")],
            ("referral_reward_your_company", "referral_reward_total"),
        )
        self.assertEqual(len(CSV_COLUMNS), 43)

    def test_jobins_new_careerbank_columns_are_empty_text_cells(self) -> None:
        path = self.exporter.export("jobins", [make_job()])
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["age_requirement"], "'")
        self.assertEqual(row["success_fee_conditions"], "'")
        self.assertEqual(row["referral_reward_your_company"], "'")
        self.assertEqual(row["referral_reward_total"], "'")

    def test_provider_outputs_are_separate(self) -> None:
        jobins_path = self.exporter.export("JOBINS", [make_job()])
        careerbank_path = self.exporter.export(
            "careerbank",
            [
                make_job(
                    source=JobSource.CAREERBANK,
                    source_job_id="456",
                    source_url="https://example.invalid/careerbank/456",
                )
            ],
        )
        self.assertNotEqual(jobins_path, careerbank_path)
        self.assertTrue(jobins_path.exists())
        self.assertTrue(careerbank_path.exists())

    def test_empty_duplicate_and_cross_provider_exports_are_rejected(self) -> None:
        with self.assertRaises(CsvExportError):
            self.exporter.export("jobins", [])
        job = make_job()
        with self.assertRaises(CsvExportError):
            self.exporter.export("jobins", [job, job])
        careerbank = make_job(
            source=JobSource.CAREERBANK,
            source_job_id="456",
            source_url=None,
        )
        with self.assertRaises(CsvExportError):
            self.exporter.export("jobins", [careerbank])

    def test_fsync_or_replace_failure_does_not_publish_partial_csv(self) -> None:
        destination = self.exporter.export("jobins", [make_job(title="旧")])
        old_bytes = destination.read_bytes()
        with patch("recruit_job_tool.csv_exporter.os.fsync", side_effect=OSError("disk")):
            with self.assertRaises(CsvExportError):
                self.exporter.export("jobins", [make_job(title="新")])
        self.assertEqual(destination.read_bytes(), old_bytes)

        with patch("recruit_job_tool.csv_exporter.os.replace", side_effect=OSError("busy")):
            with self.assertRaises(CsvExportError):
                self.exporter.export("jobins", [make_job(title="新")])
        self.assertEqual(destination.read_bytes(), old_bytes)
        self.assertEqual(list(self.output.glob("*.tmp")), [])

    def test_directory_creation_failure_is_a_csv_error(self) -> None:
        with patch(
            "recruit_job_tool.csv_exporter.Path.mkdir",
            side_effect=OSError("denied"),
        ):
            with self.assertRaises(CsvExportError):
                self.exporter.export("jobins", [make_job()])

    def test_verification_failure_keeps_the_previous_csv(self) -> None:
        destination = self.exporter.export("jobins", [make_job(title="旧")])
        old_bytes = destination.read_bytes()
        with patch(
            "recruit_job_tool.csv_exporter._verify_csv",
            side_effect=CsvExportError("verify"),
        ):
            with self.assertRaises(CsvExportError):
                self.exporter.export("jobins", [make_job(title="新")])
        self.assertEqual(destination.read_bytes(), old_bytes)


if __name__ == "__main__":
    unittest.main()

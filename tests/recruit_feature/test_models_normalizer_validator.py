from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import unittest

from recruit_job_tool.errors import NormalizationError
from recruit_job_tool.models import (
    Job,
    JobSource,
    JobStatus,
    RawJob,
    SalaryCurrency,
    SalaryParseStatus,
    SalaryPeriod,
)
from recruit_job_tool.normalizer import Normalizer
from recruit_job_tool.validator import Validator

from tests.recruit_feature.support import BASE_TIME, make_job, make_raw_job


class ModelNormalizerValidatorTests(unittest.TestCase):
    def test_normalize_preserves_free_text_and_normalizes_single_line(self) -> None:
        raw = make_raw_job(
            title="  ソフトウェア   エンジニア\r\n ",
            fields={
                "job_description": "  1行目  内部空白\r\n    字下げ\r\n\r\n 最終行  ",
                "独自項目": "  A  B\r\n C  ",
            },
        )
        job = Normalizer().normalize(raw)
        self.assertEqual(job.job_title, "ソフトウェア エンジニア")
        self.assertEqual(
            job.job_description,
            "1行目  内部空白\n    字下げ\n\n 最終行",
        )
        self.assertEqual(job.provider_specific["独自項目"], "A  B\n C")

    def test_salary_and_typed_metadata_are_derived_conservatively(self) -> None:
        job = make_job()
        self.assertEqual(job.salary_min, 5_000_000)
        self.assertEqual(job.salary_max, 7_000_000)
        self.assertIs(job.salary_currency, SalaryCurrency.JPY)
        self.assertIs(job.salary_period, SalaryPeriod.ANNUAL)
        self.assertIs(job.salary_parse_status, SalaryParseStatus.PARSED)
        self.assertIs(job.job_status, JobStatus.ACTIVE)
        self.assertEqual(job.internal_job_id, "JOBINS:123")

    def test_compound_and_standard_salary_amounts_are_parsed(self) -> None:
        cases = (
            (
                "月給22万2387円～26万9205円",
                222_387,
                269_205,
                SalaryPeriod.MONTHLY,
                SalaryParseStatus.PARSED,
            ),
            (
                "月給26万3000円以上",
                263_000,
                None,
                SalaryPeriod.MONTHLY,
                SalaryParseStatus.PARTIAL,
            ),
            (
                "月給10万2千円",
                102_000,
                102_000,
                SalaryPeriod.MONTHLY,
                SalaryParseStatus.PARSED,
            ),
            (
                "月給39.4万円",
                394_000,
                394_000,
                SalaryPeriod.MONTHLY,
                SalaryParseStatus.PARSED,
            ),
            (
                "時給1,500円",
                1_500,
                1_500,
                SalaryPeriod.HOURLY,
                SalaryParseStatus.PARSED,
            ),
            (
                "年収500万円～700万円",
                5_000_000,
                7_000_000,
                SalaryPeriod.ANNUAL,
                SalaryParseStatus.PARSED,
            ),
            (
                "月給278,000円 ～ 302,000円",
                278_000,
                302_000,
                SalaryPeriod.MONTHLY,
                SalaryParseStatus.PARSED,
            ),
        )

        for salary_text, minimum, maximum, period, status in cases:
            with self.subTest(salary_text=salary_text):
                job = make_job(fields={"salary_text": salary_text})
                self.assertEqual(job.salary_text, salary_text)
                self.assertEqual(job.salary_min, minimum)
                self.assertEqual(job.salary_max, maximum)
                self.assertIs(job.salary_currency, SalaryCurrency.JPY)
                self.assertIs(job.salary_period, period)
                self.assertIs(job.salary_parse_status, status)
                self.assertTrue(Validator().validate(job).is_valid)

    def test_partial_and_unparsed_salary_do_not_create_guessed_values(self) -> None:
        minimum = make_job(fields={"salary_text": "月給30万円以上"})
        self.assertEqual(minimum.salary_min, 300_000)
        self.assertIsNone(minimum.salary_max)
        self.assertIs(minimum.salary_parse_status, SalaryParseStatus.PARTIAL)

        ambiguous = make_job(fields={"salary_text": "基本給20万円、手当3万円"})
        self.assertIsNone(ambiguous.salary_min)
        self.assertIsNone(ambiguous.salary_max)
        self.assertIs(ambiguous.salary_parse_status, SalaryParseStatus.UNPARSED)

        missing_period = make_job(fields={"salary_text": "500万円"})
        self.assertIsNone(missing_period.salary_min)
        self.assertIsNone(missing_period.salary_max)
        self.assertIs(missing_period.salary_period, SalaryPeriod.UNKNOWN)
        self.assertIs(
            missing_period.salary_parse_status,
            SalaryParseStatus.UNPARSED,
        )

    def test_conditional_itemized_and_reversed_salary_stays_unparsed(self) -> None:
        cases = (
            "月給30万円～（宅建有）※月給25万円～（宅建無）",
            "月給30万2000円スタート（一律支給の手当10万2千円を含む）",
            "基本給20万円、手当3万円",
            "月給30万円～25万円",
        )

        for salary_text in cases:
            with self.subTest(salary_text=salary_text):
                job = make_job(fields={"salary_text": salary_text})
                self.assertEqual(job.salary_text, salary_text)
                self.assertIsNone(job.salary_min)
                self.assertIsNone(job.salary_max)
                self.assertIs(job.salary_parse_status, SalaryParseStatus.UNPARSED)
                self.assertTrue(Validator().validate(job).is_valid)

    def test_stable_source_id_and_aware_time_are_required(self) -> None:
        with self.assertRaises(NormalizationError):
            Normalizer().normalize(make_raw_job(source_job_id="  "))
        naive = datetime(2026, 8, 8, 12, 0, 0)
        with self.assertRaises(NormalizationError):
            Normalizer().normalize(make_raw_job(acquired_at=naive))

    def test_record_round_trip_is_strict_and_preserves_microseconds(self) -> None:
        job = make_job()
        restored = Job.from_record(job.to_record())
        self.assertEqual(restored.to_record(), job.to_record())
        self.assertEqual(len(job.to_record()), 44)
        self.assertEqual(len(Job.SERIALIZED_FIELDS_V1), 40)
        self.assertEqual(len(Job.SERIALIZED_FIELDS_V2), 42)
        self.assertEqual(len(Job.SERIALIZED_FIELDS), 44)
        record = job.to_record()
        record["unknown"] = "value"
        with self.assertRaises(ValueError):
            Job.from_record(record)

    def test_validator_reports_invalid_url_without_raising(self) -> None:
        job = make_job()
        malformed = replace(job, source_url="https://[broken")
        result = Validator().validate(malformed)
        self.assertFalse(result.is_valid)
        self.assertIn("source_url_invalid", result.errors)

    def test_schema_version_1_record_is_strictly_migrated_to_version_3(self) -> None:
        old_record = make_job().to_record()
        old_record["schema_version"] = 1
        old_record.pop("age_requirement")
        old_record.pop("success_fee_conditions")
        old_record.pop("referral_reward_your_company")
        old_record.pop("referral_reward_total")
        old_record["provider_specific"] = {
            "age_requirement": "do-not-infer",
            "success_fee_conditions": "do-not-infer",
            "referral_reward_your_company": "do-not-infer",
            "referral_reward_total": "do-not-infer",
        }

        restored = Job.from_record(old_record)

        self.assertEqual(restored.schema_version, 3)
        self.assertEqual(restored.age_requirement, "")
        self.assertEqual(restored.success_fee_conditions, "")
        self.assertEqual(restored.referral_reward_your_company, "")
        self.assertEqual(restored.referral_reward_total, "")
        self.assertEqual(restored.to_record()["schema_version"], 3)

    def test_schema_version_2_record_is_strictly_migrated_to_version_3(self) -> None:
        old_record = make_job().to_record()
        old_record["schema_version"] = 2
        old_record.pop("referral_reward_your_company")
        old_record.pop("referral_reward_total")
        old_record["provider_specific"] = {
            "referral_reward_your_company": "do-not-infer",
            "referral_reward_total": "do-not-infer",
        }

        restored = Job.from_record(old_record)

        self.assertEqual(restored.schema_version, 3)
        self.assertEqual(restored.referral_reward_your_company, "")
        self.assertEqual(restored.referral_reward_total, "")

    def test_each_supported_schema_rejects_missing_and_unknown_fields(self) -> None:
        version_3 = make_job().to_record()
        version_2 = dict(version_3)
        version_2["schema_version"] = 2
        version_2.pop("referral_reward_your_company")
        version_2.pop("referral_reward_total")
        version_1 = dict(version_2)
        version_1["schema_version"] = 1
        version_1.pop("age_requirement")
        version_1.pop("success_fee_conditions")
        for name, record in (("v1", version_1), ("v2", version_2), ("v3", version_3)):
            missing = dict(record)
            missing.pop("job_title")
            unknown = dict(record)
            unknown["unknown"] = "value"
            with self.subTest(name=name, kind="missing"), self.assertRaises(ValueError):
                Job.from_record(missing)
            with self.subTest(name=name, kind="unknown"), self.assertRaises(ValueError):
                Job.from_record(unknown)

        partial_v2 = dict(version_2)
        partial_v2["referral_reward_your_company"] = "一律 10万円"
        with self.assertRaises(ValueError):
            Job.from_record(partial_v2)
        partial_v1 = dict(version_1)
        partial_v1["referral_reward_total"] = "一律 20万円"
        with self.assertRaises(ValueError):
            Job.from_record(partial_v1)

    def test_unknown_and_boolean_schema_versions_are_rejected(self) -> None:
        for version in (0, 4, 999, True, False):
            record = make_job().to_record()
            record["schema_version"] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                Job.from_record(record)

    def test_direct_version_1_job_is_invalid_but_serializes_as_version_3(self) -> None:
        legacy_job = replace(make_job(), schema_version=1)

        result = Validator().validate(legacy_job)

        self.assertFalse(result.is_valid)
        self.assertIn("schema_version_unsupported", result.errors)
        self.assertEqual(legacy_job.to_record()["schema_version"], 3)

    def test_new_fields_have_distinct_normalization_rules(self) -> None:
        job = Normalizer().normalize(
            make_raw_job(
                fields={
                    "age_requirement": "  20-plus\r\n  60-minus  ",
                    "success_fee_conditions": " condition-1\r\n  condition-2\rcondition-3 ",
                    "referral_reward_your_company": " 理論年収の\r\n 28.5%\t ",
                    "referral_reward_total": " 一律\r 85.5万円 ",
                }
            )
        )

        self.assertEqual(job.schema_version, 3)
        self.assertEqual(job.age_requirement, "20-plus 60-minus")
        self.assertEqual(
            job.success_fee_conditions,
            "condition-1\n  condition-2\ncondition-3",
        )
        self.assertEqual(job.referral_reward_your_company, "理論年収の 28.5%")
        self.assertEqual(job.referral_reward_total, "一律 85.5万円")
        self.assertNotIn("referral_reward_your_company", job.provider_specific)
        self.assertNotIn("referral_reward_total", job.provider_specific)

    def test_validator_reports_warnings_and_salary_inconsistency(self) -> None:
        job = make_job(fields={"job_status": "", "salary_text": "応相談"})
        result = Validator().validate(job)
        self.assertTrue(result.is_valid)
        self.assertIn("job_status_unknown", result.warnings)

        inconsistent = replace(
            job,
            salary_parse_status=SalaryParseStatus.PARSED,
            salary_min=None,
            salary_max=None,
        )
        invalid = Validator().validate(inconsistent)
        self.assertFalse(invalid.is_valid)
        self.assertIn("salary_parsed_state_inconsistent", invalid.errors)

        reversed_range = replace(make_job(), salary_min=300_000, salary_max=250_000)
        invalid_range = Validator().validate(reversed_range)
        self.assertFalse(invalid_range.is_valid)
        self.assertIn("salary_range_invalid", invalid_range.errors)

    def test_validator_rejects_non_string_referral_reward_fields(self) -> None:
        for field_name in (
            "referral_reward_your_company",
            "referral_reward_total",
        ):
            with self.subTest(field_name=field_name):
                invalid = replace(make_job(), **{field_name: 123})
                result = Validator().validate(invalid)
                self.assertFalse(result.is_valid)
                self.assertIn(f"{field_name}_type_invalid", result.errors)

    def test_raw_fields_reject_non_string_and_null_characters(self) -> None:
        raw = RawJob(
            source=JobSource.JOBINS,
            source_job_id="123",
            source_url=None,
            fields={"job_title": "valid", "bad": "a\x00b"},
            acquired_at=BASE_TIME,
            batch_id="batch",
        )
        with self.assertRaises(NormalizationError):
            Normalizer().normalize(raw)


if __name__ == "__main__":
    unittest.main()

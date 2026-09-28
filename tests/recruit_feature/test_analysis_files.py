from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from recruit_job_tool.analysis_prompt.models import (
    PromptJob,
    PromptReferralReward,
    PromptRequirements,
    PromptSalary,
)
from recruit_job_tool.analysis_prompt.renderer import (
    render_analysis_guide,
    render_candidate_analysis_guide,
    render_job_data_json_v4,
)
from recruit_job_tool.candidate_profile import CandidateProfile
from recruit_job_tool.analysis_prompt.service import generate_analysis_files
from recruit_job_tool.csv_exporter import CsvExporter
from recruit_job_tool.errors import PromptInputError
from recruit_job_tool.models import JobSource
from tests.recruit_feature.support import make_job


def prompt_job(index: int = 1) -> PromptJob:
    return PromptJob(
        job_id=f"JOBINS:{index}",
        source_url=f"https://jobins.jp/agent/jobs/{index}",
        company="会社",
        job_title=f"求人{index}",
        industry="IT",
        location="東京",
        salary=PromptSalary("400万円", 4_000_000, 4_000_000, "JPY", "annual", "parsed"),
        application_requirements=PromptRequirements("必須", None, None, None),
        referral_reward=PromptReferralReward("10万円", "20万円"),
    )


class AnalysisV4RendererTests(unittest.TestCase):
    def test_v4_json_has_fixed_wrapper_and_existing_nine_job_keys(self) -> None:
        generated = datetime(
            2026, 9, 1, 23, 45, 12, 999999,
            tzinfo=timezone(timedelta(hours=9)),
        )
        content = render_job_data_json_v4(
            provider=JobSource.JOBINS,
            generated_at=generated,
            jobs=[prompt_job()],
        )
        payload = json.loads(content)
        self.assertEqual(
            list(payload), ["format", "provider", "generated_at", "jobs"]
        )
        self.assertEqual(payload["format"], "job-comparison-jobs-v4")
        self.assertEqual(payload["provider"], "JOBINS")
        self.assertEqual(payload["generated_at"], "2026-09-01T14:45:12Z")
        self.assertEqual(
            list(payload["jobs"][0]),
            [
                "job_id", "source_url", "company", "job_title", "industry",
                "location", "salary", "application_requirements", "referral_reward",
            ],
        )
        self.assertTrue(content.endswith("\n"))
        self.assertFalse(content.startswith("\ufeff"))
        for forbidden in (
            "candidate_profile", "job_data_file", "job_data_sha256", "job_count"
        ):
            self.assertNotIn(forbidden, payload)

    def test_v4_accepts_100_and_rejects_101(self) -> None:
        instant = datetime.now(timezone.utc)
        render_job_data_json_v4(
            provider=JobSource.CAREERBANK,
            generated_at=instant,
            jobs=[prompt_job(index) for index in range(100)],
        )
        with self.assertRaises(PromptInputError):
            render_job_data_json_v4(
                provider=JobSource.CAREERBANK,
                generated_at=instant,
                jobs=[prompt_job(index) for index in range(101)],
            )

    def test_v8_guide_is_optional_general_comparison_contract(self) -> None:
        guide = render_analysis_guide()
        self.assertIn("prompt_template_version: job-comparison-v8", guide)
        self.assertIn("添付されていなくても", guide)
        self.assertIn("複数の求人JSON", guide)
        self.assertIn("provider + job_id", guide)
        self.assertIn("最大20件", guide)
        self.assertNotIn("candidate_profile_sha256", guide)
        self.assertNotIn("job_data_sha256", guide)
        self.assertNotIn("必須条件ゲート", guide)

    def test_v9_guide_renders_candidate_as_safe_markdown_once(self) -> None:
        guide = render_candidate_analysis_guide(
            candidate_profile=CandidateProfile(
                age="34歳",
                gender="回答しない",
                education="4年制大学卒業",
                work_history="法人営業\n\n# 見出し\n---\n```\n<!-- comment -->",
                qualifications="",
            )
        )
        self.assertTrue(guide.startswith(
            "---\nprompt_template_version: job-comparison-v9\n---\n\n"
            "# 求人JSON 転職者条件付き比較ガイド\n"
        ))
        self.assertEqual(guide.splitlines().count("## 転職者条件"), 1)
        self.assertIn("- 年齢: 34歳\n- 性別: 回答しない\n- 学歴: 4年制大学卒業", guide)
        self.assertIn(
            "> 法人営業\n>\n> # 見出し\n> ---\n> ```\n> <!-- comment -->",
            guide,
        )
        self.assertIn("### 資格\n\n> 記載なし", guide)
        self.assertIn("採点対象", guide)
        self.assertIn("保留", guide)
        self.assertIn("除外", guide)
        self.assertNotIn("candidate_profile_format", guide)
        self.assertNotIn("candidate_profile_sha256", guide)
        self.assertTrue(guide.endswith("\n"))
        self.assertNotIn("\r", guide)


class AnalysisGenerationTests(unittest.TestCase):
    def test_candidate_mode_publishes_v4_json_and_v9_guide_together(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            csv_path = CsvExporter(directory).export(
                JobSource.JOBINS,
                [make_job(
                    source_job_id="one",
                    source_url="https://jobins.jp/agent/jobs/one",
                )],
            )
            profile = CandidateProfile(
                age="34歳",
                gender="回答しない",
                education="4年制大学卒業",
                qualifications="普通運転免許",
            )
            result = generate_analysis_files(
                input_path=csv_path,
                json_output_path=directory / "jobs.json",
                guide_output_path=directory / "guide.md",
                provider=JobSource.JOBINS,
                candidate_profile=profile,
            )
            self.assertEqual(json.loads(result.json_path.read_text(encoding="utf-8"))["format"], "job-comparison-jobs-v4")
            self.assertNotIn("34歳", result.json_path.read_text(encoding="utf-8"))
            self.assertIn("34歳", result.guide_path.read_text(encoding="utf-8"))
            self.assertEqual(result.warnings, ())

    def test_candidate_mode_rejects_missing_guide_before_json_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            csv_path = CsvExporter(directory).export(
                JobSource.JOBINS,
                [make_job(
                    source_job_id="one",
                    source_url="https://jobins.jp/agent/jobs/one",
                )],
            )
            with self.assertRaises(Exception):
                generate_analysis_files(
                    input_path=csv_path,
                    json_output_path=directory / "jobs.json",
                    provider=JobSource.JOBINS,
                    include_guide=False,
                    candidate_profile=CandidateProfile(
                        age="34歳",
                        gender="回答しない",
                        education="4年制大学卒業",
                    ),
                )
            self.assertFalse((directory / "jobs.json").exists())

    def test_json_succeeds_without_guide(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            csv_path = CsvExporter(directory).export(
                JobSource.JOBINS,
                [make_job(
                    source_job_id="one",
                    source_url="https://jobins.jp/agent/jobs/one",
                )],
            )
            result = generate_analysis_files(
                input_path=csv_path,
                json_output_path=directory / "jobs.json",
                provider=JobSource.JOBINS,
                include_guide=False,
            )
            self.assertTrue(result.json_path.is_file())
            self.assertIsNone(result.guide_path)
            self.assertEqual(result.warnings, ())

    def test_guide_failure_keeps_json_and_returns_success_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            csv_path = CsvExporter(directory).export(
                JobSource.JOBINS,
                [make_job(
                    source_job_id="one",
                    source_url="https://jobins.jp/agent/jobs/one",
                )],
            )
            with patch(
                "recruit_job_tool.analysis_prompt.service.render_analysis_guide",
                side_effect=PromptInputError("injected"),
            ):
                result = generate_analysis_files(
                    input_path=csv_path,
                    json_output_path=directory / "jobs.json",
                    guide_output_path=directory / "guide.md",
                    provider=JobSource.JOBINS,
                )
            self.assertTrue(result.json_path.is_file())
            self.assertIsNone(result.guide_path)
            self.assertTrue(result.warnings)
            self.assertFalse((directory / "guide.md").exists())


if __name__ == "__main__":
    unittest.main()

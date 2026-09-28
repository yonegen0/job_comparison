from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

from recruit_job_tool.analysis_prompt.__main__ import _output_paths, _parser, main
from recruit_job_tool.analysis_prompt.models import AnalysisGenerationResult
from recruit_job_tool.models import JobSource
from recruit_job_tool.single_instance import SingleInstanceLock


class AnalysisPromptCliTests(unittest.TestCase):
    def test_parser_keeps_explicit_internal_contract_without_config(self) -> None:
        args = _parser().parse_args(
            [
                "jobins",
                "--input",
                "jobs.csv",
                "--json-output",
                "jobs.json",
                "--no-guide",
            ]
        )
        self.assertEqual(args.provider, "jobins")
        self.assertEqual(args.input, Path("jobs.csv"))
        self.assertEqual(args.json_output, Path("jobs.json"))
        self.assertTrue(args.no_guide)
        with self.assertRaises(SystemExit) as caught:
            _parser().parse_args(["jobins", "--config", "x"])
        self.assertEqual(caught.exception.code, 2)

    def test_output_path_helper_numbers_json_and_guide_independently(self) -> None:
        generated = datetime(2026, 8, 25, 1, 2, 3, tzinfo=timezone.utc)
        with patch("recruit_job_tool.analysis_prompt.__main__._path_is_occupied", side_effect=[True, False, False, False]):
            markdown, json_path = _output_paths(
                provider_name="jobins",
                markdown_path=None,
                json_path=None,
                generated_at=generated,
            )
        self.assertIn("_analysis_guide_", markdown.stem)
        self.assertIn("_analysis_jobs_", json_path.stem)
        self.assertTrue(json_path.stem.endswith("_01"))
        self.assertFalse(markdown.stem.endswith("_01"))

    def test_main_delegates_to_facade_and_prints_only_basenames(self) -> None:
        result = AnalysisGenerationResult(
            JobSource.JOBINS,
            2,
            2,
            Path("C:/secret/jobs.json"),
            Path("C:/secret/guide.md"),
            10,
            10,
            (),
        )
        app = Mock()
        app.create_analysis_files.return_value = result
        paths = SimpleNamespace(initialize=Mock())
        stdout = io.StringIO()
        mutex_name = f"Local\\RecruitJobTool-AnalysisCliTest-{uuid.uuid4().hex}"
        with patch(
            "recruit_job_tool.analysis_prompt.__main__.AppPaths.from_local_app_data",
            return_value=paths,
        ), patch(
            "recruit_job_tool.analysis_prompt.__main__.RecruitJobApplication",
            return_value=app,
        ), patch(
            "recruit_job_tool.analysis_prompt.__main__.SingleInstanceLock",
            lambda: SingleInstanceLock(mutex_name),
        ), contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "jobins",
                    "--input",
                    "jobs.csv",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("guide.md", stdout.getvalue())
        self.assertIn("jobs.json", stdout.getvalue())
        self.assertNotIn("C:/secret", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

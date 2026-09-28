from __future__ import annotations

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from recruit_job_tool.app_paths import AppPaths
from recruit_job_tool.cli_adapter import parser_for, run_provider_cli
from recruit_job_tool.models import JobSource
from recruit_job_tool.single_instance import SingleInstanceLock


class CliAdapterTests(unittest.TestCase):
    def test_parser_has_no_config_and_requires_explicit_command(self) -> None:
        parser = parser_for(JobSource.JOBINS)
        with self.assertRaises(SystemExit) as missing:
            parser.parse_args([])
        self.assertEqual(missing.exception.code, 2)
        with self.assertRaises(SystemExit) as config:
            parser.parse_args(["--config", "x", "export"])
        self.assertEqual(config.exception.code, 2)
        args = parser.parse_args(["import", "input.html", "--mode", "merge"])
        self.assertEqual(args.command, "import")
        self.assertEqual(args.mode, "merge")

    def test_export_without_output_location_returns_out001(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = AppPaths.from_root(Path(temporary).resolve())
            stdout = io.StringIO()
            stderr = io.StringIO()
            mutex_name = f"Local\\RecruitJobTool-CliTest-{uuid.uuid4().hex}"
            with patch(
                "recruit_job_tool.cli_adapter.SingleInstanceLock",
                lambda: SingleInstanceLock(mutex_name),
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = run_provider_cli(
                    JobSource.CAREERBANK,
                    ["export"],
                    paths_factory=lambda: paths,
                )
            self.assertEqual(code, 1)
            self.assertIn("OUT001", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

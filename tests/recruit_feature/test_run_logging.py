from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from recruit_job_tool.errors import RunLogError
from recruit_job_tool.run_logging import RunLogger


class RunLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)
        self.logger = RunLogger(Path(self.temporary.name), clock=lambda: self.now)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_log_contains_only_five_expected_fields(self) -> None:
        self.assertEqual(self.logger.start("jobins", "run-1"), "run-1")
        self.logger.record(3, "NET429")
        line = self.logger.path.read_text(encoding="utf-8").splitlines()[0]
        record = json.loads(line)
        self.assertEqual(
            set(record),
            {"timestamp", "run_id", "provider", "count", "error_code"},
        )
        self.assertEqual(record["provider"], "JOBINS")
        self.assertEqual(record["count"], 3)
        self.assertEqual(record["error_code"], "NET429")

    def test_start_of_next_run_overwrites_previous_log(self) -> None:
        self.logger.start("jobins", "run-1")
        self.logger.record(1)
        self.logger.start("careerbank", "run-2")
        self.assertEqual(self.logger.path.read_bytes(), b"")
        self.logger.record(2)
        lines = self.logger.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["run_id"], "run-2")

    def test_invalid_count_code_or_run_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.logger.start("jobins", "secret value")
        self.logger.start("jobins", "run-1")
        with self.assertRaises(ValueError):
            self.logger.record(-1)
        with self.assertRaises(ValueError):
            self.logger.record(0, "contains spaces")

    def test_record_requires_started_run(self) -> None:
        with self.assertRaises(RuntimeError):
            self.logger.record(0)

    def test_filesystem_failures_have_a_stable_log_error(self) -> None:
        with patch(
            "recruit_job_tool.run_logging.Path.mkdir",
            side_effect=OSError("denied"),
        ):
            with self.assertRaises(RunLogError) as caught:
                self.logger.start("jobins", "run-1")
        self.assertEqual(caught.exception.error_code, "LOG001")


if __name__ == "__main__":
    unittest.main()

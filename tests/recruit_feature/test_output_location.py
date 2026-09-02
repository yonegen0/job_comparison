from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from recruit_job_tool.errors import OutputLocationError
from recruit_job_tool.models import JobSource
from recruit_job_tool.output_location import (
    OUTPUT_LOCATION_FORMAT,
    OutputLocationStore,
)


class OutputLocationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.store = OutputLocationStore(self.state)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_missing_state_is_unconfigured(self) -> None:
        self.assertIsNone(self.store.load(JobSource.JOBINS))

    def test_stage_commit_persists_only_provider_and_output_directory(self) -> None:
        directory = self.root / "saved" / "output"
        directory.parent.mkdir()
        prepared = self.store.prepare_directory(directory)
        staged = self.store.stage(JobSource.JOBINS, prepared)
        self.assertIsNone(self.store.load(JobSource.JOBINS))
        staged.commit()
        self.assertEqual(self.store.load(JobSource.JOBINS), prepared)
        state_path = self.state / "jobins-output-location-v1.json"
        raw = state_path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(raw.endswith(b"\n"))
        payload = json.loads(raw.decode("utf-8"))
        self.assertEqual(
            payload,
            {
                "format": OUTPUT_LOCATION_FORMAT,
                "provider": "JOBINS",
                "output_directory": str(prepared),
            },
        )

    def test_candidate_uses_resolved_html_parent(self) -> None:
        html = self.root / "saved" / "jobs.html"
        html.parent.mkdir()
        html.write_text("<html></html>", encoding="utf-8")
        self.assertEqual(
            self.store.candidate_from_html(html), html.parent / "output"
        )

    def test_output_name_collision_with_file_is_rejected(self) -> None:
        directory = self.root / "saved" / "output"
        directory.parent.mkdir()
        directory.write_text("occupied", encoding="utf-8")
        with self.assertRaises(OutputLocationError):
            self.store.prepare_directory(directory)

    def test_duplicate_keys_and_unknown_keys_are_rejected(self) -> None:
        state_path = self.state / "jobins-output-location-v1.json"
        state_path.write_text(
            '{"format":"output-location-v1","provider":"JOBINS",'
            '"provider":"JOBINS","output_directory":"C:/safe/output"}\n',
            encoding="utf-8",
            newline="\n",
        )
        with self.assertRaises(OutputLocationError):
            self.store.load(JobSource.JOBINS)
        state_path.write_text(
            '{"format":"output-location-v1","provider":"JOBINS",'
            '"output_directory":"C:/safe/output","extra":true}\n',
            encoding="utf-8",
            newline="\n",
        )
        with self.assertRaises(OutputLocationError):
            self.store.load(JobSource.JOBINS)

    def test_bom_crlf_relative_path_and_provider_mismatch_are_rejected(self) -> None:
        state_path = self.state / "jobins-output-location-v1.json"
        cases = (
            b'\xef\xbb\xbf{"format":"output-location-v1"}\n',
            b'{"format":"output-location-v1"}\r\n',
            (
                '{"format":"output-location-v1","provider":"JOBINS",'
                '"output_directory":"relative/output"}\n'
            ).encode("utf-8"),
            (
                '{"format":"output-location-v1","provider":"CAREERBANK",'
                f'"output_directory":{json.dumps(str(self.root / "output"))}}}\n'
            ).encode("utf-8"),
        )
        for raw in cases:
            with self.subTest(raw=raw):
                state_path.write_bytes(raw)
                with self.assertRaises(OutputLocationError):
                    self.store.load(JobSource.JOBINS)

    def test_corrupt_regular_state_can_be_repaired_by_atomic_commit(self) -> None:
        state_path = self.state / "jobins-output-location-v1.json"
        state_path.write_text("broken\n", encoding="utf-8", newline="\n")
        directory = self.root / "saved" / "output"
        self.store.stage(JobSource.JOBINS, directory).commit()
        self.assertEqual(self.store.load(JobSource.JOBINS), directory)

    def test_output_symlink_is_rejected_when_supported(self) -> None:
        target = self.root / "target" / "output"
        target.mkdir(parents=True)
        link = self.root / "saved" / "output"
        link.parent.mkdir()
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlinks are unavailable")
        with self.assertRaises(OutputLocationError):
            self.store.prepare_directory(link)


if __name__ == "__main__":
    unittest.main()

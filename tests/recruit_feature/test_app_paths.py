from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from recruit_job_tool.app_paths import AppPaths
from recruit_job_tool.errors import EnvironmentError


class AppPathsTests(unittest.TestCase):
    def test_from_root_builds_fixed_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "RecruitJobTool"
            paths = AppPaths.from_root(root)
            paths.initialize()
            self.assertEqual(paths.output_dir, root / "data" / "output")
            self.assertTrue(paths.work_dir.is_dir())
            self.assertTrue(paths.state_dir.is_dir())

    def test_local_app_data_is_required_and_absolute(self) -> None:
        for value in (None, "", "relative"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {} if value is None else {"LOCALAPPDATA": value},
                clear=True,
            ):
                with self.assertRaises(EnvironmentError) as caught:
                    AppPaths.from_local_app_data()
                self.assertEqual(caught.exception.error_code, "ENV001")


if __name__ == "__main__":
    unittest.main()

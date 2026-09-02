from __future__ import annotations

import os
import unittest
import uuid

from recruit_job_tool.errors import AlreadyRunningError
from recruit_job_tool.single_instance import SingleInstanceLock


@unittest.skipUnless(os.name == "nt", "Windows named mutex test")
class SingleInstanceTests(unittest.TestCase):
    def test_second_owner_is_rejected_and_release_allows_next(self) -> None:
        name = f"Local\\RecruitJobTool-Test-{uuid.uuid4().hex}"
        first = SingleInstanceLock(name).acquire()
        try:
            with self.assertRaises(AlreadyRunningError):
                SingleInstanceLock(name).acquire()
        finally:
            first.release()
        SingleInstanceLock(name).acquire().release()


if __name__ == "__main__":
    unittest.main()

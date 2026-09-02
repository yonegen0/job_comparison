from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from recruit_job_tool.constants import MAX_CURRENT_JOBS
from recruit_job_tool.providers.errors import ImportValidationError, UnsupportedFormatError
from recruit_job_tool.providers.jobins import JobinsProvider, JobinsSearchHtmlImporter


FIXTURE = Path(__file__).parent / "fixtures" / "jobins_search_results.html"


class JobinsProviderTests(unittest.TestCase):
    def _load_fixture_variant(self, conditions: str):
        original = "【必須要件】合成必須条件D<br>【歓迎要件】合成歓迎条件D"
        fixture_text = FIXTURE.read_text(encoding="utf-8")
        self.assertIn(original, fixture_text)
        variant = fixture_text.replace(original, conditions, 1)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "jobins.html"
            path.write_text(variant, encoding="utf-8", newline="\n")
            return JobinsProvider(importer=JobinsSearchHtmlImporter()).load(
                path, limit=MAX_CURRENT_JOBS
            )

    def test_fixture_maps_cards_and_rewards(self) -> None:
        jobs = JobinsProvider(importer=JobinsSearchHtmlImporter()).load(
            FIXTURE, limit=MAX_CURRENT_JOBS
        )
        self.assertEqual([job.source_job_id for job in jobs], ["JOB-001", "JOB-002", "JOB-003"])
        self.assertEqual(jobs[0].fields["referral_reward_your_company"], "一律 52.5万円")
        self.assertEqual(jobs[0].fields["required_conditions"], "合成必須条件A / 合成必須条件B")

    def test_limit_accepts_maximum_and_rejects_invalid_values(self) -> None:
        provider = JobinsProvider(importer=JobinsSearchHtmlImporter())
        self.assertEqual(len(provider.load(FIXTURE, limit=MAX_CURRENT_JOBS)), 3)
        for value in (True, 0, MAX_CURRENT_JOBS + 1):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                provider.load(FIXTURE, limit=value)

    def test_consecutive_empty_required_markers_are_collapsed(self) -> None:
        jobs = self._load_fixture_variant(
            "【必須要件】<br>■必須要件<br>合成必須条件D<br>"
            "【歓迎要件】合成歓迎条件D"
        )

        self.assertEqual(jobs[2].fields["required_conditions"], "合成必須条件D")
        self.assertEqual(jobs[2].fields["preferred_conditions"], "合成歓迎条件D")
        self.assertNotIn("必須要件", jobs[2].fields["required_conditions"])

    def test_consecutive_empty_preferred_markers_are_collapsed(self) -> None:
        jobs = self._load_fixture_variant(
            "【必須要件】合成必須条件D<br>【歓迎要件】<br>"
            "■歓迎要件<br>合成歓迎条件D"
        )

        self.assertEqual(jobs[2].fields["required_conditions"], "合成必須条件D")
        self.assertEqual(jobs[2].fields["preferred_conditions"], "合成歓迎条件D")
        self.assertNotIn("歓迎要件", jobs[2].fields["preferred_conditions"])

    def test_duplicate_marker_after_condition_text_is_rejected(self) -> None:
        with self.assertRaises(ImportValidationError) as caught:
            self._load_fixture_variant(
                "【必須要件】<br>合成必須条件D<br>■必須要件<br>"
                "合成必須条件E"
            )

        self.assertEqual(caught.exception.code, "JOBINS_CARD_INVALID")

    def test_duplicate_marker_with_inline_value_is_rejected(self) -> None:
        with self.assertRaises(ImportValidationError) as caught:
            self._load_fixture_variant(
                "【必須要件】<br>■必須要件：合成必須条件D"
            )

        self.assertEqual(caught.exception.code, "JOBINS_CARD_INVALID")

    def test_required_marker_after_preferred_is_rejected(self) -> None:
        with self.assertRaises(ImportValidationError) as caught:
            self._load_fixture_variant(
                "【歓迎要件】合成歓迎条件D<br>■必須要件<br>合成必須条件D"
            )

        self.assertEqual(caught.exception.code, "JOBINS_CARD_INVALID")

    def test_invalid_extension_and_page_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            text = root / "input.txt"
            text.write_text("x", encoding="utf-8")
            with self.assertRaises(UnsupportedFormatError):
                JobinsSearchHtmlImporter().load(text, limit=MAX_CURRENT_JOBS)
            html = root / "input.html"
            html.write_text("<html></html>", encoding="utf-8")
            with self.assertRaises(ImportValidationError):
                JobinsSearchHtmlImporter().load(html, limit=MAX_CURRENT_JOBS)


if __name__ == "__main__":
    unittest.main()

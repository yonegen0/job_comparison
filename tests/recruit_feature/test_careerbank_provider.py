from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from recruit_job_tool.constants import MAX_CURRENT_JOBS
from recruit_job_tool.providers.careerbank import CareerBankProvider, CareerBankSearchHtmlImporter
from recruit_job_tool.providers.errors import ImportValidationError, UnsupportedFormatError


FIXTURE = Path(__file__).parent / "fixtures" / "careerbank_search_results.html"


class CareerBankProviderTests(unittest.TestCase):
    def test_fixture_maps_all_cards_then_applies_limit(self) -> None:
        provider = CareerBankProvider(importer=CareerBankSearchHtmlImporter())
        jobs = provider.load(FIXTURE, limit=2)
        self.assertEqual([job.source_job_id for job in jobs], ["900001", "900002"])
        self.assertTrue(jobs[0].fields["required_conditions"])

    def test_limit_accepts_maximum_and_rejects_invalid_values(self) -> None:
        provider = CareerBankProvider(importer=CareerBankSearchHtmlImporter())
        self.assertEqual(len(provider.load(FIXTURE, limit=MAX_CURRENT_JOBS)), 3)
        for value in (False, 0, MAX_CURRENT_JOBS + 1):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                provider.load(FIXTURE, limit=value)

    def test_invalid_extension_and_page_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            text = root / "input.txt"
            text.write_text("x", encoding="utf-8")
            with self.assertRaises(UnsupportedFormatError):
                CareerBankSearchHtmlImporter().load(text)
            html = root / "input.html"
            html.write_text("<html></html>", encoding="utf-8")
            with self.assertRaises(ImportValidationError):
                CareerBankSearchHtmlImporter().load(html)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from recruit_job_tool import CandidateProfile
from recruit_job_tool.candidate_profile import (
    CANDIDATE_AGE_CHOICES,
    CANDIDATE_EDUCATION_CHOICES,
    CANDIDATE_GENDER_CHOICES,
    MAX_CANDIDATE_PROFILE_FILE_BYTES,
    _candidate_profile_json,
    _candidate_profile_sha256,
    _load_candidate_profile,
)
from recruit_job_tool.errors import PromptInputError


def profile(**overrides: str) -> CandidateProfile:
    values = {
        "age": "34歳",
        "gender": "回答しない",
        "education": "4年制大学卒業",
        "work_history": "法人営業3年",
        "qualifications": "普通運転免許",
    }
    values.update(overrides)
    return CandidateProfile(**values)


class CandidateProfileTests(unittest.TestCase):
    def test_choices_are_fixed_and_accepted(self) -> None:
        self.assertEqual(CANDIDATE_AGE_CHOICES[0], "18歳")
        self.assertEqual(CANDIDATE_AGE_CHOICES[-1], "80歳以上")
        for choice in CANDIDATE_AGE_CHOICES:
            self.assertEqual(profile(age=choice).age, choice)
        for choice in CANDIDATE_GENDER_CHOICES:
            self.assertEqual(profile(gender=choice).gender, choice)
        for choice in CANDIDATE_EDUCATION_CHOICES:
            self.assertEqual(profile(education=choice).education, choice)

    def test_invalid_required_choices_are_rejected_without_value_leak(self) -> None:
        for field in ("age", "gender", "education"):
            for value in ("", " 不正値 ", "不正値"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(PromptInputError) as caught:
                        profile(**{field: value})
                    self.assertEqual(caught.exception.error_code, "PRM001")
                    if value:
                        self.assertNotIn(value, str(caught.exception))

    def test_free_text_is_normalized_and_internal_text_is_preserved(self) -> None:
        value = profile(
            work_history="  1行目\r\n\r2行目  \n 3行目  ",
            qualifications=" \n ",
        )
        self.assertEqual(value.work_history, "1行目\n\n2行目  \n 3行目")
        self.assertEqual(value.qualifications, "")

    def test_free_text_boundaries_and_controls(self) -> None:
        self.assertEqual(len(profile(work_history="a" * 4000).work_history), 4000)
        for invalid in ("a" * 4001, "a\tb", "a\x00b", "a\x1fb", "a\x7fb"):
            with self.subTest(invalid_length=len(invalid)):
                with self.assertRaises(PromptInputError):
                    profile(work_history=invalid)

    def test_repr_is_redacted_and_canonical_json_is_stable(self) -> None:
        value = profile(work_history='秘密"\n```')
        self.assertNotIn("秘密", repr(value))
        content = _candidate_profile_json(value)
        self.assertFalse(content.endswith("\n"))
        self.assertEqual(
            list(json.loads(content)),
            [
                "profile_format",
                "age",
                "gender",
                "education",
                "work_history",
                "qualifications",
            ],
        )
        self.assertEqual(
            _candidate_profile_sha256(value),
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    def test_loader_accepts_pretty_json_without_changing_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profile.data"
            payload = json.loads(_candidate_profile_json(profile()))
            original = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            path.write_bytes(original)
            loaded = _load_candidate_profile(path)
            self.assertEqual(_candidate_profile_json(loaded), _candidate_profile_json(profile()))
            self.assertEqual(path.read_bytes(), original)

    def test_loader_rejects_invalid_files_and_json_shapes(self) -> None:
        valid = json.loads(_candidate_profile_json(profile()))
        cases = (
            b"",
            b"\xef\xbb\xbf{}",
            b"{\x00}",
            b'{"profile_format":NaN}',
            json.dumps(valid | {"unknown": "x"}, ensure_ascii=False).encode("utf-8"),
            json.dumps({key: value for key, value in valid.items() if key != "age"}, ensure_ascii=False).encode("utf-8"),
            _candidate_profile_json(profile()).replace(
                '"age":"34歳"', '"age":"34歳","age":"35歳"'
            ).encode("utf-8"),
        )
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for index, content in enumerate(cases):
                path = directory / f"invalid-{index}"
                path.write_bytes(content)
                with self.subTest(index=index), self.assertRaises(PromptInputError):
                    _load_candidate_profile(path)
            oversized = directory / "oversized"
            oversized.write_bytes(b" " * (MAX_CANDIDATE_PROFILE_FILE_BYTES + 1))
            with self.assertRaises(PromptInputError):
                _load_candidate_profile(oversized)
            with self.assertRaises(PromptInputError):
                _load_candidate_profile(directory)


if __name__ == "__main__":
    unittest.main()

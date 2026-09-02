"""転職者情報の検証とAI比較用の正規化表現。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat

from .errors import PromptInputError


CANDIDATE_PROFILE_FORMAT = "candidate-profile-v1"
CANDIDATE_AGE_CHOICES = tuple(f"{age}歳" for age in range(18, 80)) + (
    "80歳以上",
)
CANDIDATE_GENDER_CHOICES = ("男性", "女性", "その他", "回答しない")
CANDIDATE_EDUCATION_CHOICES = (
    "中学校卒業",
    "高等学校卒業",
    "専門学校卒業",
    "短期大学卒業",
    "高等専門学校卒業",
    "4年制大学卒業",
    "大学院修了",
    "その他",
)
MAX_CANDIDATE_FREE_TEXT_CHARACTERS = 4_000
MAX_CANDIDATE_PROFILE_FILE_BYTES = 64 * 1024

_PROFILE_KEYS = (
    "profile_format",
    "age",
    "gender",
    "education",
    "work_history",
    "qualifications",
)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class _CandidateProfileFieldError(PromptInputError):
    """GUIが値を漏らさず不正項目へfocusするための内部例外。"""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class CandidateProfile:
    """AI比較の必須条件ゲートにだけ使用する転職者情報。"""

    age: str
    gender: str
    education: str
    work_history: str = ""
    qualifications: str = ""

    def __post_init__(self) -> None:
        _validate_choice("age", self.age, CANDIDATE_AGE_CHOICES, "年齢")
        _validate_choice(
            "gender", self.gender, CANDIDATE_GENDER_CHOICES, "性別"
        )
        _validate_choice(
            "education",
            self.education,
            CANDIDATE_EDUCATION_CHOICES,
            "学歴",
        )
        object.__setattr__(
            self,
            "work_history",
            _normalize_free_text("work_history", self.work_history, "職歴"),
        )
        object.__setattr__(
            self,
            "qualifications",
            _normalize_free_text(
                "qualifications", self.qualifications, "資格"
            ),
        )

    @property
    def profile_format(self) -> str:
        return CANDIDATE_PROFILE_FORMAT


def _validate_choice(
    field: str,
    value: object,
    choices: tuple[str, ...],
    label: str,
) -> None:
    if value == "":
        raise _CandidateProfileFieldError(field, f"{label}を選択してください。")
    if not isinstance(value, str) or value not in choices:
        raise _CandidateProfileFieldError(
            field, f"{label}の選択内容が不正です。"
        )


def _normalize_free_text(field: str, value: object, label: str) -> str:
    if not isinstance(value, str):
        raise _CandidateProfileFieldError(field, f"{label}の入力が不正です。")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if any(
        (ord(character) < 0x20 and character != "\n")
        or ord(character) == 0x7F
        for character in normalized
    ):
        raise _CandidateProfileFieldError(
            field, f"{label}に使用できない文字が含まれています。"
        )
    if len(normalized) > MAX_CANDIDATE_FREE_TEXT_CHARACTERS:
        raise _CandidateProfileFieldError(
            field, f"{label}は4,000文字以内で入力してください。"
        )
    return normalized


def _candidate_profile_payload(profile: CandidateProfile) -> dict[str, str]:
    if not isinstance(profile, CandidateProfile):
        raise PromptInputError("転職者情報が不正です")
    return {
        "profile_format": profile.profile_format,
        "age": profile.age,
        "gender": profile.gender,
        "education": profile.education,
        "work_history": profile.work_history,
        "qualifications": profile.qualifications,
    }


def _candidate_profile_json(profile: CandidateProfile) -> str:
    try:
        return json.dumps(
            _candidate_profile_payload(profile),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise PromptInputError("転職者情報が不正です") from error


def _candidate_profile_sha256(profile: CandidateProfile) -> str:
    return hashlib.sha256(
        _candidate_profile_json(profile).encode("utf-8")
    ).hexdigest()


def _candidate_profile_from_json(
    content: str,
    *,
    require_canonical: bool = False,
) -> CandidateProfile:
    try:
        payload = json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(payload, dict) or set(payload) != set(_PROFILE_KEYS):
            raise ValueError("profile keys")
        if payload["profile_format"] != CANDIDATE_PROFILE_FORMAT:
            raise ValueError("profile format")
        profile = CandidateProfile(
            age=payload["age"],
            gender=payload["gender"],
            education=payload["education"],
            work_history=payload["work_history"],
            qualifications=payload["qualifications"],
        )
        if require_canonical and content != _candidate_profile_json(profile):
            raise ValueError("non-canonical profile")
        return profile
    except _CandidateProfileFieldError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PromptInputError("転職者情報が不正です") from error


def _load_candidate_profile(path: Path) -> CandidateProfile:
    try:
        candidate_path = Path(path).expanduser()
        metadata = candidate_path.lstat()
        attributes = getattr(metadata, "st_file_attributes", 0)
        if not stat.S_ISREG(metadata.st_mode) or attributes & _REPARSE_POINT:
            raise OSError("not a regular file")
        if (
            metadata.st_size < 1
            or metadata.st_size > MAX_CANDIDATE_PROFILE_FILE_BYTES
        ):
            raise ValueError("profile size")
        content = candidate_path.read_bytes()
        if (
            len(content) != metadata.st_size
            or content.startswith(b"\xef\xbb\xbf")
            or b"\x00" in content
        ):
            raise ValueError("profile bytes")
        return _candidate_profile_from_json(content.decode("utf-8"))
    except _CandidateProfileFieldError:
        raise
    except PromptInputError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        raise PromptInputError("転職者情報ファイルが不正です") from error


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate profile key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError("invalid profile constant")


__all__ = ["CandidateProfile"]

"""分析プロンプトへ渡す最小データモデル。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from recruit_job_tool.models import JobSource


@dataclass(frozen=True, slots=True)
class PromptSalary:
    text: str | None
    minimum: int | None
    maximum: int | None
    currency: str | None
    period: str | None
    parse_status: str


@dataclass(frozen=True, slots=True)
class PromptRequirements:
    required: str | None
    preferred: str | None
    education: str | None
    age: str | None


@dataclass(frozen=True, slots=True)
class PromptReferralReward:
    your_company: str | None
    total: str | None


@dataclass(frozen=True, slots=True)
class PromptJob:
    job_id: str
    source_url: str | None
    company: str | None
    job_title: str
    industry: str | None
    location: str | None
    salary: PromptSalary
    application_requirements: PromptRequirements
    referral_reward: PromptReferralReward


@dataclass(frozen=True, slots=True)
class PromptGenerationResult:
    markdown_path: Path
    json_path: Path
    provider: JobSource
    job_count: int
    company_count: int
    markdown_character_count: int
    json_character_count: int


@dataclass(frozen=True, slots=True)
class AnalysisGenerationResult:
    provider: JobSource
    job_count: int
    company_count: int
    json_path: Path
    guide_path: Path | None
    json_character_count: int
    guide_character_count: int | None
    warnings: tuple[str, ...]

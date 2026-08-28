"""完全ローカルで求人比較資料を検証・生成するパッケージ。"""

from .models import BuildResult, JobEntry, ReportDocument, VerificationFinding

__all__ = ["BuildResult", "JobEntry", "ReportDocument", "VerificationFinding"]

"""アプリケーション境界で利用する安定した例外型。"""


class RecruitJobToolError(Exception):
    """本ツールで処理可能なエラーの基底型。"""

    error_code = "APP001"


class EnvironmentError(RecruitJobToolError):
    """The per-user application data root is unavailable or unsafe."""

    error_code = "ENV001"


class AlreadyRunningError(RecruitJobToolError):
    error_code = "APP003"


class InstanceLockError(RecruitJobToolError):
    error_code = "APP004"


class MigrationError(RecruitJobToolError):
    def __init__(self, message: str, *, error_code: str = "MIG005") -> None:
        super().__init__(message)
        self.error_code = error_code


class NormalizationError(RecruitJobToolError):
    """RawJobを安全に共通モデルへ変換できない場合。"""

    error_code = "VAL001"


class JobValidationError(RecruitJobToolError):
    """永続化または出力対象のJobが不正な場合。"""

    error_code = "VAL001"


class RepositoryError(RecruitJobToolError):
    """JSONLの読書きに失敗した場合。"""

    error_code = "STR001"


class RepositoryCorruptionError(RepositoryError):
    """JSONLを現行ファイルまたは前世代から復旧できない場合。"""


class CsvExportError(RecruitJobToolError):
    """CSVを完全な状態で公開できない場合。"""

    error_code = "CSV001"


class OutputLocationError(RecruitJobToolError):
    """The per-provider user-facing output location is missing or unsafe."""

    error_code = "OUT001"


class RunLogError(RecruitJobToolError):
    """最小実行ログを初期化または追記できない場合。"""

    error_code = "LOG001"


class PromptInputError(RecruitJobToolError):
    """分析プロンプトの入力または生成内容が不正な場合。"""

    error_code = "PRM001"


class PromptOutputError(RecruitJobToolError):
    """分析プロンプトを安全に出力できない場合。"""

    error_code = "PRM002"


class HtmlBatchInputError(RecruitJobToolError):
    """A specific HTML failed before the batch was saved."""

    def __init__(self, filename: str, error_code: str) -> None:
        super().__init__("HTMLを取り込めませんでした")
        self.filename = filename
        self.error_code = error_code


class JobLimitError(JobValidationError):
    def __init__(self, projected_count: int, limit: int) -> None:
        super().__init__(f"取り込み後の求人が{projected_count}件となり、上限{limit}件を超えます")
        self.projected_count = projected_count
        self.limit = limit

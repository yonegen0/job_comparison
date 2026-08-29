from __future__ import annotations


class JobComparisonAppError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.user_message = message
        super().__init__(f"{message}（エラーコード: {code}）")


class InputChangedError(JobComparisonAppError):
    def __init__(self, message: str = "確認後に入力または出力が変更されました。もう一度事前確認してください。"):
        super().__init__("CHANGED001", message)


class InputValidationError(JobComparisonAppError):
    def __init__(self, message: str):
        super().__init__("INPUT001", message)


class BrowserUnavailableError(JobComparisonAppError):
    def __init__(self, message: str):
        super().__init__("BROWSER001", message)


class OutputOperationError(JobComparisonAppError):
    def __init__(self, message: str):
        super().__init__("OUTPUT001", message)


class ResourceLockError(JobComparisonAppError):
    def __init__(self, message: str):
        super().__init__("LOCK001", message)


class AlreadyRunningError(ResourceLockError):
    pass


class InstanceLockError(ResourceLockError):
    pass

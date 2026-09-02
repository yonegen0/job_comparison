"""Stable, non-sensitive errors exposed by provider boundaries."""

from __future__ import annotations


class ProviderError(RuntimeError):
    """Base error with a stable code suitable for local operational logs."""

    default_code = "PROVIDER_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code or self.default_code


class UnsupportedFormatError(ProviderError):
    default_code = "IMPORT_FORMAT_UNSUPPORTED"


class ImportValidationError(ProviderError):
    default_code = "IMPORT_VALIDATION_FAILED"

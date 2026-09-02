"""Provider boundaries for configured, read-only job acquisition."""

from .errors import (
    ImportValidationError,
    ProviderError,
    UnsupportedFormatError,
)
from .careerbank import (
    CareerBankProvider,
    CareerBankSearchHtmlImporter,
)
from .jobins import JobinsProvider, JobinsSearchHtmlImporter

__all__ = [
    "CareerBankProvider",
    "CareerBankSearchHtmlImporter",
    "ImportValidationError",
    "JobinsProvider",
    "JobinsSearchHtmlImporter",
    "ProviderError",
    "UnsupportedFormatError",
]

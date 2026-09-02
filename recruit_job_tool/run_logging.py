"""本文を受け取らない最小限の構造化実行ログ。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid

from .errors import RunLogError
from .models import JobSource, format_rfc3339
from .repository import _coerce_source


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


class RunLogger:
    """時刻、run ID、媒体、件数、エラーコード以外を記録しない。"""

    def __init__(
        self,
        log_dir: Path | str = Path("logs"),
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = Path(log_dir) / "run.jsonl"
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._source: JobSource | None = None
        self._run_id: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def run_id(self) -> str | None:
        return self._run_id

    def start(self, provider: JobSource | str, run_id: str | None = None) -> str:
        source = _coerce_source(provider)
        selected_run_id = run_id or uuid.uuid4().hex
        if not isinstance(selected_run_id, str) or not _SAFE_ID.fullmatch(
            selected_run_id
        ):
            raise ValueError("run_id contains unsupported characters")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("w", encoding="utf-8", newline="\n") as stream:
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            raise RunLogError("実行ログを初期化できませんでした") from error
        self._source = source
        self._run_id = selected_run_id
        return selected_run_id

    def record(self, count: int, error_code: str | None = None) -> None:
        if self._source is None or self._run_id is None:
            raise RuntimeError("start() must be called before record()")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("count must be a non-negative integer")
        normalized_error = error_code or ""
        if normalized_error and not _ERROR_CODE.fullmatch(normalized_error):
            raise ValueError("error_code must be a stable uppercase identifier")
        timestamp = self._clock()
        record = {
            "timestamp": format_rfc3339(timestamp),
            "run_id": self._run_id,
            "provider": self._source.value,
            "count": count,
            "error_code": normalized_error,
        }
        try:
            with self._path.open("a", encoding="utf-8", newline="\n") as stream:
                json.dump(
                    record,
                    stream,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            raise RunLogError("実行ログを追記できませんでした") from error

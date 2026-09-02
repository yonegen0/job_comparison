"""Absolute per-user application paths."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .errors import EnvironmentError


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path
    data_dir: Path
    work_dir: Path
    backup_dir: Path
    output_dir: Path
    log_dir: Path
    state_dir: Path

    @classmethod
    def from_local_app_data(cls) -> "AppPaths":
        raw = os.environ.get("LOCALAPPDATA", "")
        if not raw.strip():
            raise EnvironmentError("保存先を取得できませんでした")
        base = Path(raw)
        if not base.is_absolute():
            raise EnvironmentError("保存先が絶対パスではありません")
        try:
            resolved = base.resolve(strict=True)
        except OSError as error:
            raise EnvironmentError("保存先を参照できませんでした") from error
        if not resolved.is_dir():
            raise EnvironmentError("保存先がフォルダーではありません")
        return cls.from_root(resolved / "RecruitJobTool")

    @classmethod
    def from_root(cls, root: Path | str) -> "AppPaths":
        selected = Path(root)
        if not selected.is_absolute():
            raise EnvironmentError("保存先が絶対パスではありません")
        try:
            selected = selected.resolve(strict=False)
        except OSError as error:
            raise EnvironmentError("保存先を解決できませんでした") from error
        data = selected / "data"
        return cls(
            root=selected,
            data_dir=data,
            work_dir=data / "work",
            backup_dir=data / "backup",
            output_dir=data / "output",
            log_dir=selected / "logs",
            state_dir=selected / "state",
        )

    def initialize(self) -> None:
        try:
            for directory in (
                self.work_dir,
                self.backup_dir,
                self.output_dir,
                self.log_dir,
                self.state_dir,
            ):
                directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise EnvironmentError("保存先を初期化できませんでした") from error


__all__ = ["AppPaths"]

"""Persistent, per-provider locations for user-facing output files."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import uuid

from .errors import OutputLocationError
from .models import JobSource


OUTPUT_LOCATION_FORMAT = "output-location-v1"
_MAX_STATE_BYTES = 8 * 1024
_STATE_KEYS = {"format", "provider", "output_directory"}


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _is_reparse_point(path: Path, *, follow_symlinks: bool = False) -> bool:
    try:
        info = path.stat() if follow_symlinks else path.lstat()
    except OSError as error:
        raise OutputLocationError("出力先を検査できません") from error
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise OutputLocationError("出力先設定に重複項目があります")
        result[key] = value
    return result


@dataclass(slots=True)
class StagedOutputLocation:
    """A verified state update that is not visible until commit()."""

    temporary_path: Path
    destination_path: Path
    directory: Path
    _finished: bool = False

    def commit(self) -> None:
        if self._finished:
            raise OutputLocationError("出力先設定を確定できません")
        try:
            os.replace(self.temporary_path, self.destination_path)
        except OSError as error:
            raise OutputLocationError("出力先設定を保存できません") from error
        self._finished = True

    def discard(self) -> None:
        if self._finished:
            return
        _safe_unlink(self.temporary_path)
        self._finished = True


class OutputLocationStore:
    """Loads and atomically publishes one output directory per provider."""

    def __init__(self, state_dir: Path | str) -> None:
        self._state_dir = Path(state_dir)

    def load(self, provider: JobSource) -> Path | None:
        destination = self._state_path(provider)
        try:
            if not destination.exists() and not destination.is_symlink():
                return None
            if _is_reparse_point(destination):
                raise OutputLocationError("出力先設定にリンクは使用できません")
            if not destination.is_file():
                raise OutputLocationError("出力先設定が通常ファイルではありません")
            raw = destination.read_bytes()
        except OutputLocationError:
            raise
        except OSError as error:
            raise OutputLocationError("出力先設定を読み込めません") from error
        return self._decode(provider, raw)

    def candidate_from_html(self, input_path: Path | str) -> Path:
        selected = Path(input_path)
        try:
            resolved = selected.resolve(strict=True)
        except OSError as error:
            raise OutputLocationError("HTMLの保存場所を確認できません") from error
        if not resolved.is_file():
            raise OutputLocationError("HTMLの保存場所を確認できません")
        return resolved.parent / "output"

    def prepare_directory(self, directory: Path | str) -> Path:
        selected = self._validate_directory_value(Path(directory))
        try:
            if selected.exists() or selected.is_symlink():
                if _is_reparse_point(selected):
                    raise OutputLocationError("出力先にリンクは使用できません")
                if not selected.is_dir():
                    raise OutputLocationError("outputと同名のファイルがあります")
            else:
                selected.mkdir(parents=False, exist_ok=False)
            probe = selected / f".output-write-test-{uuid.uuid4().hex}.tmp"
            try:
                with probe.open("xb") as handle:
                    handle.write(b"ok\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                probe.unlink(missing_ok=True)
        except OutputLocationError:
            raise
        except OSError as error:
            raise OutputLocationError("出力フォルダーを作成または使用できません") from error
        return selected

    def stage(self, provider: JobSource, directory: Path | str) -> StagedOutputLocation:
        selected = self._validate_directory_value(Path(directory))
        destination = self._state_path(provider)
        temporary = self._state_dir / (
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        payload = {
            "format": OUTPUT_LOCATION_FORMAT,
            "provider": provider.value,
            "output_directory": str(selected),
        }
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            if _is_reparse_point(self._state_dir):
                raise OutputLocationError("状態フォルダーにリンクは使用できません")
            if destination.exists() and destination.is_dir() and not destination.is_symlink():
                raise OutputLocationError("出力先設定が通常ファイルではありません")
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            verified = self._decode(provider, temporary.read_bytes())
            if verified != selected:
                raise OutputLocationError("出力先設定を検証できません")
        except OutputLocationError:
            _safe_unlink(temporary)
            raise
        except OSError as error:
            _safe_unlink(temporary)
            raise OutputLocationError("出力先設定を準備できません") from error
        return StagedOutputLocation(temporary, destination, selected)

    def _decode(self, provider: JobSource, raw: bytes) -> Path:
        if not raw or len(raw) > _MAX_STATE_BYTES:
            raise OutputLocationError("出力先設定のサイズが不正です")
        if raw.startswith(b"\xef\xbb\xbf") or b"\x00" in raw or b"\r" in raw:
            raise OutputLocationError("出力先設定の文字形式が不正です")
        if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
            raise OutputLocationError("出力先設定の改行形式が不正です")
        try:
            text = raw.decode("utf-8", errors="strict")
            value = json.loads(text, object_pairs_hook=_unique_object)
        except OutputLocationError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OutputLocationError("出力先設定が不正です") from error
        if not isinstance(value, dict) or set(value) != _STATE_KEYS:
            raise OutputLocationError("出力先設定の項目が不正です")
        if value["format"] != OUTPUT_LOCATION_FORMAT:
            raise OutputLocationError("出力先設定の形式が不正です")
        if value["provider"] != provider.value:
            raise OutputLocationError("出力先設定の媒体が一致しません")
        raw_directory = value["output_directory"]
        if not isinstance(raw_directory, str) or not raw_directory:
            raise OutputLocationError("出力先設定のパスが不正です")
        raw_path = Path(raw_directory)
        selected = self._validate_directory_value(raw_path)
        if raw_path != selected:
            raise OutputLocationError("出力先設定のパスが正規化されていません")
        return selected

    @staticmethod
    def _validate_directory_value(directory: Path) -> Path:
        if not directory.is_absolute() or directory.name.casefold() != "output":
            raise OutputLocationError("出力先は絶対パスのoutputフォルダーである必要があります")
        if directory.exists() or directory.is_symlink():
            if _is_reparse_point(directory):
                raise OutputLocationError("出力先にリンクは使用できません")
            if not directory.is_dir():
                raise OutputLocationError("出力先がフォルダーではありません")
        try:
            selected = directory.resolve(strict=False)
        except OSError as error:
            raise OutputLocationError("出力先を解決できません") from error
        if selected.name.casefold() != "output":
            raise OutputLocationError("出力先はoutputフォルダーである必要があります")
        return selected

    def _state_path(self, provider: JobSource) -> Path:
        if not isinstance(provider, JobSource):
            raise OutputLocationError("媒体が不正です")
        return self._state_dir / f"{provider.value.lower()}-output-location-v1.json"


__all__ = [
    "OUTPUT_LOCATION_FORMAT",
    "OutputLocationStore",
    "StagedOutputLocation",
]

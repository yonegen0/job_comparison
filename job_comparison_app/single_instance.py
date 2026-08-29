from __future__ import annotations

import ctypes
import os

from .errors import AlreadyRunningError, InstanceLockError


ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\JobComparisonTool"


class SingleInstanceLock:
    def __init__(self, name: str = MUTEX_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._kernel32 = None

    def acquire(self) -> "SingleInstanceLock":
        if self._handle is not None or os.name != "nt":
            return self
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create = kernel32.CreateMutexW
            create.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
            create.restype = ctypes.c_void_p
            ctypes.set_last_error(0)
            handle = create(None, False, self._name)
            error = ctypes.get_last_error()
        except (AttributeError, OSError) as exc:
            raise InstanceLockError("単一起動ロックを作成できませんでした。") from exc
        if not handle:
            raise InstanceLockError("単一起動ロックを作成できませんでした。")
        self._kernel32 = kernel32
        self._handle = int(handle)
        if error == ERROR_ALREADY_EXISTS:
            self.release()
            raise AlreadyRunningError("求人比較ツールは既に起動しています。")
        return self

    def release(self) -> None:
        handle = self._handle
        kernel32 = self._kernel32
        self._handle = None
        self._kernel32 = None
        if handle is None or kernel32 is None:
            return
        try:
            close = kernel32.CloseHandle
            close.argtypes = (ctypes.c_void_p,)
            close.restype = ctypes.c_bool
            if not close(handle):
                raise InstanceLockError("単一起動ロックを解放できませんでした。")
        except (AttributeError, OSError) as exc:
            raise InstanceLockError("単一起動ロックを解放できませんでした。") from exc

    def __enter__(self) -> "SingleInstanceLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


__all__ = ["MUTEX_NAME", "SingleInstanceLock"]

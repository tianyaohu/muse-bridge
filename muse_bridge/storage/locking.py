"""Advisory file locks for the JSONL store and the key file.

``fcntl.flock`` covers threads in this process and other processes (the admin
CLI and the server). Locks are not re-entrant; each public method takes a
lock once.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import fcntl


def _lock_fd(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    lock_path = path.with_name(path.name + ".lock")
    return os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)


@contextmanager
def _locked(path: Path, mode: int) -> Iterator[None]:
    fd = _lock_fd(path)
    try:
        fcntl.flock(fd, mode)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    with _locked(path, fcntl.LOCK_EX):
        yield


@contextmanager
def shared_lock(path: Path) -> Iterator[None]:
    with _locked(path, fcntl.LOCK_SH):
        yield

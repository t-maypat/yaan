"""Small JSON state files that threads and processes share (D123): tokens, the people met in
the tools, connector cursors.

A write goes to a temp file named for its process and its thread, then replaces the file in
one step: a reader never sees half a file, and two writers never share a temp file. A
read-modify-write (`update`) holds a lock (a thread lock and `<file>.lock`) and re-reads inside
it, so two writers can't lose each other's change, and neither can put back an entry the
other removed. `cached` re-reads a file only when it changed, for files read on every request.
"""

import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from filelock import FileLock
from filelock import Timeout as _LockTimeout

LOCK_TIMEOUT_S = 30.0
REPLACE_TRIES = 40  # x 50 ms: Windows refuses a rename while anyone holds the file open
_guard = threading.Lock()
_thread_locks: dict[str, threading.Lock] = {}
_cache: dict[str, tuple[tuple[int, int, int], Any]] = {}


class Busy(TimeoutError):
    """Another writer held the file's lock for longer than the caller would wait."""


def read(path: Path, default: Any = None) -> Any:
    """The file's JSON, or `default` when there is no file."""
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def cached(path: Path, default: Any = None) -> Any:
    """`read`, re-read only when the file changed (size, mtime or identity). The value is
    shared: treat it as read-only, and change the file through `update`."""
    try:
        st = path.stat()
    except FileNotFoundError:
        _cache.pop(str(path), None)
        return default
    sig = (st.st_mtime_ns, st.st_size, st.st_ino)
    hit = _cache.get(str(path))
    if hit and hit[0] == sig:
        return hit[1]
    value = read(path, default)
    _cache[str(path)] = (sig, value)
    return value


def write(path: Path, data: Any, sort_keys: bool = False) -> None:
    """Atomic: a crash or a concurrent reader sees the old file or the new one, never a mix."""
    write_text(path, json.dumps(data, indent=1, sort_keys=sort_keys) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    replace(tmp, path)


def replace(tmp: Path, path: Path) -> None:
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # a reader (another process, an antivirus scan) has it open
            if attempt == REPLACE_TRIES - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


@contextmanager
def locked(path: Path, timeout: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Hold `path`'s lock: every thread of this process, and every process, that locks it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _guard:
        mine = _thread_locks.setdefault(str(path), threading.Lock())
    if not mine.acquire(timeout=timeout):
        raise Busy(f"{path.name} is busy: another writer held it for {timeout:g} s")
    try:
        with FileLock(str(path.with_name(path.name + ".lock")), timeout=timeout):
            yield
    except _LockTimeout as e:
        raise Busy(f"{path.name} is busy: another process held it for {timeout:g} s") from e
    finally:
        mine.release()


@contextmanager
def update(
    path: Path, timeout: float = LOCK_TIMEOUT_S, sort_keys: bool = False
) -> Iterator[dict[str, Any]]:
    """Read-modify-write under the lock: yields the file's current dict ({} with no file);
    written back when the block ends without an exception and something changed."""
    with locked(path, timeout):
        data = read(path, {})
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            write(path, data, sort_keys=sort_keys)

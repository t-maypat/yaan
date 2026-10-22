"""Shared JSON state files (D123): cached reads follow the file, updates are locked and skip
writing when nothing changed, and a failed update writes nothing."""

import pytest

from app import filestate


def test_a_cached_read_follows_every_change_of_the_file(tmp_path):
    path = tmp_path / "state.json"
    assert filestate.cached(path, {}) == {}
    filestate.write(path, {"a": 1})
    assert filestate.cached(path, {}) == {"a": 1}
    with filestate.update(path) as data:
        data["b"] = 2
    assert filestate.cached(path, {}) == {"a": 1, "b": 2}
    path.unlink()
    assert filestate.cached(path, {}) == {}


def test_an_update_writes_only_a_change_and_nothing_when_it_fails(tmp_path):
    path = tmp_path / "state.json"
    filestate.write(path, {"a": 1})
    before = path.stat().st_mtime_ns
    with filestate.update(path) as data:
        data["a"] = 1  # the same value
    assert path.stat().st_mtime_ns == before
    with pytest.raises(RuntimeError), filestate.update(path) as data:
        data["a"] = 2
        raise RuntimeError("the caller changed its mind")
    assert filestate.read(path) == {"a": 1}
    assert not list(tmp_path.glob("*.tmp"))  # no temp file left behind


def test_a_lock_held_too_long_is_busy_not_a_hang(tmp_path):
    import threading

    path = tmp_path / "state.json"
    caught: list[BaseException] = []

    def other() -> None:
        try:
            with filestate.update(path, timeout=0.05):
                pass
        except filestate.Busy as e:
            caught.append(e)

    with filestate.locked(path):
        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert caught and "busy" in str(caught[0])

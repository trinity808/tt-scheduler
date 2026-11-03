"""
Tests for app/run_lock.py: the lock file that stops two runs overlapping.

Every test works in a temporary folder. Ages are made by setting a lock file's
modified time, and the clock is passed in, so nothing waits and nothing
depends on the real time.

Run from the project root:
    python -m pytest tests/test_run_lock.py -v
"""

import json
import os
import threading

import pytest

from app import run_lock
from app.run_lock import (
    DEFAULT_STALE_MINUTES,
    LockHeld,
    acquire,
    describe,
    minutes_from_config,
    release,
)


NOW = 1_800_000_000.0     # a fixed moment, as the clock passed in
LIMIT = 15 * 60           # the stale limit, in seconds


def clock():
    return NOW


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "sweep.lock")


def make_old(lock_path, seconds):
    """Make the lock file look `seconds` old, as of NOW."""
    os.utime(lock_path, (NOW - seconds, NOW - seconds))


def take(lock_path, mode="live"):
    """
    Take the lock as of NOW. The file's modified time is the real one, so it is
    set to the pretend time too, or the lock would look months old.
    """
    lock = acquire(lock_path, mode, LIMIT, now=clock)
    make_old(lock_path, 0)
    return lock


def contents(lock_path):
    with open(lock_path, encoding="utf-8") as handle:
        return json.load(handle)


# --- Taking and releasing -------------------------------------------


def test_taking_the_lock_creates_the_file_and_says_who_holds_it(path):
    lock = take(path)
    info = contents(path)
    assert info["pid"] == os.getpid()
    assert info["mode"] == "live"
    assert info["token"] == lock.token
    assert info["started"].startswith("2027-01-15")


def test_a_second_run_is_stopped_while_the_first_holds_it(path):
    take(path)
    with pytest.raises(LockHeld):
        acquire(path, "live", LIMIT, now=clock)


def test_the_stopped_run_leaves_the_holders_lock_untouched(path):
    first = take(path)
    before = contents(path)
    with pytest.raises(LockHeld):
        acquire(path, "live", LIMIT, now=clock)
    assert contents(path) == before
    assert contents(path)["token"] == first.token


def test_releasing_deletes_the_file_and_the_lock_can_be_taken_again(path):
    lock = acquire(path, "live", LIMIT, now=clock)
    assert release(lock) is True
    assert not os.path.exists(path)
    acquire(path, "live", LIMIT, now=clock)


def test_releasing_twice_is_harmless(path):
    lock = acquire(path, "live", LIMIT, now=clock)
    assert release(lock) is True
    assert release(lock) is False


def test_a_run_only_deletes_a_lock_that_is_still_its_own(path):
    # This run was slow, so another run took the lock over. When it finally
    # finishes, it must not delete the lock that now belongs to someone else.
    slow = take(path)
    make_old(path, LIMIT + 60)
    successor = acquire(path, "live", LIMIT, now=clock)

    assert release(slow) is False
    assert os.path.exists(path)
    assert contents(path)["token"] == successor.token


def test_only_the_lock_file_is_left_in_the_folder(path):
    acquire(path, "live", LIMIT, now=clock)
    assert os.listdir(os.path.dirname(path)) == ["sweep.lock"]


def test_a_failure_while_writing_leaves_no_lock_behind(path, monkeypatch):
    def broken_write(descriptor, data):
        raise OSError("disk full")

    monkeypatch.setattr(run_lock.os, "write", broken_write)
    with pytest.raises(OSError, match="disk full"):
        acquire(path, "live", LIMIT, now=clock)
    assert not os.path.exists(path)


def test_only_one_of_many_runs_starting_at_once_gets_the_lock(path):
    runs = 12
    barrier = threading.Barrier(runs)
    outcomes = []

    def start():
        barrier.wait()
        try:
            acquire(path, "live", LIMIT)
            outcomes.append("got it")
        except LockHeld:
            outcomes.append("held")

    threads = [threading.Thread(target=start) for _ in range(runs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("got it") == 1
    assert outcomes.count("held") == runs - 1


# --- Old locks ------------------------------------------------------


def test_a_lock_just_under_the_limit_still_blocks(path):
    take(path)
    make_old(path, LIMIT - 1)
    with pytest.raises(LockHeld):
        acquire(path, "live", LIMIT, now=clock)


def test_a_lock_at_the_limit_is_taken_over(path):
    take(path)
    make_old(path, LIMIT)
    assert acquire(path, "live", LIMIT, now=clock)


def test_taking_over_replaces_the_lock_and_remembers_the_old_one(path):
    old = take(path)
    old_pid = contents(path)["pid"]
    make_old(path, 25 * 60)

    new = acquire(path, "live", LIMIT, now=clock)

    assert contents(path)["token"] == new.token != old.token
    assert new.taken_over["pid"] == old_pid
    assert new.taken_over["token"] == old.token
    assert new.taken_over_age == pytest.approx(25 * 60)


def test_a_lock_taken_without_taking_over_says_so(path):
    lock = acquire(path, "live", LIMIT, now=clock)
    assert lock.taken_over is None and lock.taken_over_age is None


def test_a_lock_with_unreadable_contents_is_still_taken_over_when_old(path):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("not json at all")
    make_old(path, LIMIT + 5)
    lock = acquire(path, "live", LIMIT, now=clock)
    assert lock.taken_over == {}


def test_a_fresh_lock_with_unreadable_contents_still_blocks(path):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("")
    make_old(path, 10)
    with pytest.raises(LockHeld) as caught:
        acquire(path, "live", LIMIT, now=clock)
    assert caught.value.info == {}


def test_age_comes_from_the_files_time_not_from_what_is_written_in_it(path):
    take(path)
    info = contents(path)
    info["started"] = "2001-01-01T00:00:00"  # claims to be ancient
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(info, handle)
    make_old(path, 30)                        # but the file itself is fresh
    with pytest.raises(LockHeld):
        acquire(path, "live", LIMIT, now=clock)


def test_an_old_lock_is_not_deleted_if_it_was_replaced_while_being_judged(path, monkeypatch):
    # The lock looks old when first inspected, but by the time it would be
    # deleted someone else has replaced it with a fresh one.
    take(path)
    make_old(path, LIMIT + 100)
    times = iter([NOW - LIMIT - 100, NOW - 1])
    last = {"value": NOW - 1}

    def shifting(_path):
        last["value"] = next(times, last["value"])
        return last["value"]

    monkeypatch.setattr(run_lock.os.path, "getmtime", shifting)
    with pytest.raises(LockHeld):
        acquire(path, "live", LIMIT, now=clock)
    assert os.path.exists(path)


def test_the_blocked_run_reports_how_old_the_lock_is(path):
    take(path)
    make_old(path, 4 * 60)
    with pytest.raises(LockHeld) as caught:
        acquire(path, "live", LIMIT, now=clock)
    assert caught.value.age_seconds == pytest.approx(4 * 60)


def test_a_lock_dated_a_moment_in_the_future_blocks_and_has_an_age_of_zero(path):
    # On Windows a file's time can be a few milliseconds ahead of the clock.
    take(path)
    ahead = NOW + 0.05
    os.utime(path, (ahead, ahead))
    with pytest.raises(LockHeld) as caught:
        acquire(path, "live", LIMIT, now=clock)
    assert caught.value.age_seconds == 0
    assert str(caught.value).endswith(", 0.0 minutes old")


def test_the_last_resort_report_also_has_a_sane_age(path, monkeypatch):
    # The lock keeps looking released and then turns out to exist, three times
    # in a row. The run gives up and reports it, and that age must not be negative either.
    take(path)
    calls = {"n": 0}

    def flickering(_path):
        calls["n"] += 1
        if calls["n"] <= run_lock._ATTEMPTS:
            raise FileNotFoundError
        return NOW + 0.05

    monkeypatch.setattr(run_lock.os.path, "getmtime", flickering)
    with pytest.raises(LockHeld) as caught:
        acquire(path, "live", LIMIT, now=clock)
    assert caught.value.age_seconds == 0


# --- Describing a lock ----------------------------------------------


def test_an_age_below_zero_is_described_as_zero():
    assert describe({"pid": 7}, -3).endswith("0.0 minutes old")
    assert "-" not in describe({"pid": 7}, -0.02)


def test_a_lock_is_described_with_who_how_and_since_when():
    text = describe({"pid": 4321, "mode": "live", "started": "2026-11-02T10:00:05"}, 120)
    assert text == "held by process 4321 (live) since 2026-11-02T10:00:05, 2.0 minutes old"


def test_a_lock_with_nothing_readable_is_still_described():
    assert describe({}, 90) == "held by an unknown process, 1.5 minutes old"


def test_the_message_of_a_blocked_run_is_that_description(path):
    take(path)
    with pytest.raises(LockHeld) as caught:
        acquire(path, "live", LIMIT, now=clock)
    assert f"held by process {os.getpid()} (live)" in str(caught.value)


# --- The stale limit from the settings ------------------------------


def test_the_limit_defaults_to_fifteen_minutes():
    assert DEFAULT_STALE_MINUTES == 15
    assert minutes_from_config({}) == (15, None)
    assert minutes_from_config(None) == (15, None)


def test_the_limit_can_be_set():
    assert minutes_from_config({"sweep_lock_stale_minutes": 30}) == (30, None)
    assert minutes_from_config({"sweep_lock_stale_minutes": 0.5}) == (0.5, None)


@pytest.mark.parametrize("bad", [0, -5, "15", True, False, float("nan"), float("inf"), [], {}])
def test_a_limit_that_is_not_a_positive_number_falls_back_with_a_message(bad):
    minutes, problem = minutes_from_config({"sweep_lock_stale_minutes": bad})
    assert minutes == 15
    assert "sweep_lock_stale_minutes" in problem and "so 15 is used" in problem
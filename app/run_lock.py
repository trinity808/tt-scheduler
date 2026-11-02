"""
A lock file that stops two runs of the same command from overlapping.

A run creates the file at the start and deletes it at the end. A second run
that finds the file there knows another run is in progress, and backs off.

Creating the file and checking that it isn't already there happen as one step.
The operating system is asked to create the file only if it doesn't exist
(O_CREAT | O_EXCL), so when two runs start at the same instant exactly one of
them wins. Checking first and creating second would let both in.

A run that is killed hard, by a power cut or a closed window, never gets to
delete its lock. So a lock older than a limit is taken to belong to a dead
run, and the next run takes it over and says so. The age is the file's
modified time, not what is written inside it, so a lock whose contents are
unreadable can still be recognized as old. Without that limit one crash would
block every later run in silence, which is the weakness of the DOCX pipeline's
scheduler.lock.

Taking over is done by deleting the old lock and creating a new one, only if
the old file is still the one that was judged old. Two runs that find the same
old lock at the same instant could still both get in. That needs a crash and
two starts within a few milliseconds of each other, and the sweep's own design
(a cell is always rebuilt from the original plus the comment) contains what an
overlap could do.

Only the run that took a lock may delete it. A run that was slow enough to be
taken over doesn't delete its successor's lock when it finishes.

The lock says which process holds it, in which mode, and since when, so a log
line can name it.
"""

import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime


DEFAULT_STALE_MINUTES = 15
_ATTEMPTS = 3


def minutes_from_config(config, key="sweep_lock_stale_minutes", default=DEFAULT_STALE_MINUTES):
    """
    How many minutes old a lock must be before it is taken over, from the
    settings. Returns (minutes, problem). A missing value is the default and
    no problem. A value that isn't a positive number is the default, with a
    message, because a limit of zero or less would make every lock "old" and
    switch the protection off.
    """
    value = (config or {}).get(key)
    if value is None:
        return default, None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        return default, (
            f"{key} must be a positive number of minutes, not {value!r}, "
            f"so {default} is used"
        )
    return value, None


class LockHeld(Exception):
    """Another run holds the lock, and it isn't old enough to be taken over."""

    def __init__(self, info, age_seconds):
        self.info = info
        self.age_seconds = age_seconds
        super().__init__(describe(info, age_seconds))


@dataclass
class Lock:
    path: str
    token: str
    taken_over: dict = None         # what the old lock said, if this one replaced one
    taken_over_age: float = None    # how old that lock was, in seconds


def describe(info, age_seconds):
    """A lock in words, for a log: who holds it, in which mode, since when."""
    who = f"process {info['pid']}" if info.get("pid") else "an unknown process"
    mode = f" ({info['mode']})" if info.get("mode") else ""
    started = f" since {info['started']}" if info.get("started") else ""
    return f"held by {who}{mode}{started}, {age_seconds / 60:.1f} minutes old"


def _read_info(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _age(path, now):
    try:
        return now() - os.path.getmtime(path)
    except OSError:
        return 0.0


def _create(path, mode, token, now):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        info = {
            "pid": os.getpid(),
            "mode": mode,
            "started": datetime.fromtimestamp(now()).isoformat(timespec="seconds"),
            "token": token,
        }
        os.write(descriptor, json.dumps(info).encode("utf-8"))
    except BaseException:
        os.close(descriptor)
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    os.close(descriptor)


def acquire(path, mode, stale_after_seconds, now=time.time):
    """
    Takes the lock, or raises LockHeld if another run has it and it isn't old
    enough to take over. `now` is for tests.
    """
    token = uuid.uuid4().hex
    replaced, replaced_age = None, None

    for _ in range(_ATTEMPTS):
        try:
            _create(path, mode, token, now)
            return Lock(path, token, replaced, replaced_age)
        except FileExistsError:
            pass

        try:
            seen = os.path.getmtime(path)
        except FileNotFoundError:
            continue  # it was released between our attempt and now: try again

        age = now() - seen
        info = _read_info(path)
        if age < stale_after_seconds:
            raise LockHeld(info, age)

        # Old enough to take over, but only if it is still the file we judged.
        try:
            if os.path.getmtime(path) != seen:
                continue
            os.unlink(path)
        except FileNotFoundError:
            pass
        replaced, replaced_age = info, age

    raise LockHeld(_read_info(path), _age(path, now))


def release(lock):
    """
    Deletes the lock if it is still ours. Returns True if it did. False means
    the lock was already gone, or belongs to someone else by now, because this
    run was slow enough to be taken over.
    """
    if _read_info(lock.path).get("token") != lock.token:
        return False
    try:
        os.unlink(lock.path)
    except FileNotFoundError:
        return False
    return True
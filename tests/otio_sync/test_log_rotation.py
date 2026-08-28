"""Tests for sync_test's per-test log rotation.

Every run used to overwrite ``logs/<test>/`` in place, so an intermittent
failure followed by a passing run destroyed the only record of the failure.
Two separately root-caused flakes were diagnosable only because their failure
happened to be the last run of that test. These tests pin the rotation that
keeps a short history instead.

Lives under ``tests/otio_sync/`` because that is what ``run_tests_core.sh``
executes; the code under test is the sync_test harness, not otio_sync_core.
"""

import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../sync_test/python')))

from sync_test.spawner import rotate_log_dirs, RETAINED_LOG_RUNS  # noqa: E402


def _mk(path, marker):
    """Create a log dir containing a single file identifying which run it was."""
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "runner.log"), "w") as fh:
        fh.write(marker)


def _marker(path):
    with open(os.path.join(path, "runner.log")) as fh:
        return fh.read()


def test_previous_run_is_preserved_as_slot_1(tmp_path):
    logs = str(tmp_path / "add_media")
    _mk(logs, "run-A")

    rotate_log_dirs(logs)

    # The current slot is vacated for the incoming run, and the run that was
    # there is still readable one slot along.
    assert not os.path.exists(logs)
    assert _marker(f"{logs}_1") == "run-A"


def test_runs_shift_down_in_order_without_clobbering(tmp_path):
    """The shift must walk oldest-first; walking the other way overwrites a
    slot before its occupant has been moved out of it."""
    logs = str(tmp_path / "add_media")
    _mk(logs, "run-C")
    _mk(f"{logs}_1", "run-B")
    _mk(f"{logs}_2", "run-A")

    rotate_log_dirs(logs)

    assert _marker(f"{logs}_1") == "run-C"
    assert _marker(f"{logs}_2") == "run-B"
    assert _marker(f"{logs}_3") == "run-A"


def test_history_is_capped_and_the_oldest_is_evicted(tmp_path):
    logs = str(tmp_path / "add_media")
    _mk(logs, "newest")
    for i in range(1, RETAINED_LOG_RUNS):
        _mk(f"{logs}_{i}", f"old-{i}")

    rotate_log_dirs(logs)

    # Retention counts the current slot, so archives stop one short of it.
    assert os.path.exists(f"{logs}_{RETAINED_LOG_RUNS - 1}")
    assert not os.path.exists(f"{logs}_{RETAINED_LOG_RUNS}")
    assert _marker(f"{logs}_1") == "newest"


def test_repeated_rotation_holds_the_cap(tmp_path):
    """Rotating many times must not grow the history without bound."""
    logs = str(tmp_path / "add_media")
    for run in range(RETAINED_LOG_RUNS * 3):
        _mk(logs, f"run-{run}")
        rotate_log_dirs(logs)

    kept = [d for d in os.listdir(tmp_path) if d.startswith("add_media")]
    assert len(kept) == RETAINED_LOG_RUNS - 1
    # Most recent rotation is always slot 1.
    assert _marker(f"{logs}_1") == f"run-{RETAINED_LOG_RUNS * 3 - 1}"


def test_first_ever_run_creates_no_empty_archive(tmp_path):
    """Nothing to preserve must not leave a stray empty slot behind."""
    logs = str(tmp_path / "never_run_before")

    rotate_log_dirs(logs)

    assert not os.path.exists(f"{logs}_1")


def test_empty_dir_is_not_rotated(tmp_path):
    """An empty dir carries no evidence; rotating it would push out a real run."""
    logs = str(tmp_path / "add_media")
    os.makedirs(logs)
    _mk(f"{logs}_1", "real-run")

    rotate_log_dirs(logs)

    assert _marker(f"{logs}_1") == "real-run"


@pytest.mark.parametrize("keep", [0, 1])
def test_retention_below_two_disables_rotation(tmp_path, keep):
    logs = str(tmp_path / "add_media")
    _mk(logs, "run-A")

    rotate_log_dirs(logs, keep=keep)

    assert _marker(logs) == "run-A"
    assert not os.path.exists(f"{logs}_1")


def test_rotation_failure_does_not_raise(tmp_path):
    """A rotation problem must never take down the test run itself."""
    logs = str(tmp_path / "add_media")
    _mk(logs, "run-A")
    # A plain file where the archive slot belongs makes the rename fail.
    with open(f"{logs}_1", "w") as fh:
        fh.write("not a directory")

    rotate_log_dirs(logs)  # must not raise

## 1. Pytest Configuration

- [x] 1.1 Add `pytest.ini` with `xstudio` marker definition.
- [x] 1.2 Create `tests/xstudio_plugin/conftest.py` that skips all tests in its directory if `xstudio` module cannot be imported, and adds the `xstudio` marker to them.

## 2. Moving Tests

- [x] 2.1 Move `tests/otio_sync/test_display_coords.py` to `tests/xstudio_plugin/test_display_coords.py`.

## 3. Fixing Tests

- [x] 3.1 Fix `bad any cast` in `tests/otio_sync/test_rv_annotation_codec.py` by swallowing `module_from_name` duplicate registration exceptions.
- [x] 3.2 Update `FakePlugin` in `tests/xstudio_plugin/test_playback_echo_guard.py` to implement `stamp_remote_apply` and `claim_lease`.
- [x] 3.3 Update `FakeXsTimeline` in `tests/xstudio_plugin/test_sequence_reconciliation_convergence.py` to include a `tracks` attribute.

## 4. Runner Scripts

- [x] 4.1 Create `run_tests_core.sh` to execute standard pytest.
- [x] 4.2 Create `run_tests_xstudio.sh` to execute xStudio-dependent tests with the bundled Python interpreter.
- [x] 4.3 Make both shell scripts executable.

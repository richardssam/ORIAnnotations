## Context

The testing structure currently mixes tests that require standard Python (`otio_sync`) and tests that require the xStudio Python bindings (`xstudio_plugin`). Running all tests with a standard `pytest` command crashes when pytest attempts to collect the xStudio-dependent tests. See `proposal.md` for motivation.

## Goals / Non-Goals

**Goals:**
- Provide single-command shell scripts to execute the correct tests with the correct environments.
- Allow a global `pytest` call in standard Python to succeed by gracefully skipping xStudio-dependent tests.
- Fix broken test mocks so the suite reliably passes.

**Non-Goals:**
- Do not build a complex test orchestration system (e.g. `tox` or massive Makefiles) unless necessary; simple shell scripts are sufficient.
- Do not refactor the production code itself; we are only fixing the testing environment.

## Decisions

- **Option B (Pytest Markers) + Option A (Runner Scripts)**:
  - Adding an `xstudio` marker and a `conftest.py` in `tests/xstudio_plugin/` ensures `pytest` skips xStudio tests when run in standard python.
  - Adding `run_tests_core.sh` and `run_tests_xstudio.sh` makes it extremely simple for developers without requiring a `Makefile`.
  
- **Test Locations**:
  - `test_display_coords.py` tests `xstudio_plugin` code. It will be moved to `tests/xstudio_plugin/`.

- **Test Fixes**:
  - `bad any cast` in `test_rv_annotation_codec.py`: The `opentimelineio` C++ bindings crash if the same manifest is registered repeatedly across tests. This will be wrapped in a `try...except Exception` block, falling back if it's already registered.
  - `FakePlugin` & `FakeXsTimeline`: These are missing `.stamp_remote_apply()`, `.claim_lease()`, and `.tracks` properties. We will add no-op implementations of these to the mocks to satisfy the production code that calls them.

## Risks / Trade-offs

- **Risk**: xStudio tests might be accidentally skipped if the runner script fails to locate the xStudio Python executable.
  - **Mitigation**: The `run_tests_xstudio.sh` script will hardcode the expected path from the developer environment (`/Users/sam/git/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3`), and emit a clear error if the executable is missing.

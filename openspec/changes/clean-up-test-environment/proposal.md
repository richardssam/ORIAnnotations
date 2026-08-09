## Why

The current test environment is confusing and brittle because `xstudio_plugin` tests are mixed with `otio_sync` core tests, and tests requiring the xStudio bundled Python interpreter fail confusingly when run with standard Python. Some tests are outdated due to plugin API updates (`stamp_remote_apply` and `claim_lease`). Cleaning this up will make it easy to run tests and keep CI/local runs stable.

## What Changes

- Add root-level runner scripts (`run_tests_core.sh` and `run_tests_xstudio.sh`) for convenience.
- Configure pytest to skip xStudio-dependent tests in the standard environment.
- Move `test_display_coords.py` to its proper `xstudio_plugin` directory.
- Update `FakePlugin` and `FakeXsTimeline` mocks to implement recently added API methods.
- Fix `bad any cast` in `test_rv_annotation_codec.py` by catching redundant schema registration exceptions.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
None. This is a pure test environment and tooling refactor. (We will add `skip_specs: true` to `.openspec.yaml`).

## Impact

- **Developer Experience**: Running tests becomes as easy as executing a script. Standard `pytest` won't crash on xStudio imports.
- **Tests**: Three currently broken tests will start passing again.

## 1. Network Core Refactor

- [x] 1.1 Add `_send_event` helper to `otio_sync_core/manager.py` to dispatch nested ASWF envelopes (`LiveSession.1` schema structure).
- [x] 1.2 Update payload reception and parsing in `otio_sync_core/manager.py` to extract commands from the nested payload wrapper.
- [x] 1.3 Ensure `I_AM_MASTER` handshake correctly injects the top-level `schema: "SYNC_REVIEW_1.0"`.

## 2. Legacy Recording Migration

- [x] 2.1 Create `sync_recorder/convert_format.py` script capable of parsing legacy flat `.jsonl` recordings and mutating them into ASWF nested format.
- [x] 2.2 Run conversion script across standard integration recordings (e.g. `color.jsonl`, `reorder.jsonl`).
- [x] 2.3 Run conversion script across all test suite recordings located in `sync_test/recordings/`.

## Tasks
- [x] Migrate `sync_recorder` recordings to the new nested format
- [x] Update `otio_sync_core/manager.py` to use the nested format for sending/receiving
- [x] Update `sync_test/runner.py` to interpret commands from nested schema
- [x] Run test suite and fix any parsing/selection issues
- [x] 3.3 Update mock UDP payload definitions inside `tests/otio_sync/test_sync_recorder.py`.
- [x] 3.4 Verify `test_sync_recorder.py` suite passes successfully.
- [x] 3.5 Verify `sync_test/run_tests.sh` suite passes successfully.

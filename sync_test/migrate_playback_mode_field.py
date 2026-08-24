#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One-off migration: rename the recorded ``looping: bool`` field to
``playback_mode: str`` across sync_test/recordings/*.jsonl.

See openspec/changes/expand-playback-modes/design.md (D3). Every recorded
value maps unambiguously (``true`` -> "loop", ``false`` -> "play-once") since
no recording ever captured ping-pong (it didn't exist on the wire yet).

Walks each JSON object in each recording line-by-line, recursively renaming
any dict's ``"looping"`` key (found in both standalone PLAYBACK_SETTINGS_1.0
SET payloads and nested STATE_SNAPSHOT.playback_state objects) to
``"playback_mode"``. Fails loudly if ``"looping"`` appears with anything but
a literal bool value, rather than silently skipping it.
"""
import glob
import json
import os
import sys

RECORDINGS_DIR = os.path.join(os.path.dirname(__file__), "recordings")


def _migrate(obj):
    """Recursively rename "looping": bool -> "playback_mode": str in-place."""
    if isinstance(obj, dict):
        if "looping" in obj:
            value = obj["looping"]
            if not isinstance(value, bool):
                raise ValueError(
                    f"unrecognized 'looping' value (not a bool): {value!r} in {obj!r}"
                )
            new_obj = {}
            for k, v in obj.items():
                if k == "looping":
                    new_obj["playback_mode"] = "loop" if value else "play-once"
                else:
                    new_obj[k] = v
            obj.clear()
            obj.update(new_obj)
        for v in obj.values():
            _migrate(v)
    elif isinstance(obj, list):
        for item in obj:
            _migrate(item)


def migrate_file(path):
    with open(path, "r") as f:
        lines = f.readlines()
    changed = 0
    out_lines = []
    for lineno, line in enumerate(lines, 1):
        line = line.rstrip("\n")
        if not line:
            out_lines.append(line)
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            # Pre-existing corrupt line (not introduced by this script) — the
            # existing sync_test readers already tolerate/skip these. Leave
            # byte-identical rather than guessing at a repair.
            print(f"  WARNING: {path}:{lineno} is not valid JSON — left unchanged", file=sys.stderr)
            out_lines.append(line)
            continue
        before = json.dumps(obj, sort_keys=True)
        _migrate(obj)
        after = json.dumps(obj, sort_keys=True)
        if before != after:
            changed += 1
        out_lines.append(json.dumps(obj))
    with open(path, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    return changed


def main():
    paths = sorted(glob.glob(os.path.join(RECORDINGS_DIR, "*.jsonl")))
    if not paths:
        print(f"no recordings found in {RECORDINGS_DIR}", file=sys.stderr)
        return 1
    total_changed = 0
    for path in paths:
        changed = migrate_file(path)
        total_changed += changed
        print(f"{os.path.basename(path)}: {changed} lines migrated")
    print(f"TOTAL: {total_changed} lines migrated across {len(paths)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

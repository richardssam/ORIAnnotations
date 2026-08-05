import yaml
import os
import logging

#: `description`/`status`/`blocked_by` are required only in the canonical
#: suite definition. Other config files (sync_tests_xstudio.yaml,
#: sync_demos.yaml) duplicate a subset of its entries by name/recording for
#: convenience and are not independently authored, so requiring them to also
#: carry this metadata would just be copy-paste duplication with nowhere new
#: for the information to come from — sync_tests.yaml is the single source
#: of truth for a test's intent (see sync-tests-tracking design.md).
CANONICAL_SUITE_FILENAME = "sync_tests.yaml"

VALID_STATUSES = {"stable", "known_broken"}


class SyncTestConfig:
    def __init__(self, tests, settings=None):
        self.tests = tests
        self.settings = settings or {}

    @classmethod
    def from_file(cls, path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Configuration file not found: {path}")

        with open(path, 'r') as f:
            data = yaml.safe_load(f)

        if not data or 'tests' not in data:
            raise ValueError(f"Invalid configuration format in {path}. Expected a 'tests' key.")

        settings = data.get('settings', {})
        is_canonical = os.path.basename(path) == CANONICAL_SUITE_FILENAME

        parsed_tests = []
        for t in data['tests']:
            name = t.get('name')
            recording = t.get('recording')

            script_driven = t.get('script_driven', False)
            if not name or (not recording and not script_driven):
                logging.warning("Skipping test with missing 'name' or 'recording'")
                continue

            status = t.get('status', 'stable')
            if status not in VALID_STATUSES:
                raise ValueError(
                    f"Invalid status {status!r} for test {name!r} in {path}. "
                    f"Expected one of {sorted(VALID_STATUSES)}."
                )

            if is_canonical:
                if not t.get('description'):
                    raise ValueError(
                        f"Test {name!r} in {path} is missing a required 'description' "
                        "field — every test in the canonical suite must explain what "
                        "scenario it exercises and why it exists."
                    )
                if status == 'known_broken' and not t.get('blocked_by'):
                    raise ValueError(
                        f"Test {name!r} in {path} has status 'known_broken' but no "
                        "'blocked_by' — name the OpenSpec change expected to resolve it."
                    )

            parsed_tests.append({
                **t,
                "script_driven": t.get('script_driven', False),
                "status": status,
            })

        return cls(tests=parsed_tests, settings=settings)

    def get_test(self, name):
        for t in self.tests:
            if t['name'] == name:
                return t
        return None

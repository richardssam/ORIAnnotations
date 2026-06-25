import yaml
import os
import logging

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
        
        parsed_tests = []
        for t in data['tests']:
            name = t.get('name')
            recording = t.get('recording')
            apps = t.get('apps', [])
            
            script_driven = t.get('script_driven', False)
            if not name or (not recording and not script_driven):
                logging.warning("Skipping test with missing 'name' or 'recording'")
                continue
                
            parsed_tests.append({
                **t,
                "script_driven": t.get('script_driven', False),
            })
            
        return cls(tests=parsed_tests, settings=settings)
        
    def get_test(self, name):
        for t in self.tests:
            if t['name'] == name:
                return t
        return None

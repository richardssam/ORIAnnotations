"""session-config-file: a plugin-folder config file pre-defining connection defaults."""
import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(repo_root, "python"))

from otio_sync_core.site_config import load_site_config  # noqa: E402


def test_no_file_returns_none(tmp_path):
    assert load_site_config(str(tmp_path)) is None


def test_valid_file_is_used(tmp_path):
    (tmp_path / "ori_site_config.json").write_text(json.dumps({"host": "mq.example.com", "port": 5673}))
    result = load_site_config(str(tmp_path))
    assert result == {"host": "mq.example.com", "port": 5673}


def test_malformed_file_falls_back_to_none(tmp_path):
    (tmp_path / "ori_site_config.json").write_text("{not valid json")
    assert load_site_config(str(tmp_path)) is None


def test_file_with_neither_key_returns_none(tmp_path):
    (tmp_path / "ori_site_config.json").write_text(json.dumps({"unrelated": "value"}))
    assert load_site_config(str(tmp_path)) is None


def test_host_only_omits_port(tmp_path):
    (tmp_path / "ori_site_config.json").write_text(json.dumps({"host": "mq.example.com"}))
    assert load_site_config(str(tmp_path)) == {"host": "mq.example.com"}


def test_string_port_is_coerced_to_int(tmp_path):
    (tmp_path / "ori_site_config.json").write_text(json.dumps({"host": "mq.example.com", "port": "5673"}))
    assert load_site_config(str(tmp_path)) == {"host": "mq.example.com", "port": 5673}


def test_custom_filename(tmp_path):
    (tmp_path / "custom.json").write_text(json.dumps({"host": "mq.example.com"}))
    assert load_site_config(str(tmp_path), filename="custom.json") == {"host": "mq.example.com"}
    assert load_site_config(str(tmp_path)) is None

import json
from pathlib import Path

from src.auth import load_config, AuthConfig, SETUP_INSTRUCTIONS


def test_load_config_valid(tmp_path):
    """Loads client_id from config.json."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"client_id": "test-id-123"}))
    config = load_config(config_file)
    assert config.client_id == "test-id-123"


def test_load_config_missing_file(tmp_path):
    """Returns None when config.json doesn't exist."""
    config = load_config(tmp_path / "config.json")
    assert config is None


def test_load_config_missing_client_id(tmp_path):
    """Returns None when client_id key is missing."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"other_key": "value"}))
    config = load_config(config_file)
    assert config is None


def test_setup_instructions_exist():
    """Setup instructions string is non-empty and mentions Azure."""
    assert "azure" in SETUP_INSTRUCTIONS.lower()
    assert "client_id" in SETUP_INSTRUCTIONS

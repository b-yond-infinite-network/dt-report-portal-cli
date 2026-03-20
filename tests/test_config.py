"""Tests for config module."""

import os
import stat
from pathlib import Path

import pytest
import tomli_w

from rp_fetch.config import Settings, load_settings, write_config


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Redirect config to a temp directory."""
    import rp_fetch.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.toml")
    return tmp_path


def test_settings_defaults():
    s = Settings()
    assert s.base_url == ""
    assert s.api_key == ""
    assert s.project == ""
    assert s.output_directory == "./rp-downloads"


def test_write_config_creates_file(config_dir):
    path = write_config(
        base_url="https://rp.example.com",
        api_key="test-key-123",
        project="my_project",
    )
    assert path.exists()
    # Check permissions (600)
    mode = path.stat().st_mode
    assert mode & stat.S_IRWXG == 0  # no group access
    assert mode & stat.S_IRWXO == 0  # no other access


def test_load_settings_from_file(config_dir):
    write_config(
        base_url="https://rp.example.com",
        api_key="test-key-123",
        project="my_project",
        output_directory="./output",
    )
    settings = load_settings()
    assert settings.base_url == "https://rp.example.com"
    assert settings.api_key == "test-key-123"
    assert settings.project == "my_project"
    assert settings.output_directory == "./output"


def test_load_settings_env_overrides_file(config_dir, monkeypatch):
    write_config(
        base_url="https://rp.example.com",
        api_key="file-key",
        project="file_project",
    )
    monkeypatch.setenv("RP_API_KEY", "env-key")
    monkeypatch.setenv("RP_PROJECT", "env_project")

    settings = load_settings()
    assert settings.base_url == "https://rp.example.com"  # from file
    assert settings.api_key == "env-key"  # from env
    assert settings.project == "env_project"  # from env


def test_load_settings_cli_overrides_all(config_dir, monkeypatch):
    write_config(
        base_url="https://rp.example.com",
        api_key="file-key",
        project="file_project",
    )
    monkeypatch.setenv("RP_API_KEY", "env-key")

    settings = load_settings(api_key="cli-key", project="cli_project")
    assert settings.api_key == "cli-key"
    assert settings.project == "cli_project"


def test_load_settings_no_config_file(config_dir):
    settings = load_settings()
    assert settings.base_url == ""
    assert settings.api_key == ""

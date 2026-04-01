"""Tests for config module."""

import os
import stat
import sys
from pathlib import Path

import pytest
import tomli_w

from rp_fetch.config import (
    OAuth2Settings,
    ProxySettings,
    Settings,
    load_settings,
    write_config,
)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Redirect config to a temp directory."""
    import rp_fetch.config as cfg

    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.toml")
    return tmp_path


# ------------------------------------------------------------------
# Settings model defaults
# ------------------------------------------------------------------


def test_settings_defaults():
    s = Settings()
    assert s.base_url == ""
    assert s.api_key == ""
    assert s.project == ""
    assert s.output_directory == "./rp-downloads"
    assert s.proxy.url == ""
    assert s.proxy.auth_type == "none"


# ------------------------------------------------------------------
# Basic config read/write (existing behaviour)
# ------------------------------------------------------------------


def test_write_config_creates_file(config_dir):
    path = write_config(
        base_url="https://rp.example.com",
        api_key="test-key-123",
        project="my_project",
    )
    assert path.exists()
    if sys.platform != "win32":
        mode = path.stat().st_mode
        assert mode & stat.S_IRWXG == 0
        assert mode & stat.S_IRWXO == 0


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
    assert settings.base_url == "https://rp.example.com"
    assert settings.api_key == "env-key"
    assert settings.project == "env_project"


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


# ------------------------------------------------------------------
# Proxy config — no proxy
# ------------------------------------------------------------------


def test_no_proxy_section_when_not_configured(config_dir):
    import tomllib

    write_config(base_url="https://rp.example.com", api_key="k", project="p")
    with open(config_dir / "config.toml", "rb") as f:
        data = tomllib.load(f)
    assert "proxy" not in data


def test_load_settings_without_proxy(config_dir):
    write_config(base_url="https://rp.example.com", api_key="k", project="p")
    settings = load_settings()
    assert not settings.proxy.is_configured


# ------------------------------------------------------------------
# Proxy config — basic auth
# ------------------------------------------------------------------


def test_write_and_load_proxy_basic(config_dir):
    proxy = ProxySettings(
        url="http://proxy:8080", auth_type="basic", username="alice", password="secret"
    )
    write_config(
        base_url="https://rp.example.com", api_key="k", project="p", proxy=proxy
    )
    settings = load_settings()
    assert settings.proxy.url == "http://proxy:8080"
    assert settings.proxy.auth_type == "basic"
    assert settings.proxy.username == "alice"
    assert settings.proxy.password == "secret"


# ------------------------------------------------------------------
# Proxy config — token auth
# ------------------------------------------------------------------


def test_write_and_load_proxy_token(config_dir):
    proxy = ProxySettings(
        url="http://proxy:3128", auth_type="token", token="my-otp-999"
    )
    write_config(
        base_url="https://rp.example.com", api_key="k", project="p", proxy=proxy
    )
    settings = load_settings()
    assert settings.proxy.auth_type == "token"
    assert settings.proxy.token == "my-otp-999"


# ------------------------------------------------------------------
# Proxy config — oauth2
# ------------------------------------------------------------------


def test_write_and_load_proxy_oauth2(config_dir):
    oauth2 = OAuth2Settings(
        authorize_url="https://idp/authorize",
        token_url="https://idp/token",
        client_id="my-app",
        scopes="openid email",
        refresh_token="refresh-xyz",
        access_token="access-abc",
        token_expiry="2026-06-01T12:00:00+00:00",
    )
    proxy = ProxySettings(url="http://proxy:8080", auth_type="oauth2", oauth2=oauth2)
    write_config(
        base_url="https://rp.example.com", api_key="k", project="p", proxy=proxy
    )

    settings = load_settings()
    assert settings.proxy.auth_type == "oauth2"
    assert settings.proxy.oauth2.authorize_url == "https://idp/authorize"
    assert settings.proxy.oauth2.client_id == "my-app"
    assert settings.proxy.oauth2.refresh_token == "refresh-xyz"
    assert settings.proxy.oauth2.access_token == "access-abc"


# ------------------------------------------------------------------
# Proxy config — TOML structure validation
# ------------------------------------------------------------------


def test_toml_structure_basic_proxy(config_dir):
    import tomllib

    proxy = ProxySettings(
        url="http://p:80", auth_type="basic", username="u", password="p"
    )
    write_config(
        base_url="https://rp.example.com", api_key="k", project="p", proxy=proxy
    )
    with open(config_dir / "config.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["proxy"]["url"] == "http://p:80"
    assert data["proxy"]["auth_type"] == "basic"
    assert "oauth2" not in data["proxy"]


def test_toml_structure_oauth2_proxy(config_dir):
    import tomllib

    oauth2 = OAuth2Settings(
        authorize_url="https://a", token_url="https://t", client_id="c"
    )
    proxy = ProxySettings(url="http://p:80", auth_type="oauth2", oauth2=oauth2)
    write_config(
        base_url="https://rp.example.com", api_key="k", project="p", proxy=proxy
    )
    with open(config_dir / "config.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["proxy"]["auth_type"] == "oauth2"
    assert data["proxy"]["oauth2"]["authorize_url"] == "https://a"
    assert data["proxy"]["oauth2"]["client_id"] == "c"

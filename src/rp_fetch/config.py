"""Configuration management: TOML read/write and settings resolution."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, Field

CONFIG_DIR = Path.home() / ".rp-fetch"
CONFIG_FILE = CONFIG_DIR / "config.toml"

# Env var names
ENV_BASE_URL = "RP_BASE_URL"
ENV_API_KEY = "RP_API_KEY"
ENV_PROJECT = "RP_PROJECT"


# ------------------------------------------------------------------
# Proxy settings models
# ------------------------------------------------------------------


class OAuth2Settings(BaseModel):
    authorize_url: str = ""
    token_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    scopes: str = "openid"
    refresh_token: str = ""
    access_token: str = ""
    token_expiry: str = ""

    model_config = {"populate_by_name": True}


class ProxySettings(BaseModel):
    url: str = ""
    auth_type: str = "none"  # none | basic | token | oauth2
    username: str = ""
    password: str = ""
    token: str = ""
    oauth2: OAuth2Settings = Field(default_factory=OAuth2Settings)

    model_config = {"populate_by_name": True}

    @property
    def is_configured(self) -> bool:
        return bool(self.url)


# ------------------------------------------------------------------
# Main settings
# ------------------------------------------------------------------


class Settings(BaseModel):
    base_url: str = ""
    api_key: str = ""
    project: str = ""
    output_directory: str = Field(default="./rp-downloads")
    proxy: ProxySettings = Field(default_factory=ProxySettings)

    model_config = {"populate_by_name": True}


# ------------------------------------------------------------------
# Read helpers
# ------------------------------------------------------------------


def _read_config_file() -> dict[str, Any]:
    """Read the TOML config file, returning an empty dict if it doesn't exist."""
    if not CONFIG_FILE.exists():
        return {}
    import tomllib

    with open(CONFIG_FILE, "rb") as f:
        data = tomllib.load(f)

    result: dict[str, Any] = {}
    result.update(data.get("default", {}))

    output = data.get("output", {})
    if "directory" in output:
        result["output_directory"] = output["directory"]

    proxy_data = data.get("proxy", {})
    if proxy_data:
        result["proxy"] = proxy_data  # includes nested oauth2 table

    return result


def _read_env_vars() -> dict[str, str]:
    """Read settings from environment variables."""
    env: dict[str, str] = {}
    if val := os.environ.get(ENV_BASE_URL):
        env["base_url"] = val
    if val := os.environ.get(ENV_API_KEY):
        env["api_key"] = val
    if val := os.environ.get(ENV_PROJECT):
        env["project"] = val
    return env


def load_settings(**cli_overrides: Any) -> Settings:
    """Load settings with priority: CLI flags > env vars > config file > defaults.

    Only non-None CLI overrides are applied.
    """
    file_values = _read_config_file()
    env_values = _read_env_vars()

    merged = {**file_values, **env_values}
    for k, v in cli_overrides.items():
        if v is not None and v != "":
            merged[k] = v
    return Settings(**merged)


# ------------------------------------------------------------------
# Write helpers
# ------------------------------------------------------------------


def write_config(
    base_url: str,
    api_key: str,
    project: str,
    output_directory: str = "./rp-downloads",
    proxy: ProxySettings | None = None,
) -> Path:
    """Write config to ~/.rp-fetch/config.toml with secure permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, stat.S_IRWXU)  # 700

    data: dict[str, Any] = {
        "default": {
            "base_url": base_url,
            "api_key": api_key,
            "project": project,
        },
        "output": {
            "directory": output_directory,
        },
    }

    if proxy and proxy.is_configured:
        proxy_section: dict[str, Any] = {
            "url": proxy.url,
            "auth_type": proxy.auth_type,
        }
        if proxy.auth_type == "basic":
            proxy_section["username"] = proxy.username
            proxy_section["password"] = proxy.password
        elif proxy.auth_type == "token":
            proxy_section["token"] = proxy.token
        elif proxy.auth_type == "oauth2":
            proxy_section["oauth2"] = proxy.oauth2.model_dump()
        data["proxy"] = proxy_section

    with open(CONFIG_FILE, "wb") as f:
        tomli_w.dump(data, f)
    os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600
    return CONFIG_FILE


def config_exists() -> bool:
    return CONFIG_FILE.exists()

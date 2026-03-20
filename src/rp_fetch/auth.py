"""API key authentication helpers."""

from __future__ import annotations


def auth_headers(api_key: str) -> dict[str, str]:
    """Return HTTP headers for Bearer token authentication."""
    return {"Authorization": f"Bearer {api_key}"}

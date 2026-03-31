"""Tests for proxy authentication helpers."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from rp_fetch.proxy_auth import (
    OAuth2Error,
    OAuth2Tokens,
    _generate_pkce,
    build_proxy_headers,
    build_proxy_url_for_httpx,
    resolve_oauth2_token,
)

# ------------------------------------------------------------------
# build_proxy_url_for_httpx
# ------------------------------------------------------------------


class TestBuildProxyUrl:
    def test_basic_auth_embeds_credentials(self):
        url = build_proxy_url_for_httpx(
            "http://proxy.corp:8080",
            "basic",
            username="alice",
            password="s3cret",
        )
        assert url == "http://alice:s3cret@proxy.corp:8080"

    def test_basic_auth_url_encodes_special_chars(self):
        url = build_proxy_url_for_httpx(
            "http://proxy:3128",
            "basic",
            username="user@domain",
            password="p@ss:word",
        )
        assert "user%40domain" in url
        assert "p%40ss%3Aword" in url

    def test_basic_auth_without_password(self):
        url = build_proxy_url_for_httpx(
            "http://proxy:8080",
            "basic",
            username="alice",
        )
        assert url == "http://alice@proxy:8080"

    def test_none_auth_returns_url_unchanged(self):
        url = build_proxy_url_for_httpx("http://proxy:8080", "none")
        assert url == "http://proxy:8080"

    def test_token_auth_returns_url_unchanged(self):
        url = build_proxy_url_for_httpx("http://proxy:8080", "token")
        assert url == "http://proxy:8080"

    def test_oauth2_auth_returns_url_unchanged(self):
        url = build_proxy_url_for_httpx("http://proxy:8080", "oauth2")
        assert url == "http://proxy:8080"

    def test_basic_auth_with_https(self):
        url = build_proxy_url_for_httpx(
            "https://secure-proxy:443",
            "basic",
            username="bob",
            password="pass",
        )
        assert url == "https://bob:pass@secure-proxy:443"

    def test_basic_auth_preserves_path(self):
        url = build_proxy_url_for_httpx(
            "http://proxy:8080/some/path",
            "basic",
            username="alice",
            password="pw",
        )
        assert "/some/path" in url
        assert "alice:pw@" in url


# ------------------------------------------------------------------
# build_proxy_headers
# ------------------------------------------------------------------


class TestBuildProxyHeaders:
    def test_token_auth(self):
        headers = build_proxy_headers("token", token="my-otp-123")
        assert headers == {"Proxy-Authorization": "Bearer my-otp-123"}

    def test_oauth2_auth(self):
        headers = build_proxy_headers("oauth2", token="access-tok")
        assert headers == {"Proxy-Authorization": "Bearer access-tok"}

    def test_none_auth(self):
        assert build_proxy_headers("none") == {}

    def test_basic_auth_returns_empty(self):
        # Basic auth is handled via URL embedding, not headers
        assert build_proxy_headers("basic", token="ignored") == {}

    def test_empty_token_returns_empty(self):
        assert build_proxy_headers("token", token="") == {}

    def test_none_auth_with_token_returns_empty(self):
        assert build_proxy_headers("none", token="some-token") == {}


# ------------------------------------------------------------------
# PKCE
# ------------------------------------------------------------------


class TestPKCE:
    def test_verifier_length(self):
        verifier, challenge = _generate_pkce()
        assert 43 <= len(verifier) <= 128

    def test_challenge_is_base64url(self):
        _, challenge = _generate_pkce()
        # base64url: only A-Z, a-z, 0-9, -, _
        assert all(c.isalnum() or c in "-_" for c in challenge)

    def test_different_each_call(self):
        v1, _ = _generate_pkce()
        v2, _ = _generate_pkce()
        assert v1 != v2

    def test_challenge_is_not_empty(self):
        _, challenge = _generate_pkce()
        assert len(challenge) > 0

    def test_verifier_is_ascii(self):
        verifier, _ = _generate_pkce()
        verifier.encode("ascii")  # should not raise


# ------------------------------------------------------------------
# OAuth2Tokens
# ------------------------------------------------------------------


class TestOAuth2Tokens:
    def test_not_expired(self):
        tokens = OAuth2Tokens(
            "tok", expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        assert not tokens.is_expired

    def test_expired(self):
        tokens = OAuth2Tokens(
            "tok", expires_at=datetime.now(timezone.utc) - timedelta(hours=1)
        )
        assert tokens.is_expired

    def test_no_expiry_is_expired(self):
        tokens = OAuth2Tokens("tok")
        assert tokens.is_expired

    def test_expires_within_60s_buffer_is_expired(self):
        # Token expiring in 30s should be considered expired (60s buffer)
        tokens = OAuth2Tokens(
            "tok", expires_at=datetime.now(timezone.utc) + timedelta(seconds=30)
        )
        assert tokens.is_expired

    def test_expires_in_exactly_61s_is_not_expired(self):
        tokens = OAuth2Tokens(
            "tok", expires_at=datetime.now(timezone.utc) + timedelta(seconds=61)
        )
        assert not tokens.is_expired

    def test_stores_refresh_token(self):
        tokens = OAuth2Tokens("access", refresh_token="refresh-val")
        assert tokens.refresh_token == "refresh-val"

    def test_stores_access_token(self):
        tokens = OAuth2Tokens("my-access-token")
        assert tokens.access_token == "my-access-token"


# ------------------------------------------------------------------
# resolve_oauth2_token — reuse path
# ------------------------------------------------------------------


class TestResolveOAuth2Token:
    def test_reuses_valid_token(self):
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        tokens = resolve_oauth2_token(
            authorize_url="https://unused",
            token_url="https://unused",
            client_id="unused",
            current_access_token="still-valid",
            current_refresh_token="refresh",
            token_expiry=expiry,
        )
        assert tokens.access_token == "still-valid"

    def test_reuses_valid_token_preserves_refresh(self):
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        tokens = resolve_oauth2_token(
            authorize_url="https://unused",
            token_url="https://unused",
            client_id="unused",
            current_access_token="still-valid",
            current_refresh_token="my-refresh",
            token_expiry=expiry,
        )
        assert tokens.refresh_token == "my-refresh"

    @patch("rp_fetch.proxy_auth.webbrowser.open", return_value=False)
    def test_expired_token_without_refresh_needs_browser(self, mock_open):
        # With an expired token and no refresh token, it should attempt
        # the full browser flow. We mock webbrowser.open to return False
        # so it raises OAuth2Error instead of actually opening a browser.
        expiry = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with pytest.raises(OAuth2Error, match="Could not open browser"):
            resolve_oauth2_token(
                authorize_url="https://nonexistent-idp.localhost/authorize",
                token_url="https://nonexistent-idp.localhost/token",
                client_id="test",
                current_access_token="expired-tok",
                current_refresh_token="",
                token_expiry=expiry,
            )
        mock_open.assert_called_once()

    @patch("rp_fetch.proxy_auth.webbrowser.open", return_value=False)
    def test_invalid_expiry_string_does_not_reuse(self, mock_open):
        # A malformed expiry should not cause a crash — it falls through
        # to refresh or browser flow (which we mock).
        with pytest.raises(OAuth2Error, match="Could not open browser"):
            resolve_oauth2_token(
                authorize_url="https://nonexistent-idp.localhost/authorize",
                token_url="https://nonexistent-idp.localhost/token",
                client_id="test",
                current_access_token="tok",
                current_refresh_token="",
                token_expiry="not-a-date",
            )
        mock_open.assert_called_once()

"""Proxy authentication helpers: Basic, Token/OTP, and OAuth2 browser flow."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

# Ports to try for the OAuth2 local callback server
CALLBACK_PORTS = [8789, 8790, 8791, 8792, 8793]
CALLBACK_PATH = "/callback"
OAUTH2_TIMEOUT = 120  # seconds to wait for browser callback


class ProxyAuthError(Exception):
    """Raised when proxy authentication fails."""


class OAuth2Error(ProxyAuthError):
    """Raised when an OAuth2 flow fails."""


# ------------------------------------------------------------------
# OAuth2 token container
# ------------------------------------------------------------------


class OAuth2Tokens:
    """Holds the result of an OAuth2 token exchange."""

    def __init__(
        self,
        access_token: str,
        refresh_token: str = "",
        expires_at: datetime | None = None,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return True
        return datetime.now(timezone.utc) >= self.expires_at - timedelta(seconds=60)


# ------------------------------------------------------------------
# Proxy URL / header builders (used by RPClient)
# ------------------------------------------------------------------


def build_proxy_url_for_httpx(
    url: str,
    auth_type: str,
    username: str = "",
    password: str = "",
) -> str:
    """Return the proxy URL, embedding Basic credentials when applicable.

    For ``basic`` auth, httpx handles ``Proxy-Authorization`` automatically
    when credentials are embedded in the URL (``http://user:pass@host:port``).
    """
    if auth_type == "basic" and username:
        parsed = urllib.parse.urlparse(url)
        userinfo = urllib.parse.quote(username, safe="")
        if password:
            userinfo += ":" + urllib.parse.quote(password, safe="")
        host = parsed.hostname or "localhost"
        port = parsed.port or 8080
        return f"{parsed.scheme}://{userinfo}@{host}:{port}{parsed.path}"
    return url


def build_proxy_headers(auth_type: str, token: str = "") -> dict[str, str]:
    """Return extra headers needed for token / oauth2 proxy auth."""
    if auth_type in ("token", "oauth2") and token:
        return {"Proxy-Authorization": f"Bearer {token}"}
    return {}


# ------------------------------------------------------------------
# OAuth2 Authorization Code Flow with PKCE
# ------------------------------------------------------------------


class _CallbackHandler(BaseHTTPRequestHandler):
    """Tiny HTTP handler that captures the OAuth2 redirect callback."""

    authorization_code: str | None = None
    received_state: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        if "error" in params:
            _CallbackHandler.error = params["error"][0]
            desc = params.get("error_description", ["Unknown error"])[0]
            body = (
                "<html><body style='font-family:sans-serif;text-align:center;"
                "padding:40px'><h2 style='color:#c0392b'>Authentication "
                f"Failed</h2><p>{desc}</p><p style='color:#888'>You can close "
                "this tab.</p></body></html>"
            )
        elif "code" in params:
            _CallbackHandler.authorization_code = params["code"][0]
            _CallbackHandler.received_state = params.get("state", [None])[0]
            body = (
                "<html><body style='font-family:sans-serif;text-align:center;"
                "padding:40px'><h2 style='color:#27ae60'>Authentication "
                "Successful!</h2><p style='color:#888'>You can close this tab "
                "and return to the terminal.</p></body></html>"
            )
        else:
            body = (
                "<html><body style='font-family:sans-serif;text-align:center;"
                "padding:40px'><h2>Unexpected Response</h2></body></html>"
            )

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # suppress noisy server logs


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE ``code_verifier`` and ``code_challenge`` (S256)."""
    code_verifier = secrets.token_urlsafe(96)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _start_callback_server() -> tuple[HTTPServer, int]:
    for port in CALLBACK_PORTS:
        try:
            server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
            server.timeout = 5.0
            return server, port
        except OSError:
            continue
    raise OAuth2Error(
        f"Could not start callback server — ports "
        f"{CALLBACK_PORTS[0]}–{CALLBACK_PORTS[-1]} are all in use."
    )


def _post_token_request(token_url: str, data: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("ascii")
    req = urllib.request.Request(
        token_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode(errors="replace")
        raise OAuth2Error(
            f"Token exchange failed (HTTP {exc.code}): {error_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise OAuth2Error(f"Could not reach token endpoint: {exc.reason}") from exc


def run_oauth2_flow(
    authorize_url: str,
    token_url: str,
    client_id: str,
    client_secret: str = "",
    scopes: str = "openid",
) -> OAuth2Tokens:
    """Run the full OAuth2 Authorization Code + PKCE flow via the browser."""
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = _generate_pkce()

    server, port = _start_callback_server()
    redirect_uri = f"http://localhost:{port}{CALLBACK_PATH}"

    # Reset shared handler state
    _CallbackHandler.authorization_code = None
    _CallbackHandler.received_state = None
    _CallbackHandler.error = None

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{authorize_url}?{urllib.parse.urlencode(params)}"

    if not webbrowser.open(auth_url):
        server.server_close()
        raise OAuth2Error(
            "Could not open browser automatically.\n"
            f"Please open this URL manually:\n{auth_url}"
        )

    start = time.time()
    while time.time() - start < OAUTH2_TIMEOUT:
        server.handle_request()
        if _CallbackHandler.authorization_code or _CallbackHandler.error:
            break
    server.server_close()

    if _CallbackHandler.error:
        raise OAuth2Error(f"Authorization denied: {_CallbackHandler.error}")
    if not _CallbackHandler.authorization_code:
        raise OAuth2Error(f"Timed out after {OAUTH2_TIMEOUT}s — no callback received.")
    if _CallbackHandler.received_state != state:
        raise OAuth2Error("State mismatch — possible CSRF attack. Aborting.")

    token_data = {
        "grant_type": "authorization_code",
        "code": _CallbackHandler.authorization_code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        token_data["client_secret"] = client_secret

    result = _post_token_request(token_url, token_data)
    expires_at = None
    if "expires_in" in result:
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=int(result["expires_in"])
        )

    return OAuth2Tokens(
        access_token=result.get("access_token", ""),
        refresh_token=result.get("refresh_token", ""),
        expires_at=expires_at,
    )


def refresh_oauth2_token(
    token_url: str,
    client_id: str,
    refresh_token: str,
    client_secret: str = "",
) -> OAuth2Tokens:
    """Silently refresh an access token using a refresh token."""
    data: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret
    result = _post_token_request(token_url, data)
    expires_at = None
    if "expires_in" in result:
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=int(result["expires_in"])
        )
    return OAuth2Tokens(
        access_token=result.get("access_token", ""),
        refresh_token=result.get("refresh_token", refresh_token),
        expires_at=expires_at,
    )


def resolve_oauth2_token(
    authorize_url: str,
    token_url: str,
    client_id: str,
    client_secret: str = "",
    scopes: str = "openid",
    current_access_token: str = "",
    current_refresh_token: str = "",
    token_expiry: str = "",
) -> OAuth2Tokens:
    """Get a valid access token: reuse → silent refresh → full browser flow."""
    # 1) Current token still valid?
    if current_access_token and token_expiry:
        try:
            expiry = datetime.fromisoformat(token_expiry)
            if datetime.now(timezone.utc) < expiry - timedelta(seconds=60):
                return OAuth2Tokens(current_access_token, current_refresh_token, expiry)
        except ValueError:
            pass

    # 2) Try silent refresh
    if current_refresh_token:
        try:
            return refresh_oauth2_token(
                token_url, client_id, current_refresh_token, client_secret
            )
        except OAuth2Error:
            pass  # fall through to browser

    # 3) Full browser flow
    return run_oauth2_flow(authorize_url, token_url, client_id, client_secret, scopes)

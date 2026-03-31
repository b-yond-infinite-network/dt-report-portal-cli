"""CLI entry point using Typer."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from rp_fetch import __version__
from rp_fetch.client import (
    RPAuthError,
    RPClient,
    RPClientError,
    RPNotFoundError,
    RPProxyAuthError,
)
from rp_fetch.config import (
    OAuth2Settings,
    ProxySettings,
    config_exists,
    load_settings,
    write_config,
)
from rp_fetch.downloader import download_launch
from rp_fetch.proxy_auth import (
    OAuth2Error,
    build_proxy_headers,
    build_proxy_url_for_httpx,
    resolve_oauth2_token,
    run_oauth2_flow,
)
from rp_fetch.search import display_launches_table, search_and_select

console = Console()

app = typer.Typer(
    name="rp-fetch",
    help="Bulk-download ReportPortal launch content (logs, attachments, screenshots).",
    no_args_is_help=True,
)

# -- Sub-apps --
config_app = typer.Typer(help="Manage rp-fetch configuration.", no_args_is_help=True)
launch_app = typer.Typer(help="List and search launches.", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(launch_app, name="launch")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"rp-fetch {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", "-V", callback=_version_callback, is_eager=True),
    ] = None,
) -> None:
    """rp-fetch: ReportPortal launch downloader."""


# ------------------------------------------------------------------
# Proxy helpers
# ------------------------------------------------------------------


def _resolve_proxy_settings(settings):
    """Ensure OAuth2 tokens are fresh; re-prompt for expired token auth.

    Returns the (possibly updated) Settings object, and saves new tokens
    to disk when they change.
    """
    proxy = settings.proxy
    if not proxy.is_configured or proxy.auth_type == "none":
        return settings

    if proxy.auth_type == "token" and not proxy.token:
        console.print("[yellow]Proxy token is empty. Please provide one.[/yellow]")
        proxy.token = typer.prompt("Proxy token", hide_input=True)
        _save_settings(settings)

    if proxy.auth_type == "oauth2":
        oauth2 = proxy.oauth2
        console.print("[dim]Checking OAuth2 proxy token...[/dim]")
        try:
            tokens = resolve_oauth2_token(
                authorize_url=oauth2.authorize_url,
                token_url=oauth2.token_url,
                client_id=oauth2.client_id,
                client_secret=oauth2.client_secret,
                scopes=oauth2.scopes,
                current_access_token=oauth2.access_token,
                current_refresh_token=oauth2.refresh_token,
                token_expiry=oauth2.token_expiry,
            )
        except OAuth2Error as exc:
            console.print(f"[red]OAuth2 error:[/red] {exc}")
            raise typer.Exit(1)

        oauth2.access_token = tokens.access_token
        if tokens.refresh_token:
            oauth2.refresh_token = tokens.refresh_token
        oauth2.token_expiry = tokens.expires_at.isoformat() if tokens.expires_at else ""
        _save_settings(settings)
        console.print("[dim]OAuth2 token is valid.[/dim]")

    return settings


def _build_client_from_settings(settings) -> RPClient:
    """Create an RPClient with fully resolved proxy configuration."""
    proxy = settings.proxy
    proxy_url: str | None = None
    proxy_headers: dict[str, str] = {}

    if proxy.is_configured:
        proxy_url = build_proxy_url_for_httpx(
            proxy.url,
            proxy.auth_type,
            proxy.username,
            proxy.password,
        )
        active_token = ""
        if proxy.auth_type == "token":
            active_token = proxy.token
        elif proxy.auth_type == "oauth2":
            active_token = proxy.oauth2.access_token
        proxy_headers = build_proxy_headers(proxy.auth_type, token=active_token)

    return RPClient(
        settings.base_url,
        settings.api_key,
        settings.project,
        proxy_url=proxy_url,
        proxy_headers=proxy_headers,
    )


def _save_settings(settings) -> None:
    """Persist the current Settings back to the config file."""
    write_config(
        base_url=settings.base_url,
        api_key=settings.api_key,
        project=settings.project,
        output_directory=settings.output_directory,
        proxy=settings.proxy,
    )


def _get_client(
    base_url: str | None = None,
    api_key: str | None = None,
    project: str | None = None,
) -> RPClient:
    """Load settings, resolve proxy auth, and return an RPClient."""
    settings = load_settings(
        base_url=base_url or "", api_key=api_key or "", project=project or ""
    )
    if not settings.base_url:
        console.print(
            "[red]Error:[/red] No base_url configured. Run: rp-fetch config init"
        )
        raise typer.Exit(1)
    if not settings.api_key:
        console.print(
            "[red]Error:[/red] No api_key configured. Run: rp-fetch config init"
        )
        raise typer.Exit(1)
    if not settings.project:
        console.print(
            "[red]Error:[/red] No project configured. Run: rp-fetch config init"
        )
        raise typer.Exit(1)

    settings = _resolve_proxy_settings(settings)
    return _build_client_from_settings(settings)


# ------------------------------------------------------------------
# Proxy re-prompt on 407 during operations
# ------------------------------------------------------------------


def _reprompt_proxy_credentials(exc: RPProxyAuthError) -> RPClient:
    """Prompt the user for fresh proxy credentials, save, and return a new client.

    Called when a command gets a 407 at runtime.  Re-prompts based on the
    configured auth type, persists the new credentials, and returns a
    freshly-built RPClient ready for a retry.
    """
    console.print(f"\n[yellow]Proxy authentication failed:[/yellow] {exc}")

    settings = load_settings()
    proxy = settings.proxy

    if proxy.auth_type == "basic":
        console.print("Please re-enter your proxy credentials.")
        proxy.username = typer.prompt("Proxy username", default=proxy.username)
        proxy.password = typer.prompt("Proxy password", hide_input=True)

    elif proxy.auth_type == "token":
        console.print("Please provide a new proxy token.")
        proxy.token = typer.prompt("Proxy token", hide_input=True)

    elif proxy.auth_type == "oauth2":
        console.print("Re-authenticating via OAuth2...")
        # Clear cached tokens so _resolve_proxy_settings triggers a full flow
        proxy.oauth2.access_token = ""
        proxy.oauth2.refresh_token = ""
        proxy.oauth2.token_expiry = ""

    else:
        console.print(
            "[red]Proxy rejected the request but auth type is 'none'.[/red]\n"
            "Update your proxy config: [dim]rp-fetch config init[/dim]"
        )
        raise typer.Exit(1)

    _save_settings(settings)
    # Resolve again (handles the OAuth2 browser flow if needed)
    settings = _resolve_proxy_settings(settings)
    return _build_client_from_settings(settings)


# ======================================================================
# config commands
# ======================================================================


def _prompt_proxy() -> ProxySettings:
    """Interactive proxy configuration prompts. Returns ProxySettings."""
    use_proxy = typer.confirm("Use a proxy?", default=False)
    if not use_proxy:
        return ProxySettings()

    url = typer.prompt("Proxy URL (e.g. http://proxy.corp:8080)")
    auth_type = (
        typer.prompt(
            "Proxy auth type (none/basic/token/oauth2)",
            default="none",
        )
        .lower()
        .strip()
    )

    if auth_type not in ("none", "basic", "token", "oauth2"):
        console.print(
            f"[red]Unknown auth type '{auth_type}', defaulting to 'none'.[/red]"
        )
        auth_type = "none"

    proxy = ProxySettings(url=url, auth_type=auth_type)

    if auth_type == "basic":
        proxy.username = typer.prompt("Proxy username")
        proxy.password = typer.prompt("Proxy password", hide_input=True)

    elif auth_type == "token":
        proxy.token = typer.prompt("Proxy token", hide_input=True)

    elif auth_type == "oauth2":
        oauth2 = OAuth2Settings()
        oauth2.authorize_url = typer.prompt("OAuth2 Authorize URL")
        oauth2.token_url = typer.prompt("OAuth2 Token URL")
        oauth2.client_id = typer.prompt("Client ID")
        oauth2.client_secret = typer.prompt(
            "Client Secret (leave empty for public client)", default=""
        )
        oauth2.scopes = typer.prompt("Scopes", default="openid")
        proxy.oauth2 = oauth2

        console.print("\n[bold]Opening browser for authentication...[/bold]")
        try:
            tokens = run_oauth2_flow(
                oauth2.authorize_url,
                oauth2.token_url,
                oauth2.client_id,
                oauth2.client_secret,
                oauth2.scopes,
            )
            oauth2.access_token = tokens.access_token
            oauth2.refresh_token = tokens.refresh_token
            oauth2.token_expiry = (
                tokens.expires_at.isoformat() if tokens.expires_at else ""
            )
            expires_in_msg = ""
            if tokens.expires_at:
                secs = int(
                    (tokens.expires_at - datetime.now(timezone.utc)).total_seconds()
                )
                expires_in_msg = f" Token expires in {secs}s."
            console.print(
                f"[green]Browser authentication successful!{expires_in_msg}[/green]"
            )
        except OAuth2Error as exc:
            console.print(f"[red]OAuth2 flow failed:[/red] {exc}")
            console.print(
                "[yellow]Proxy config saved without tokens. "
                "Run 'config test' to retry.[/yellow]"
            )

    return proxy


@config_app.command("init")
def config_init() -> None:
    """Interactive first-time setup: prompts for base_url, api_key, project, proxy."""
    console.print("[bold]rp-fetch configuration setup[/bold]\n")

    if config_exists():
        overwrite = typer.confirm(
            "Config file already exists. Overwrite?", default=False
        )
        if not overwrite:
            raise typer.Exit()

    base_url = typer.prompt(
        "ReportPortal base URL (e.g. https://reportportal.example.com)"
    )
    api_key = typer.prompt("API key (Bearer token)", hide_input=True)
    project = typer.prompt("Project name (slug)")
    output_dir = typer.prompt("Default output directory", default="./rp-downloads")

    proxy = _prompt_proxy()

    path = write_config(base_url, api_key, project, output_dir, proxy=proxy)
    console.print(f"\n[green]Config written to {path}[/green]")

    # Connection test
    console.print("\nTesting connection...")
    if proxy.is_configured:
        console.print(f"[dim]Using proxy: {proxy.url} (auth: {proxy.auth_type})[/dim]")

    proxy_url: str | None = None
    proxy_headers: dict[str, str] = {}
    if proxy.is_configured:
        proxy_url = build_proxy_url_for_httpx(
            proxy.url,
            proxy.auth_type,
            proxy.username,
            proxy.password,
        )
        active_token = ""
        if proxy.auth_type == "token":
            active_token = proxy.token
        elif proxy.auth_type == "oauth2":
            active_token = proxy.oauth2.access_token
        proxy_headers = build_proxy_headers(proxy.auth_type, token=active_token)

    client = RPClient(
        base_url,
        api_key,
        project,
        proxy_url=proxy_url,
        proxy_headers=proxy_headers,
    )
    try:
        asyncio.run(_test_connection(client))
        console.print("[green]Connection successful![/green]")
    except RPProxyAuthError as e:
        console.print(f"[red]Proxy authentication failed:[/red] {e}")
    except RPAuthError as e:
        console.print(f"[red]Authentication failed:[/red] {e}")
    except RPClientError as e:
        console.print(f"[red]Connection failed:[/red] {e}")
    except Exception as e:
        console.print(f"[red]Connection failed:[/red] {e}")


@config_app.command("show")
def config_show() -> None:
    """Print the current resolved configuration (sensitive values masked)."""
    settings = load_settings()
    masked_key = (
        settings.api_key[:4] + "****" + settings.api_key[-4:]
        if len(settings.api_key) >= 8
        else "****"
    )
    console.print(f"  base_url:         {settings.base_url or '[dim]not set[/dim]'}")
    console.print(f"  api_key:          {masked_key}")
    console.print(f"  project:          {settings.project or '[dim]not set[/dim]'}")
    console.print(f"  output_directory: {settings.output_directory}")

    proxy = settings.proxy
    if proxy.is_configured:
        console.print(f"  proxy_url:        {proxy.url}")
        console.print(f"  proxy_auth:       {proxy.auth_type}", end="")
        if proxy.auth_type == "basic":
            console.print(f" (user: {proxy.username})")
            console.print(
                f"  proxy_password:   "
                f"{'****' if proxy.password else '[dim]not set[/dim]'}"
            )
        elif proxy.auth_type == "token":
            console.print()
            masked = proxy.token[:4] + "****" if len(proxy.token) >= 4 else "****"
            console.print(f"  proxy_token:      {masked}")
        elif proxy.auth_type == "oauth2":
            console.print()
            console.print(f"  oauth2_client_id: {proxy.oauth2.client_id}")
            has_refresh = "yes" if proxy.oauth2.refresh_token else "no"
            console.print(f"  oauth2_refresh:   {has_refresh}")
            console.print(
                f"  oauth2_expiry:    {proxy.oauth2.token_expiry or '[dim]none[/dim]'}"
            )
        else:
            console.print()
    else:
        console.print("  proxy:            [dim]not configured[/dim]")


@config_app.command("test")
def config_test() -> None:
    """Test proxy (if configured) and ReportPortal connectivity."""
    settings = load_settings()
    if not settings.base_url or not settings.api_key or not settings.project:
        console.print(
            "[red]Error:[/red] Incomplete configuration. Run: rp-fetch config init"
        )
        raise typer.Exit(1)

    proxy = settings.proxy
    if proxy.is_configured:
        console.print(f"Proxy configured: {proxy.url} (auth: {proxy.auth_type})")

    # Resolve proxy auth (may refresh OAuth2 tokens or prompt for token)
    try:
        settings = _resolve_proxy_settings(settings)
    except typer.Exit:
        raise

    client = _build_client_from_settings(settings)
    console.print("Testing connection...")
    try:
        asyncio.run(_test_connection(client))
        console.print("[green]Connection successful![/green]")
    except RPProxyAuthError as e:
        client = _reprompt_proxy_credentials(e)
        console.print("Retrying connection...")
        try:
            asyncio.run(_test_connection(client))
            console.print("[green]Connection successful![/green]")
        except RPProxyAuthError as e2:
            console.print(f"[red]Proxy authentication failed again:[/red] {e2}")
            raise typer.Exit(1)
    except RPAuthError as e:
        console.print(f"[red]Authentication failed:[/red] {e}")
        raise typer.Exit(1)
    except RPClientError as e:
        console.print(f"[red]Connection failed:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help="Config key to set")],
    value: Annotated[str, typer.Argument(help="Config value")],
) -> None:
    """Set a single configuration value."""
    base_keys = {"base_url", "api_key", "project", "output_directory"}
    proxy_keys = {
        "proxy_url",
        "proxy_auth_type",
        "proxy_username",
        "proxy_password",
        "proxy_token",
    }
    all_keys = base_keys | proxy_keys
    if key not in all_keys:
        console.print(
            f"[red]Invalid key:[/red] {key}. Valid: {', '.join(sorted(all_keys))}"
        )
        raise typer.Exit(1)

    settings = load_settings()

    if key in base_keys:
        setattr(settings, key, value)
    else:
        # Map proxy_<field> -> settings.proxy.<field>
        proxy_field = key[6:]  # strip "proxy_"
        setattr(settings.proxy, proxy_field, value)

    _save_settings(settings)
    console.print(f"[green]Set {key}[/green]")


async def _test_connection(client: RPClient) -> None:
    async with client:
        await client.test_connection()


# ======================================================================
# launch commands
# ======================================================================


@launch_app.command("list")
def launch_list(
    limit: Annotated[int, typer.Option(help="Number of launches to show")] = 20,
    name: Annotated[
        Optional[str], typer.Option(help="Filter by launch name (substring)")
    ] = None,
    status: Annotated[
        Optional[str],
        typer.Option(help="Filter by status: passed|failed|stopped|interrupted"),
    ] = None,
    from_date: Annotated[
        Optional[str],
        typer.Option("--from", help="Filter launches after date (YYYY-MM-DD)"),
    ] = None,
    to_date: Annotated[
        Optional[str],
        typer.Option("--to", help="Filter launches before date (YYYY-MM-DD)"),
    ] = None,
    attr: Annotated[
        Optional[list[str]], typer.Option(help="Filter by attribute KEY:VALUE")
    ] = None,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output raw JSON")
    ] = False,
    project: Annotated[
        Optional[str], typer.Option(help="Override project name")
    ] = None,
) -> None:
    """List recent launches for the configured project."""
    client = _get_client(project=project)
    from_d = date.fromisoformat(from_date) if from_date else None
    to_d = date.fromisoformat(to_date) if to_date else None

    async def _run() -> None:
        async with client:
            launches, page_info = await client.list_launches(
                limit=limit,
                name=name,
                status=status,
                from_date=from_d,
                to_date=to_d,
                attributes=attr,
            )
            if not launches:
                console.print("[yellow]No launches found.[/yellow]")
                return
            if output_json:
                data = [l.model_dump(mode="json", by_alias=True) for l in launches]
                console.print_json(json.dumps(data, default=str))
            else:
                display_launches_table(launches)
                console.print(
                    f"\nShowing {len(launches)} of "
                    f"{page_info.total_elements} total launches."
                )

    try:
        asyncio.run(_run())
    except RPProxyAuthError as e:
        client = _reprompt_proxy_credentials(e)
        try:
            asyncio.run(_run())
        except RPProxyAuthError as e2:
            console.print(f"[red]Proxy authentication failed again:[/red] {e2}")
            raise typer.Exit(1)
    except RPAuthError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except RPClientError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@launch_app.command("search")
def launch_search(
    name: Annotated[Optional[str], typer.Option(help="Pre-fill search term")] = None,
    status: Annotated[Optional[str], typer.Option(help="Pre-filter by status")] = None,
    from_date: Annotated[
        Optional[str],
        typer.Option("--from", help="Pre-filter by start date (YYYY-MM-DD)"),
    ] = None,
    project: Annotated[
        Optional[str], typer.Option(help="Override project name")
    ] = None,
) -> None:
    """Interactive launch search and selection."""
    client = _get_client(project=project)
    from_d = date.fromisoformat(from_date) if from_date else None

    async def _run() -> None:
        async with client:
            selected = await search_and_select(
                client,
                name=name,
                status=status,
                from_date=from_d,
            )
            if selected:
                console.print(f"\nLaunch UUID: [bold cyan]{selected.uuid}[/bold cyan]")
                console.print(f"Use: [dim]rp-fetch download {selected.uuid}[/dim]")

    try:
        asyncio.run(_run())
    except RPProxyAuthError as e:
        client = _reprompt_proxy_credentials(e)
        try:
            asyncio.run(_run())
        except RPProxyAuthError as e2:
            console.print(f"[red]Proxy authentication failed again:[/red] {e2}")
            raise typer.Exit(1)
    except RPAuthError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except RPClientError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ======================================================================
# download command
# ======================================================================


@app.command("download")
def download(
    launch_id: Annotated[str, typer.Argument(help="UUID of the launch to download")],
    out: Annotated[Optional[str], typer.Option(help="Output directory")] = None,
    include: Annotated[
        Optional[list[str]],
        typer.Option(help="Content types: logs|attachments|screenshots|all"),
    ] = None,
    level: Annotated[
        Optional[str],
        typer.Option(help="Minimum log level: error|warn|info|debug|trace"),
    ] = None,
    parallel: Annotated[int, typer.Option(help="Parallel attachment downloads")] = 4,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview without writing files")
    ] = False,
    flat: Annotated[
        bool, typer.Option(help="Flatten output into a single directory")
    ] = False,
    project: Annotated[
        Optional[str], typer.Option(help="Override project name")
    ] = None,
) -> None:
    """Download all content for a given launch."""
    if parallel < 1 or parallel > 16:
        console.print("[red]Error:[/red] --parallel must be between 1 and 16")
        raise typer.Exit(1)

    settings = load_settings(project=project or "")
    client = _get_client(project=project)
    output_dir = Path(out) if out else Path(settings.output_directory)

    async def _run() -> None:
        async with client:
            await download_launch(
                client,
                launch_id,
                output_dir=output_dir,
                include=include or ["all"],
                min_level=level,
                parallel=parallel,
                dry_run=dry_run,
                flat=flat,
            )

    try:
        asyncio.run(_run())
    except RPProxyAuthError as e:
        client = _reprompt_proxy_credentials(e)
        try:
            asyncio.run(_run())
        except RPProxyAuthError as e2:
            console.print(f"[red]Proxy authentication failed again:[/red] {e2}")
            raise typer.Exit(1)
    except RPAuthError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except RPNotFoundError:
        console.print(f"[red]Launch not found:[/red] {launch_id}")
        raise typer.Exit(1)
    except RPClientError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except OSError as e:
        if "No space left on device" in str(e):
            console.print(
                "[red]Disk full![/red] Download aborted. "
                "Manifest preserved with progress checkpoint."
            )
        else:
            console.print(f"[red]File system error:[/red] {e}")
        raise typer.Exit(1)


# ======================================================================
# search-and-download command
# ======================================================================


@app.command("search-and-download")
def search_and_download_cmd(
    name: Annotated[Optional[str], typer.Option(help="Pre-fill search term")] = None,
    status: Annotated[Optional[str], typer.Option(help="Pre-filter by status")] = None,
    from_date: Annotated[
        Optional[str],
        typer.Option("--from", help="Pre-filter by start date (YYYY-MM-DD)"),
    ] = None,
    out: Annotated[Optional[str], typer.Option(help="Output directory")] = None,
    include: Annotated[
        Optional[list[str]],
        typer.Option(help="Content types: logs|attachments|screenshots|all"),
    ] = None,
    level: Annotated[Optional[str], typer.Option(help="Minimum log level")] = None,
    parallel: Annotated[int, typer.Option(help="Parallel attachment downloads")] = 4,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview without writing files")
    ] = False,
    flat: Annotated[bool, typer.Option(help="Flatten output")] = False,
    project: Annotated[
        Optional[str], typer.Option(help="Override project name")
    ] = None,
) -> None:
    """Search for a launch interactively, then download it."""
    if parallel < 1 or parallel > 16:
        console.print("[red]Error:[/red] --parallel must be between 1 and 16")
        raise typer.Exit(1)

    settings = load_settings(project=project or "")
    client = _get_client(project=project)
    output_dir = Path(out) if out else Path(settings.output_directory)
    from_d = date.fromisoformat(from_date) if from_date else None

    async def _run() -> None:
        async with client:
            selected = await search_and_select(
                client,
                name=name,
                status=status,
                from_date=from_d,
            )
            if not selected:
                return

            confirm = typer.confirm(
                f"\nDownload launch '{selected.name}'?", default=True
            )
            if not confirm:
                console.print("[dim]Cancelled.[/dim]")
                return

            await download_launch(
                client,
                selected.uuid,
                output_dir=output_dir,
                include=include or ["all"],
                min_level=level,
                parallel=parallel,
                dry_run=dry_run,
                flat=flat,
            )

    try:
        asyncio.run(_run())
    except RPProxyAuthError as e:
        client = _reprompt_proxy_credentials(e)
        try:
            asyncio.run(_run())
        except RPProxyAuthError as e2:
            console.print(f"[red]Proxy authentication failed again:[/red] {e2}")
            raise typer.Exit(1)
    except RPAuthError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except RPClientError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ======================================================================
# PyInstaller / standalone entry point
# ======================================================================

if __name__ == "__main__":
    app()

"""CLI entry point using Typer."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from rp_fetch import __version__
from rp_fetch.client import RPAuthError, RPClient, RPClientError, RPNotFoundError
from rp_fetch.config import config_exists, load_settings, write_config
from rp_fetch.downloader import download_launch
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


def _get_client(
    base_url: str | None = None,
    api_key: str | None = None,
    project: str | None = None,
) -> RPClient:
    """Build an RPClient from resolved settings."""
    settings = load_settings(base_url=base_url or "", api_key=api_key or "", project=project or "")
    if not settings.base_url:
        console.print("[red]Error:[/red] No base_url configured. Run: rp-fetch config init")
        raise typer.Exit(1)
    if not settings.api_key:
        console.print("[red]Error:[/red] No api_key configured. Run: rp-fetch config init")
        raise typer.Exit(1)
    if not settings.project:
        console.print("[red]Error:[/red] No project configured. Run: rp-fetch config init")
        raise typer.Exit(1)
    return RPClient(settings.base_url, settings.api_key, settings.project)


# ======================================================================
# config commands
# ======================================================================


@config_app.command("init")
def config_init() -> None:
    """Interactive first-time setup: prompts for base_url, api_key, project."""
    console.print("[bold]rp-fetch configuration setup[/bold]\n")

    if config_exists():
        overwrite = typer.confirm("Config file already exists. Overwrite?", default=False)
        if not overwrite:
            raise typer.Exit()

    base_url = typer.prompt("ReportPortal base URL (e.g. https://reportportal.example.com)")
    api_key = typer.prompt("API key (Bearer token)", hide_input=True)
    project = typer.prompt("Project name (slug)")
    output_dir = typer.prompt("Default output directory", default="./rp-downloads")

    path = write_config(base_url, api_key, project, output_dir)
    console.print(f"\n[green]Config written to {path}[/green]")

    # Connection test
    console.print("\nTesting connection...")
    client = RPClient(base_url, api_key, project)
    try:
        asyncio.run(_test_connection(client))
        console.print("[green]Connection successful![/green]")
    except RPAuthError as e:
        console.print(f"[red]Authentication failed:[/red] {e}")
    except RPClientError as e:
        console.print(f"[red]Connection failed:[/red] {e}")
    except Exception as e:
        console.print(f"[red]Connection failed:[/red] {e}")


@config_app.command("show")
def config_show() -> None:
    """Print the current resolved configuration (API key masked)."""
    settings = load_settings()
    masked_key = settings.api_key[:4] + "****" + settings.api_key[-4:] if len(settings.api_key) >= 8 else "****"
    console.print(f"  base_url:         {settings.base_url or '[dim]not set[/dim]'}")
    console.print(f"  api_key:          {masked_key}")
    console.print(f"  project:          {settings.project or '[dim]not set[/dim]'}")
    console.print(f"  output_directory: {settings.output_directory}")


@config_app.command("test")
def config_test() -> None:
    """Test connection and authentication against the configured instance."""
    client = _get_client()
    console.print("Testing connection...")
    try:
        asyncio.run(_test_connection(client))
        console.print("[green]Connection successful![/green]")
    except RPAuthError as e:
        console.print(f"[red]Authentication failed:[/red] {e}")
        raise typer.Exit(1)
    except RPClientError as e:
        console.print(f"[red]Connection failed:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help="Config key (base_url, api_key, project, output_directory)")],
    value: Annotated[str, typer.Argument(help="Config value to set")],
) -> None:
    """Set a single configuration value."""
    valid_keys = {"base_url", "api_key", "project", "output_directory"}
    if key not in valid_keys:
        console.print(f"[red]Invalid key:[/red] {key}. Valid keys: {', '.join(sorted(valid_keys))}")
        raise typer.Exit(1)
    settings = load_settings()
    setattr(settings, key, value)
    write_config(
        base_url=settings.base_url,
        api_key=settings.api_key,
        project=settings.project,
        output_directory=settings.output_directory,
    )
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
    name: Annotated[Optional[str], typer.Option(help="Filter by launch name (substring)")] = None,
    status: Annotated[Optional[str], typer.Option(help="Filter by status: passed|failed|stopped|interrupted")] = None,
    from_date: Annotated[Optional[str], typer.Option("--from", help="Filter launches after date (YYYY-MM-DD)")] = None,
    to_date: Annotated[Optional[str], typer.Option("--to", help="Filter launches before date (YYYY-MM-DD)")] = None,
    attr: Annotated[Optional[list[str]], typer.Option(help="Filter by attribute KEY:VALUE")] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Output raw JSON")] = False,
    project: Annotated[Optional[str], typer.Option(help="Override project name")] = None,
) -> None:
    """List recent launches for the configured project."""
    client = _get_client(project=project)
    from_d = date.fromisoformat(from_date) if from_date else None
    to_d = date.fromisoformat(to_date) if to_date else None

    async def _run() -> None:
        async with client:
            launches, page_info = await client.list_launches(
                limit=limit, name=name, status=status,
                from_date=from_d, to_date=to_d, attributes=attr,
            )
            if not launches:
                console.print("[yellow]No launches found.[/yellow]")
                return
            if output_json:
                data = [l.model_dump(mode="json", by_alias=True) for l in launches]
                console.print_json(json.dumps(data, default=str))
            else:
                display_launches_table(launches)
                console.print(f"\nShowing {len(launches)} of {page_info.total_elements} total launches.")

    try:
        asyncio.run(_run())
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
    from_date: Annotated[Optional[str], typer.Option("--from", help="Pre-filter by start date (YYYY-MM-DD)")] = None,
    project: Annotated[Optional[str], typer.Option(help="Override project name")] = None,
) -> None:
    """Interactive launch search and selection."""
    client = _get_client(project=project)
    from_d = date.fromisoformat(from_date) if from_date else None

    async def _run() -> None:
        async with client:
            selected = await search_and_select(
                client, name=name, status=status, from_date=from_d,
            )
            if selected:
                console.print(f"\nLaunch UUID: [bold cyan]{selected.uuid}[/bold cyan]")
                console.print(f"Use: [dim]rp-fetch download {selected.uuid}[/dim]")

    try:
        asyncio.run(_run())
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
    include: Annotated[Optional[list[str]], typer.Option(help="Content types: logs|attachments|screenshots|all")] = None,
    level: Annotated[Optional[str], typer.Option(help="Minimum log level: error|warn|info|debug|trace")] = None,
    parallel: Annotated[int, typer.Option(help="Parallel attachment downloads")] = 4,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without writing files")] = False,
    flat: Annotated[bool, typer.Option(help="Flatten output into a single directory")] = False,
    project: Annotated[Optional[str], typer.Option(help="Override project name")] = None,
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
            console.print("[red]Disk full![/red] Download aborted. Manifest preserved with progress checkpoint.")
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
    from_date: Annotated[Optional[str], typer.Option("--from", help="Pre-filter by start date (YYYY-MM-DD)")] = None,
    out: Annotated[Optional[str], typer.Option(help="Output directory")] = None,
    include: Annotated[Optional[list[str]], typer.Option(help="Content types: logs|attachments|screenshots|all")] = None,
    level: Annotated[Optional[str], typer.Option(help="Minimum log level")] = None,
    parallel: Annotated[int, typer.Option(help="Parallel attachment downloads")] = 4,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without writing files")] = False,
    flat: Annotated[bool, typer.Option(help="Flatten output")] = False,
    project: Annotated[Optional[str], typer.Option(help="Override project name")] = None,
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
                client, name=name, status=status, from_date=from_d,
            )
            if not selected:
                return

            confirm = typer.confirm(f"\nDownload launch '{selected.name}'?", default=True)
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

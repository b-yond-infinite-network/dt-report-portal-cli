"""Recursive download orchestration for ReportPortal launches."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from rp_fetch.client import RPClient, RPClientError, RPProxyAuthError
from rp_fetch.fs import OutputWriter
from rp_fetch.models import (
    Launch,
    LogEntry,
    Manifest,
    ManifestError,
    TestItem,
)

console = Console()

# Log levels ordered by severity
LOG_LEVELS = ["trace", "debug", "info", "warn", "error"]


def _should_include_log(log: LogEntry, min_level: str | None) -> bool:
    """Check if a log entry meets the minimum log level threshold."""
    if not min_level or min_level.lower() == "all":
        return True
    if not log.level:
        return True  # include logs with no level
    try:
        min_idx = LOG_LEVELS.index(min_level.lower())
        log_idx = LOG_LEVELS.index(log.level.lower())
        return log_idx >= min_idx
    except ValueError:
        return True


def _format_log_entry(log: LogEntry) -> str:
    """Format a single log entry as a text line."""
    ts = log.log_time.isoformat() if log.log_time else "no-timestamp"
    level = (log.level or "INFO").upper()
    return f"[{ts}] [{level}] {log.message}"


async def download_launch(
    rp_client: RPClient,
    launch_uuid: str,
    *,
    output_dir: Path,
    include: list[str] | None = None,
    min_level: str | None = None,
    parallel: int = 4,
    dry_run: bool = False,
    flat: bool = False,
) -> Manifest:
    """Download all content for a launch.

    Args:
        rp_client: Authenticated RPClient instance (already in context manager).
        launch_uuid: UUID of the launch to download.
        output_dir: Base output directory.
        include: Content types to include (logs, attachments, screenshots, all).
        min_level: Minimum log level to include.
        parallel: Number of parallel attachment downloads.
        dry_run: If True, only preview what would be downloaded.
        flat: If True, flatten output into a single directory.

    Returns:
        Manifest with download summary.
    """
    include = include or ["all"]
    include_set = set(i.lower() for i in include)
    want_logs = "all" in include_set or "logs" in include_set
    want_attachments = (
        "all" in include_set
        or "attachments" in include_set
        or "screenshots" in include_set
    )
    want_screenshots_only = (
        "screenshots" in include_set
        and "attachments" not in include_set
        and "all" not in include_set
    )

    # Step 1: Resolve launch
    console.print(f"[bold]Resolving launch [cyan]{launch_uuid}[/cyan]...[/bold]")
    launch = await rp_client.get_launch(launch_uuid)
    console.print(f"  Launch: [green]{launch.name}[/green] ({launch.status})")

    manifest = Manifest(
        launch_uuid=launch.uuid,
        launch_name=launch.name,
        started=launch.start_time,
    )

    writer = OutputWriter(output_dir, launch, flat=flat)

    if dry_run:
        console.print("\n[yellow][DRY RUN] Previewing download...[/yellow]")
    else:
        writer.setup()
        writer.write_launch_metadata(launch)

    # Step 2: Fetch all test items
    console.print("[bold]Fetching test items...[/bold]")
    all_items = await rp_client.get_all_items(launch.id)
    manifest.total_items = len(all_items)
    console.print(f"  Found [cyan]{len(all_items)}[/cyan] test items")

    items_by_id: dict[int, TestItem] = {item.id: item for item in all_items}

    if dry_run:
        _print_dry_run_summary(all_items, items_by_id, want_logs, want_attachments)
        return manifest

    # Step 3 & 4: Fetch logs and download attachments
    semaphore = asyncio.Semaphore(parallel)
    attachment_tasks: list[asyncio.Task[None]] = []
    total_bytes = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        items_task = progress.add_task("Processing items", total=len(all_items))

        for item in all_items:
            progress.update(items_task, description=f"Processing: {item.name[:50]}")

            # Fetch logs
            logs = await rp_client.get_all_logs(item.id)
            manifest.total_logs += len(logs)

            # Write logs
            if want_logs:
                filtered_logs = [l for l in logs if _should_include_log(l, min_level)]
                if filtered_logs:
                    log_text = (
                        "\n".join(_format_log_entry(l) for l in filtered_logs) + "\n"
                    )
                    writer.write_logs(item, items_by_id, log_text)

            # Write item metadata
            writer.write_item_metadata(item, items_by_id)

            # Queue attachment downloads
            if want_attachments:
                for log in logs:
                    if log.binary_content and log.binary_content.id:
                        bc = log.binary_content
                        if want_screenshots_only:
                            ct = (bc.content_type or "").lower()
                            if not ct.startswith("image/"):
                                continue
                        manifest.total_attachments += 1
                        task = asyncio.create_task(
                            _download_attachment(
                                rp_client,
                                writer,
                                item,
                                items_by_id,
                                bc.id,
                                bc.content_type,
                                semaphore,
                                manifest,
                            )
                        )
                        attachment_tasks.append(task)

            progress.update(items_task, advance=1)

    # Wait for all attachment downloads
    if attachment_tasks:
        console.print(
            f"[bold]Downloading [cyan]{len(attachment_tasks)}[/cyan] attachments ({parallel} parallel)...[/bold]"
        )
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            att_task = progress.add_task("Attachments", total=len(attachment_tasks))
            for coro in asyncio.as_completed(attachment_tasks):
                await coro
                progress.update(att_task, advance=1)

    # Step 5: Write manifest
    writer.write_manifest(manifest)
    console.print(f"\n[bold green]Download complete![/bold green]")
    console.print(f"  Items:       {manifest.total_items}")
    console.print(f"  Logs:        {manifest.total_logs}")
    console.print(f"  Attachments: {manifest.total_attachments}")
    console.print(f"  Errors:      {len(manifest.errors)}")
    console.print(f"  Output:      {writer.launch_dir}")

    return manifest


async def _download_attachment(
    rp_client: RPClient,
    writer: OutputWriter,
    item: TestItem,
    items_by_id: dict[int, TestItem],
    binary_id: str,
    content_type: str | None,
    semaphore: asyncio.Semaphore,
    manifest: Manifest,
) -> None:
    """Download a single binary attachment with concurrency control."""
    async with semaphore:
        try:
            data = await rp_client.download_attachment(binary_id)
            writer.write_attachment(item, items_by_id, data, content_type, binary_id)
            manifest.total_bytes += len(data)
        except RPProxyAuthError:
            # Proxy auth errors must bubble up to the CLI for re-prompt,
            # not be silently recorded as a per-attachment manifest error.
            raise
        except RPClientError as exc:
            manifest.errors.append(
                ManifestError(
                    item_id=item.id,
                    binary_content_id=binary_id,
                    error=str(exc),
                    retry_suggestion=f"rp-fetch download {manifest.launch_uuid} --include attachments",
                )
            )
        except OSError as exc:
            if "No space left on device" in str(exc):
                raise
            manifest.errors.append(
                ManifestError(
                    item_id=item.id,
                    binary_content_id=binary_id,
                    error=f"File write error: {exc}",
                )
            )


def _print_dry_run_summary(
    items: list[TestItem],
    items_by_id: dict[int, TestItem],
    want_logs: bool,
    want_attachments: bool,
) -> None:
    """Print a summary of what would be downloaded."""
    from rp_fetch.fs import build_item_path

    console.print(f"\n[bold]Would download {len(items)} test items:[/bold]")
    for item in items[:20]:
        path = build_item_path(item, items_by_id)
        console.print(f"  items/{path}/")
    if len(items) > 20:
        console.print(f"  ... and {len(items) - 20} more")
    console.print()
    if want_logs:
        console.print("  [cyan]✓[/cyan] Logs will be downloaded")
    if want_attachments:
        console.print("  [cyan]✓[/cyan] Attachments will be downloaded")
    console.print("\n[yellow]No files written (dry run).[/yellow]")

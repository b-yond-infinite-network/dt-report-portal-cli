"""Launch search and interactive selection."""

from __future__ import annotations

from datetime import date

from rich.console import Console
from rich.prompt import IntPrompt
from rich.table import Table

from rp_fetch.client import RPClient
from rp_fetch.models import Launch

console = Console()


def display_launches_table(launches: list[Launch]) -> None:
    """Display launches in a rich table."""
    table = Table(title="Launches", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Launch UUID", style="cyan", max_width=36)
    table.add_column("Name", style="green")
    table.add_column("Status", style="bold")
    table.add_column("Started", style="blue")
    table.add_column("Number", style="dim")

    for i, launch in enumerate(launches, 1):
        status_style = {
            "PASSED": "green",
            "FAILED": "red",
            "STOPPED": "yellow",
            "INTERRUPTED": "magenta",
        }.get((launch.status or "").upper(), "white")

        started = launch.start_time.strftime("%Y-%m-%d %H:%M") if launch.start_time else "—"
        table.add_row(
            str(i),
            launch.uuid,
            launch.name,
            f"[{status_style}]{launch.status or '—'}[/{status_style}]",
            started,
            str(launch.number or "—"),
        )

    console.print(table)


async def search_and_select(
    rp_client: RPClient,
    *,
    name: str | None = None,
    status: str | None = None,
    from_date: date | None = None,
    limit: int = 20,
) -> Launch | None:
    """Search launches and let the user interactively select one.

    Returns the selected Launch or None if the user cancels.
    """
    launches, page_info = await rp_client.list_launches(
        limit=limit,
        name=name,
        status=status,
        from_date=from_date,
    )

    if not launches:
        console.print("[yellow]No launches found matching the criteria.[/yellow]")
        return None

    display_launches_table(launches)
    console.print(f"\nShowing {len(launches)} of {page_info.total_elements} total launches.")

    try:
        choice = IntPrompt.ask(
            "\nSelect a launch by number (0 to cancel)",
            default=0,
        )
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled.[/dim]")
        return None

    if choice == 0 or choice > len(launches):
        console.print("[dim]Selection cancelled.[/dim]")
        return None

    selected = launches[choice - 1]
    console.print(f"\nSelected: [bold green]{selected.name}[/bold green] ({selected.uuid})")
    return selected

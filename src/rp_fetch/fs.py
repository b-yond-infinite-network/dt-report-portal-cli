"""Filesystem output structure builder."""

from __future__ import annotations

import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from rp_fetch.models import Launch, Manifest, TestItem


def _sanitize_name(name: str) -> str:
    """Sanitize a name for use as a directory/file name."""
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", name)
    sanitized = sanitized.strip(". ")
    return sanitized[:200] or "unnamed"


def launch_dir_name(launch: Launch) -> str:
    """Generate a directory name for a launch: {name}_{date}."""
    name = _sanitize_name(launch.name)
    date_str = ""
    if launch.start_time:
        date_str = f"_{launch.start_time.strftime('%Y-%m-%d')}"
    return f"{name}{date_str}"


def build_item_path(item: TestItem, items_by_id: dict[int, TestItem]) -> Path:
    """Build a relative path for a test item based on its parent hierarchy."""
    parts: list[str] = []
    current: TestItem | None = item
    while current is not None:
        parts.append(_sanitize_name(current.name))
        current = items_by_id.get(current.parent) if current.parent else None
    parts.reverse()
    return Path(*parts) if parts else Path(_sanitize_name(item.name))


def flat_prefix(item: TestItem, items_by_id: dict[int, TestItem]) -> str:
    """Build a flat filename prefix from the item hierarchy."""
    path = build_item_path(item, items_by_id)
    return str(path).replace("/", "__").replace("\\", "__")


def extension_from_content_type(content_type: str | None) -> str:
    """Guess file extension from a MIME content type."""
    if not content_type:
        return ".bin"
    ext = mimetypes.guess_extension(content_type, strict=False)
    if ext:
        return ext
    # Common mappings not always in mimetypes
    fallback = {
        "application/vnd.tcpdump.pcap": ".pcap",
        "application/pcap": ".pcap",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "text/plain": ".log",
        "application/octet-stream": ".bin",
    }
    return fallback.get(content_type.lower(), ".bin")


class OutputWriter:
    """Manages writing downloaded content to the local filesystem."""

    def __init__(self, base_dir: Path, launch: Launch, *, flat: bool = False) -> None:
        self.flat = flat
        self.launch_dir = base_dir / launch_dir_name(launch)
        self.items_dir = self.launch_dir / "items"
        self.launch = launch
        self._attachment_counter: dict[int, int] = {}

    def setup(self) -> Path:
        """Create the output directory structure. Returns the root output path."""
        self.launch_dir.mkdir(parents=True, exist_ok=True)
        if not self.flat:
            self.items_dir.mkdir(exist_ok=True)
        return self.launch_dir

    def write_launch_metadata(self, launch: Launch) -> Path:
        path = self.launch_dir / "launch_metadata.json"
        path.write_text(
            json.dumps(launch.model_dump(mode="json", by_alias=True), indent=2, default=str),
            encoding="utf-8",
        )
        return path

    def item_dir(self, item: TestItem, items_by_id: dict[int, TestItem]) -> Path:
        """Get or create the directory for a test item."""
        if self.flat:
            return self.launch_dir
        item_path = self.items_dir / build_item_path(item, items_by_id)
        item_path.mkdir(parents=True, exist_ok=True)
        return item_path

    def write_item_metadata(
        self, item: TestItem, items_by_id: dict[int, TestItem]
    ) -> Path:
        dest = self.item_dir(item, items_by_id)
        if self.flat:
            prefix = flat_prefix(item, items_by_id)
            path = dest / f"{prefix}__item_metadata.json"
        else:
            path = dest / "item_metadata.json"
        path.write_text(
            json.dumps(item.model_dump(mode="json", by_alias=True), indent=2, default=str),
            encoding="utf-8",
        )
        return path

    def write_logs(
        self, item: TestItem, items_by_id: dict[int, TestItem], log_text: str
    ) -> Path | None:
        if not log_text.strip():
            return None
        dest = self.item_dir(item, items_by_id)
        if self.flat:
            prefix = flat_prefix(item, items_by_id)
            path = dest / f"{prefix}__logs.txt"
        else:
            path = dest / "logs.txt"
        path.write_text(log_text, encoding="utf-8")
        return path

    def write_attachment(
        self,
        item: TestItem,
        items_by_id: dict[int, TestItem],
        data: bytes,
        content_type: str | None,
        binary_id: str,
    ) -> Path:
        ext = extension_from_content_type(content_type)
        counter = self._attachment_counter.get(item.id, 0)
        self._attachment_counter[item.id] = counter + 1

        if self.flat:
            prefix = flat_prefix(item, items_by_id)
            filename = f"{prefix}__attachment_{counter:03d}{ext}"
            path = self.launch_dir / filename
        else:
            att_dir = self.item_dir(item, items_by_id) / "attachments"
            att_dir.mkdir(exist_ok=True)
            filename = f"attachment_{counter:03d}{ext}"
            path = att_dir / filename

        path.write_bytes(data)
        return path

    def write_manifest(self, manifest: Manifest) -> Path:
        manifest.completed_at = datetime.now()
        path = self.launch_dir / "manifest.json"
        path.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )
        return path

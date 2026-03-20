"""Pydantic models for ReportPortal API responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LaunchAttributes(BaseModel):
    key: str | None = None
    value: str
    system: bool = False


class Launch(BaseModel):
    id: int
    uuid: str
    name: str
    number: int | None = None
    description: str | None = None
    status: str | None = None
    start_time: datetime | None = Field(None, alias="startTime")
    end_time: datetime | None = Field(None, alias="endTime")
    attributes: list[LaunchAttributes] = []
    statistics: dict[str, Any] = {}
    has_retries: bool = Field(False, alias="hasRetries")

    model_config = {"populate_by_name": True}


class TestItem(BaseModel):
    id: int
    uuid: str
    name: str
    type: str | None = None
    status: str | None = None
    description: str | None = None
    start_time: datetime | None = Field(None, alias="startTime")
    end_time: datetime | None = Field(None, alias="endTime")
    parent: int | None = None
    path_names: dict[str, str] | None = Field(None, alias="pathNames")
    launch_id: int | None = Field(None, alias="launchId")
    has_children: bool = Field(False, alias="hasChildren")
    has_stats: bool = Field(False, alias="hasStats")
    statistics: dict[str, Any] = {}

    model_config = {"populate_by_name": True}


class BinaryContent(BaseModel):
    id: str
    content_type: str | None = Field(None, alias="contentType")
    thumbnail_id: str | None = Field(None, alias="thumbnailId")

    model_config = {"populate_by_name": True}


class LogEntry(BaseModel):
    id: int
    uuid: str | None = None
    message: str = ""
    level: str | None = None
    log_time: datetime | None = Field(None, alias="logTime")
    item_id: int | None = Field(None, alias="itemId")
    launch_id: int | None = Field(None, alias="launchId")
    binary_content: BinaryContent | None = Field(None, alias="binaryContent")

    model_config = {"populate_by_name": True}


class Page(BaseModel):
    number: int
    size: int
    total_elements: int = Field(0, alias="totalElements")
    total_pages: int = Field(0, alias="totalPages")

    model_config = {"populate_by_name": True}


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""
    content: list[dict[str, Any]]
    page: Page


class ManifestError(BaseModel):
    item_id: int | None = None
    binary_content_id: str | None = None
    error: str
    retry_suggestion: str | None = None


class Manifest(BaseModel):
    launch_uuid: str
    launch_name: str
    started: datetime | None = None
    total_items: int = 0
    total_logs: int = 0
    total_attachments: int = 0
    total_bytes: int = 0
    items: list[dict[str, Any]] = []
    errors: list[ManifestError] = []
    completed_at: datetime | None = None

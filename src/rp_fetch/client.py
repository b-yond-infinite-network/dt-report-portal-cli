"""Async HTTP client for the ReportPortal v5 API."""

from __future__ import annotations

import asyncio
import random
from datetime import date
from typing import Any

import httpx

from rp_fetch.auth import auth_headers
from rp_fetch.models import (
    BinaryContent,
    Launch,
    LogEntry,
    Page,
    TestItem,
)

# Minimum delay between sequential log-page requests (seconds).
LOG_PAGE_DELAY = 0.1

# Retry / backoff settings
MAX_RETRIES_429 = 5
MAX_RETRIES_TIMEOUT = 3
BACKOFF_BASE = 1.0  # seconds


class RPClientError(Exception):
    """Base exception for ReportPortal client errors."""


class RPAuthError(RPClientError):
    """401 / 403 — bad or insufficient credentials."""


class RPNotFoundError(RPClientError):
    """404 — resource not found."""


class RPClient:
    """Thin async wrapper around the ReportPortal REST API (v5+)."""

    def __init__(self, base_url: str, api_key: str, project: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.project = project
        self._headers = auth_headers(api_key)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "RPClient":
        self._client = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v1/{self.project}",
            headers=self._headers,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("RPClient must be used as an async context manager")
        return self._client

    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Execute a request with retry logic for 429 and timeouts."""
        last_exc: Exception | None = None
        max_retries = MAX_RETRIES_429

        for attempt in range(max_retries + 1):
            try:
                resp = await self.client.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                if attempt < MAX_RETRIES_TIMEOUT:
                    await asyncio.sleep(BACKOFF_BASE * (attempt + 1))
                    last_exc = exc
                    continue
                raise RPClientError(f"Request timed out after {MAX_RETRIES_TIMEOUT} retries: {path}") from exc

            if resp.status_code == 401:
                raise RPAuthError(
                    "401 Unauthorized — check your API key. "
                    "Generate one at: ReportPortal → Profile → API Keys"
                )
            if resp.status_code == 403:
                raise RPAuthError(
                    "403 Forbidden — your API key may lack read access to "
                    f"project '{self.project}'"
                )
            if resp.status_code == 404:
                raise RPNotFoundError(f"404 Not Found: {path}")
            if resp.status_code == 429:
                if attempt < max_retries:
                    delay = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(delay)
                    continue
                raise RPClientError("Rate limited (429) after max retries")

            resp.raise_for_status()
            return resp

        raise last_exc or RPClientError("Request failed")

    async def _get_json(self, path: str, **params: Any) -> dict[str, Any]:
        # Strip None params
        cleaned = {k: v for k, v in params.items() if v is not None}
        resp = await self._request("GET", path, params=cleaned)
        return resp.json()

    # ------------------------------------------------------------------
    # Launch endpoints
    # ------------------------------------------------------------------

    async def get_launch(self, uuid: str) -> Launch:
        data = await self._get_json(f"/launch/uuid/{uuid}")
        return Launch.model_validate(data)

    async def list_launches(
        self,
        *,
        limit: int = 20,
        name: str | None = None,
        status: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        attributes: list[str] | None = None,
        page: int = 1,
    ) -> tuple[list[Launch], Page]:
        params: dict[str, Any] = {
            "page.size": limit,
            "page.page": page,
            "page.sort": "startTime,desc",
        }
        if name:
            params["filter.cnt.name"] = name
        if status:
            params["filter.eq.status"] = status.upper()
        if from_date:
            params["filter.gte.startTime"] = f"{from_date.isoformat()}T00:00:00"
        if to_date:
            params["filter.lte.startTime"] = f"{to_date.isoformat()}T23:59:59"
        if attributes:
            for attr in attributes:
                params["filter.has.attributeValue"] = attr

        data = await self._get_json("/launch", **params)
        launches = [Launch.model_validate(item) for item in data.get("content", [])]
        page_info = Page.model_validate(data.get("page", {}))
        return launches, page_info

    # ------------------------------------------------------------------
    # Test item endpoints
    # ------------------------------------------------------------------

    async def get_items(
        self, launch_id: int, *, page: int = 1, page_size: int = 100
    ) -> tuple[list[TestItem], Page]:
        data = await self._get_json(
            "/item",
            **{
                "filter.eq.launchId": launch_id,
                "page.size": page_size,
                "page.page": page,
            },
        )
        items = [TestItem.model_validate(item) for item in data.get("content", [])]
        page_info = Page.model_validate(data.get("page", {}))
        return items, page_info

    async def get_all_items(self, launch_id: int) -> list[TestItem]:
        """Fetch all test items across all pages."""
        all_items: list[TestItem] = []
        page = 1
        while True:
            items, page_info = await self.get_items(launch_id, page=page)
            all_items.extend(items)
            if page >= page_info.total_pages:
                break
            page += 1
        return all_items

    # ------------------------------------------------------------------
    # Log endpoints
    # ------------------------------------------------------------------

    async def get_logs(
        self, item_id: int, *, page: int = 1, page_size: int = 100
    ) -> tuple[list[LogEntry], Page]:
        data = await self._get_json(
            "/log",
            **{
                "filter.eq.item": item_id,
                "page.size": page_size,
                "page.page": page,
            },
        )
        logs = [LogEntry.model_validate(entry) for entry in data.get("content", [])]
        page_info = Page.model_validate(data.get("page", {}))
        return logs, page_info

    async def get_all_logs(self, item_id: int) -> list[LogEntry]:
        """Fetch all logs for a test item across all pages, respecting rate limits."""
        all_logs: list[LogEntry] = []
        page = 1
        while True:
            logs, page_info = await self.get_logs(item_id, page=page)
            all_logs.extend(logs)
            if page >= page_info.total_pages:
                break
            page += 1
            await asyncio.sleep(LOG_PAGE_DELAY)
        return all_logs

    # ------------------------------------------------------------------
    # Binary attachment download
    # ------------------------------------------------------------------

    async def download_attachment(self, binary_id: str) -> bytes:
        # The file storage endpoint is /api/v1/data/{project}/{id} — note:
        # /data is at the API root, not nested under the project path.
        url = f"{self.base_url}/api/v1/data/{self.project}/{binary_id}"
        resp = await self.client.get(url, headers=self._headers)
        if resp.status_code == 404:
            raise RPNotFoundError(f"404 Not Found: /data/{self.project}/{binary_id}")
        resp.raise_for_status()
        return resp.content

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    async def test_connection(self) -> bool:
        """Test that authentication and project access work."""
        await self.list_launches(limit=1)
        return True

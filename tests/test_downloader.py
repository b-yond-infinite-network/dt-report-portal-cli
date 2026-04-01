"""Tests for the download orchestration engine."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from rp_fetch.client import RPProxyAuthError
from rp_fetch.downloader import (
    _download_attachment,
    _format_log_entry,
    _should_include_log,
)
from rp_fetch.models import LogEntry, Manifest, TestItem


def _make_log(level: str = "INFO", message: str = "test") -> LogEntry:
    return LogEntry.model_validate(
        {
            "id": 1,
            "message": message,
            "level": level,
            "time": "2026-03-18T14:30:00Z",
        }
    )


class TestShouldIncludeLog:
    def test_no_min_level(self):
        log = _make_log("DEBUG")
        assert _should_include_log(log, None) is True

    def test_min_level_all(self):
        log = _make_log("TRACE")
        assert _should_include_log(log, "all") is True

    def test_error_filters_lower(self):
        assert _should_include_log(_make_log("ERROR"), "error") is True
        assert _should_include_log(_make_log("WARN"), "error") is False
        assert _should_include_log(_make_log("INFO"), "error") is False

    def test_warn_includes_error(self):
        assert _should_include_log(_make_log("ERROR"), "warn") is True
        assert _should_include_log(_make_log("WARN"), "warn") is True
        assert _should_include_log(_make_log("INFO"), "warn") is False

    def test_debug_includes_most(self):
        assert _should_include_log(_make_log("ERROR"), "debug") is True
        assert _should_include_log(_make_log("DEBUG"), "debug") is True
        assert _should_include_log(_make_log("TRACE"), "debug") is False

    def test_no_level_on_log_always_included(self):
        log = LogEntry.model_validate({"id": 1, "message": "test"})
        assert _should_include_log(log, "error") is True


class TestFormatLogEntry:
    def test_basic_format(self):
        log = _make_log("INFO", "Hello world")
        result = _format_log_entry(log)
        assert "[INFO]" in result
        assert "Hello world" in result
        assert "2026-03-18" in result

    def test_no_timestamp(self):
        log = LogEntry.model_validate({"id": 1, "message": "msg", "level": "ERROR"})
        result = _format_log_entry(log)
        assert "no-timestamp" in result
        assert "[ERROR]" in result


@pytest.mark.asyncio
async def test_download_attachment_propagates_proxy_auth_error():
    """RPProxyAuthError must bubble up, not be recorded as a manifest error."""
    mock_client = AsyncMock()
    mock_client.download_attachment.side_effect = RPProxyAuthError("proxy auth failed")

    mock_writer = MagicMock()
    item = TestItem.model_validate(
        {
            "id": 10,
            "uuid": "item-uuid",
            "name": "Test Item",
            "type": "STEP",
            "status": "PASSED",
            "launchId": 1,
            "hasChildren": False,
            "hasStats": True,
        }
    )
    items_by_id = {10: item}
    manifest = Manifest(launch_uuid="abc-123", launch_name="Test Launch")
    semaphore = asyncio.Semaphore(1)

    with pytest.raises(RPProxyAuthError, match="proxy auth failed"):
        await _download_attachment(
            mock_client,
            mock_writer,
            item,
            items_by_id,
            "bin-id",
            "image/png",
            semaphore,
            manifest,
        )

    # Ensure no manifest error was recorded
    assert len(manifest.errors) == 0

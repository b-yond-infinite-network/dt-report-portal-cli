"""Tests for the download orchestration engine."""

import pytest

from rp_fetch.downloader import _should_include_log, _format_log_entry
from rp_fetch.models import LogEntry


def _make_log(level: str = "INFO", message: str = "test") -> LogEntry:
    return LogEntry.model_validate({
        "id": 1,
        "message": message,
        "level": level,
        "logTime": "2026-03-18T14:30:00Z",
    })


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

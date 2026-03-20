"""Tests for filesystem output module."""

from datetime import datetime
from pathlib import Path

import pytest

from rp_fetch.fs import (
    OutputWriter,
    _sanitize_name,
    build_item_path,
    extension_from_content_type,
    flat_prefix,
    launch_dir_name,
)
from rp_fetch.models import Launch, Manifest, TestItem


def _make_launch(**kwargs) -> Launch:
    defaults = {
        "id": 1,
        "uuid": "launch-uuid-1",
        "name": "Test Launch",
        "status": "PASSED",
        "startTime": "2026-03-18T14:00:00Z",
    }
    defaults.update(kwargs)
    return Launch.model_validate(defaults)


def _make_item(**kwargs) -> TestItem:
    defaults = {
        "id": 1,
        "uuid": "item-uuid-1",
        "name": "Test Item",
        "type": "TEST",
        "status": "PASSED",
    }
    defaults.update(kwargs)
    return TestItem.model_validate(defaults)


class TestSanitizeName:
    def test_removes_special_chars(self):
        assert _sanitize_name('test<>:"/\\|?*name') == "test_________name"

    def test_strips_dots_and_spaces(self):
        assert _sanitize_name("...test...") == "test"

    def test_truncates_long_names(self):
        long_name = "a" * 300
        assert len(_sanitize_name(long_name)) == 200

    def test_empty_becomes_unnamed(self):
        assert _sanitize_name("...") == "unnamed"


class TestLaunchDirName:
    def test_with_date(self):
        launch = _make_launch()
        assert launch_dir_name(launch) == "Test Launch_2026-03-18"

    def test_without_date(self):
        launch = _make_launch(startTime=None)
        assert launch_dir_name(launch) == "Test Launch"


class TestBuildItemPath:
    def test_single_item(self):
        item = _make_item(id=1, name="MyTest")
        items_by_id = {1: item}
        assert build_item_path(item, items_by_id) == Path("MyTest")

    def test_nested_hierarchy(self):
        suite = _make_item(id=1, name="Suite")
        test = _make_item(id=2, name="TestCase", parent=1)
        step = _make_item(id=3, name="Step1", parent=2)
        items_by_id = {1: suite, 2: test, 3: step}
        assert build_item_path(step, items_by_id) == Path("Suite/TestCase/Step1")


class TestFlatPrefix:
    def test_flat_prefix(self):
        suite = _make_item(id=1, name="Suite")
        test = _make_item(id=2, name="TestCase", parent=1)
        items_by_id = {1: suite, 2: test}
        assert flat_prefix(test, items_by_id) == "Suite__TestCase"


class TestExtensionFromContentType:
    def test_png(self):
        assert extension_from_content_type("image/png") == ".png"

    def test_pcap(self):
        assert extension_from_content_type("application/vnd.tcpdump.pcap") == ".pcap"

    def test_none(self):
        assert extension_from_content_type(None) == ".bin"

    def test_plain_text(self):
        assert extension_from_content_type("text/plain") in (".txt", ".log")


class TestOutputWriter:
    def test_setup_creates_directories(self, tmp_path):
        launch = _make_launch()
        writer = OutputWriter(tmp_path, launch)
        root = writer.setup()
        assert root.exists()
        assert root.parent == tmp_path

    def test_write_launch_metadata(self, tmp_path):
        launch = _make_launch()
        writer = OutputWriter(tmp_path, launch)
        writer.setup()
        path = writer.write_launch_metadata(launch)
        assert path.exists()
        import json
        data = json.loads(path.read_text())
        assert data["name"] == "Test Launch"

    def test_write_logs(self, tmp_path):
        launch = _make_launch()
        item = _make_item()
        items_by_id = {1: item}
        writer = OutputWriter(tmp_path, launch)
        writer.setup()
        path = writer.write_logs(item, items_by_id, "line1\nline2\n")
        assert path is not None
        assert path.read_text() == "line1\nline2\n"

    def test_write_logs_empty_returns_none(self, tmp_path):
        launch = _make_launch()
        item = _make_item()
        items_by_id = {1: item}
        writer = OutputWriter(tmp_path, launch)
        writer.setup()
        result = writer.write_logs(item, items_by_id, "   ")
        assert result is None

    def test_write_attachment(self, tmp_path):
        launch = _make_launch()
        item = _make_item()
        items_by_id = {1: item}
        writer = OutputWriter(tmp_path, launch)
        writer.setup()
        path = writer.write_attachment(
            item, items_by_id, b"fake-data", "image/png", "bin-1"
        )
        assert path.exists()
        assert path.suffix == ".png"
        assert path.read_bytes() == b"fake-data"

    def test_flat_mode(self, tmp_path):
        launch = _make_launch()
        suite = _make_item(id=1, name="Suite")
        test = _make_item(id=2, name="Test", parent=1)
        items_by_id = {1: suite, 2: test}
        writer = OutputWriter(tmp_path, launch, flat=True)
        writer.setup()
        path = writer.write_logs(test, items_by_id, "log data\n")
        assert path is not None
        assert "__" in path.name  # flat prefix separator

    def test_write_manifest(self, tmp_path):
        launch = _make_launch()
        writer = OutputWriter(tmp_path, launch)
        writer.setup()
        manifest = Manifest(
            launch_uuid="launch-uuid-1",
            launch_name="Test Launch",
            total_items=5,
            total_logs=10,
        )
        path = writer.write_manifest(manifest)
        assert path.exists()
        import json
        data = json.loads(path.read_text())
        assert data["total_items"] == 5

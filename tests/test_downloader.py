import json
import os
from pathlib import Path

import httpx
import pytest

from src.downloader import (
    download_file,
    verify_hash,
    write_metadata_sidecar,
    should_skip_file,
    DownloadResult,
)
from src.models import DriveItem
from src.quickxor import QuickXorHash


def _make_item(
    name: str = "test.txt",
    size: int = 100,
    quick_xor_hash: str | None = "abc=",
    remote_path: str = "Documents",
    item_id: str = "item1",
) -> DriveItem:
    from datetime import datetime
    return DriveItem(
        id=item_id,
        name=name,
        size=size,
        is_folder=False,
        created=datetime(2023, 6, 15, 10, 30),
        modified=datetime(2024, 1, 20, 14, 0),
        remote_path=remote_path,
        quick_xor_hash=quick_xor_hash,
    )


def test_verify_hash_match():
    """verify_hash returns True when hashes match."""
    data = b"hello world"
    h = QuickXorHash()
    h.update(data)
    expected = h.base64_digest()
    assert verify_hash(data, expected) is True


def test_verify_hash_mismatch():
    """verify_hash returns False on mismatch."""
    assert verify_hash(b"hello", "AAAAAAAAAAAAAAAAAAAAAAAAAAAA") is False


def test_should_skip_file_exists_correct_size(tmp_path):
    """Skip file when it exists locally with matching size."""
    item = _make_item(size=11)
    local_file = tmp_path / "Documents" / "test.txt"
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"hello world")
    assert should_skip_file(item, tmp_path) is True


def test_should_skip_file_wrong_size(tmp_path):
    """Don't skip when local file has different size."""
    item = _make_item(size=9999)
    local_file = tmp_path / "Documents" / "test.txt"
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"hello world")
    assert should_skip_file(item, tmp_path) is False


def test_should_skip_file_not_exists(tmp_path):
    """Don't skip when file doesn't exist locally."""
    item = _make_item()
    assert should_skip_file(item, tmp_path) is False


def test_write_metadata_sidecar(tmp_path):
    """Writes .metadata.json with item details."""
    item = _make_item()
    write_metadata_sidecar(item, tmp_path)
    sidecar_path = tmp_path / "Documents" / ".metadata.json"
    assert sidecar_path.exists()
    data = json.loads(sidecar_path.read_text())
    assert "test.txt" in data
    assert data["test.txt"]["id"] == "item1"
    assert data["test.txt"]["created"] == "2023-06-15T10:30:00"
    assert data["test.txt"]["modified"] == "2024-01-20T14:00:00"


def test_write_metadata_sidecar_appends(tmp_path):
    """Appends to existing .metadata.json instead of overwriting."""
    item1 = _make_item(name="a.txt", item_id="id1")
    item2 = _make_item(name="b.txt", item_id="id2")
    write_metadata_sidecar(item1, tmp_path)
    write_metadata_sidecar(item2, tmp_path)
    sidecar_path = tmp_path / "Documents" / ".metadata.json"
    data = json.loads(sidecar_path.read_text())
    assert "a.txt" in data
    assert "b.txt" in data

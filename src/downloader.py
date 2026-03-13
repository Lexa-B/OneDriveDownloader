from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from src.models import DriveItem
from src.quickxor import QuickXorHash

if TYPE_CHECKING:
    from collections.abc import Callable

CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB


class DownloadStatus(Enum):
    SUCCESS = auto()
    HASH_MISMATCH = auto()
    MISSING_HASH = auto()
    SKIPPED = auto()
    FAILED = auto()


@dataclass
class DownloadResult:
    item: DriveItem
    status: DownloadStatus
    error: str | None = None


def should_skip_file(item: DriveItem, output_dir: Path) -> bool:
    local_path = output_dir / item.full_path
    if not local_path.exists():
        return False
    return local_path.stat().st_size == item.size


def verify_hash(data: bytes, expected_hash: str) -> bool:
    h = QuickXorHash()
    h.update(data)
    return h.base64_digest() == expected_hash


def write_metadata_sidecar(item: DriveItem, output_dir: Path) -> None:
    folder_path = output_dir / item.remote_path if item.remote_path else output_dir
    folder_path.mkdir(parents=True, exist_ok=True)
    sidecar_path = folder_path / ".metadata.json"

    existing: dict = {}
    if sidecar_path.exists():
        existing = json.loads(sidecar_path.read_text())

    existing[item.name] = {
        "id": item.id,
        "size": item.size,
        "created": item.created.isoformat(),
        "modified": item.modified.isoformat(),
        "quick_xor_hash": item.quick_xor_hash,
    }
    sidecar_path.write_text(json.dumps(existing, indent=2))


def set_file_timestamps(file_path: Path, item: DriveItem) -> None:
    mtime = item.modified.timestamp()
    os.utime(file_path, (mtime, mtime))


async def download_file(
    item: DriveItem,
    download_url: str,
    output_dir: Path,
    http_client: httpx.AsyncClient,
    on_progress: Callable[[int], None] | None = None,
) -> DownloadResult:
    if item.quick_xor_hash is None:
        return DownloadResult(item=item, status=DownloadStatus.MISSING_HASH)

    local_path = output_dir / item.full_path
    local_path.parent.mkdir(parents=True, exist_ok=True)

    hasher = QuickXorHash()
    temp_path = local_path.with_suffix(local_path.suffix + ".tmp")

    try:
        async with http_client.stream("GET", download_url) as response:
            response.raise_for_status()
            with open(temp_path, "wb") as f:
                async for chunk in response.aiter_bytes(CHUNK_SIZE):
                    f.write(chunk)
                    hasher.update(chunk)
                    if on_progress:
                        on_progress(len(chunk))

        computed_hash = hasher.base64_digest()
        if computed_hash != item.quick_xor_hash:
            temp_path.unlink(missing_ok=True)
            return DownloadResult(
                item=item,
                status=DownloadStatus.HASH_MISMATCH,
                error=f"Expected {item.quick_xor_hash}, got {computed_hash}",
            )

        temp_path.rename(local_path)
        set_file_timestamps(local_path, item)

        return DownloadResult(item=item, status=DownloadStatus.SUCCESS)

    except Exception as e:
        temp_path.unlink(missing_ok=True)
        return DownloadResult(item=item, status=DownloadStatus.FAILED, error=str(e))

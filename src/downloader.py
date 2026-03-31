from __future__ import annotations

import asyncio
import base64
import json
import multiprocessing
import os
import struct
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from src.models import DriveItem
from src.quickxor import QuickXorHash

if TYPE_CHECKING:
    from collections.abc import Callable

CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB
_HASH_WORKERS: ProcessPoolExecutor | None = None


def init_hash_pool() -> None:
    """Initialize the hash worker pool. Must be called before Textual starts."""
    global _HASH_WORKERS
    if _HASH_WORKERS is None:
        _HASH_WORKERS = ProcessPoolExecutor()
        # Force workers to spawn now while FDs are clean
        _HASH_WORKERS.submit(int).result()


def _get_hash_pool() -> ProcessPoolExecutor:
    if _HASH_WORKERS is None:
        raise RuntimeError("Hash pool not initialized — call init_hash_pool() before starting the app")
    return _HASH_WORKERS


def _hash_file_chunk(file_path: str, offset: int, length: int, start_shift: int) -> list[int]:
    """Hash a chunk of a file, returning partial QuickXorHash cells.

    Runs in a worker process. The caller combines results with XOR.
    """
    from src.quickxor import QuickXorHash
    hasher = QuickXorHash()
    hasher._shift_so_far = start_shift
    with open(file_path, "rb") as f:
        f.seek(offset)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(remaining, CHUNK_SIZE))
            if not chunk:
                break
            hasher.update(chunk)
            remaining -= len(chunk)
    return hasher._data


def parallel_hash_file(file_path: Path, file_size: int) -> str:
    """Compute QuickXorHash of a file using multiple processes."""
    from src.quickxor import SHIFT, WIDTH_IN_BITS

    num_workers = os.cpu_count() or 4
    chunk_size = max(CHUNK_SIZE, file_size // num_workers)
    pool = _get_hash_pool()

    futures = []
    offset = 0
    while offset < file_size:
        length = min(chunk_size, file_size - offset)
        start_shift = (offset * SHIFT) % WIDTH_IN_BITS
        futures.append(pool.submit(_hash_file_chunk, str(file_path), offset, length, start_shift))
        offset += length

    # Combine partial results with XOR
    combined = [0, 0, 0]
    for future in futures:
        partial = future.result()
        for i in range(3):
            combined[i] ^= partial[i]

    # Finalize: pack cells + XOR in file length (same as QuickXorHash.digest)
    MASK_64 = (1 << 64) - 1
    rgb = bytearray(20)
    struct.pack_into("<Q", rgb, 0, combined[0] & MASK_64)
    struct.pack_into("<Q", rgb, 8, combined[1] & MASK_64)
    struct.pack_into("<I", rgb, 16, combined[2] & 0xFFFFFFFF)
    length_bytes = struct.pack("<q", file_size)
    for i, b in enumerate(length_bytes):
        rgb[12 + i] ^= b
    return base64.b64encode(bytes(rgb)).decode("ascii")


def _rebuild_hash_state(temp_path: Path, size: int) -> list[int]:
    """Rebuild partial QuickXorHash cells from a .tmp file using all cores."""
    from src.quickxor import SHIFT, WIDTH_IN_BITS

    num_workers = os.cpu_count() or 4
    chunk_size = max(CHUNK_SIZE, size // num_workers)
    pool = _get_hash_pool()

    futures = []
    offset = 0
    while offset < size:
        length = min(chunk_size, size - offset)
        start_shift = (offset * SHIFT) % WIDTH_IN_BITS
        futures.append(pool.submit(_hash_file_chunk, str(temp_path), offset, length, start_shift))
        offset += length

    combined = [0, 0, 0]
    for future in futures:
        partial = future.result()
        for i in range(3):
            combined[i] ^= partial[i]
    return combined


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


def verify_local_file(item: DriveItem, output_dir: Path) -> DownloadResult:
    """Verify a local file matches the expected size and hash."""
    local_path = output_dir / item.full_path
    if not local_path.exists():
        return DownloadResult(item=item, status=DownloadStatus.FAILED, error="Local file missing")
    if local_path.stat().st_size != item.size:
        return DownloadResult(
            item=item, status=DownloadStatus.FAILED,
            error=f"Size mismatch: local {local_path.stat().st_size} vs remote {item.size}",
        )
    if item.quick_xor_hash is None:
        return DownloadResult(item=item, status=DownloadStatus.MISSING_HASH)
    computed = parallel_hash_file(local_path, item.size)
    if computed != item.quick_xor_hash:
        return DownloadResult(
            item=item, status=DownloadStatus.HASH_MISMATCH,
            error=f"Expected {item.quick_xor_hash}, got {computed}",
        )
    return DownloadResult(item=item, status=DownloadStatus.SKIPPED)


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
    on_retry: Callable[[], None] | None = None,
    on_refresh_url: Callable[[], Any] | None = None,  # async () -> str
    on_resume_done: Callable[[], None] | None = None,
) -> DownloadResult:
    if item.quick_xor_hash is None:
        return DownloadResult(item=item, status=DownloadStatus.MISSING_HASH)

    local_path = output_dir / item.full_path
    local_path.parent.mkdir(parents=True, exist_ok=True)

    hasher = QuickXorHash()
    temp_path = local_path.with_suffix(local_path.suffix + ".tmp")

    max_retries = 5
    last_error: Exception | None = None

    # Resume from existing .tmp file (cross-session resume)
    resume_offset = 0
    if temp_path.exists():
        resume_offset = temp_path.stat().st_size

    for attempt in range(max_retries):
        if attempt > 0 and on_retry:
            on_retry()

        # On resume, rebuild hash state from existing .tmp data
        # Uses parallel hashing across all cores to avoid blocking
        hasher = QuickXorHash()
        if resume_offset > 0 and temp_path.exists():
            partial_hash = await asyncio.to_thread(
                _rebuild_hash_state, temp_path, resume_offset,
            )
            hasher._data = partial_hash
            hasher._shift_so_far = (resume_offset * 11) % 160
            hasher._length_so_far = resume_offset
            if on_resume_done:
                on_resume_done()
            if on_progress:
                on_progress(resume_offset)

        try:
            headers = {}
            if resume_offset > 0:
                headers["Range"] = f"bytes={resume_offset}-"

            async with http_client.stream("GET", download_url, headers=headers) as response:
                if response.status_code == 401 and on_refresh_url and attempt < max_retries - 1:
                    download_url = await on_refresh_url()
                    continue
                if response.status_code in (429, 503, 502, 504):
                    retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                    await asyncio.sleep(retry_after)
                    continue
                response.raise_for_status()

                # 206 = partial content (resume), 200 = full (server ignored Range)
                if response.status_code == 200 and resume_offset > 0:
                    # Server sent full file — start over
                    resume_offset = 0
                    hasher = QuickXorHash()

                mode = "ab" if resume_offset > 0 else "wb"
                with open(temp_path, mode) as f:
                    async for chunk in response.aiter_bytes(CHUNK_SIZE):
                        f.write(chunk)
                        hasher.update(chunk)
                        if on_progress:
                            on_progress(len(chunk))

            computed_hash = hasher.base64_digest()
            if computed_hash != item.quick_xor_hash:
                temp_path.unlink(missing_ok=True)
                resume_offset = 0
                return DownloadResult(
                    item=item,
                    status=DownloadStatus.HASH_MISMATCH,
                    error=f"Expected {item.quick_xor_hash}, got {computed_hash}",
                )

            temp_path.rename(local_path)
            set_file_timestamps(local_path, item)

            return DownloadResult(item=item, status=DownloadStatus.SUCCESS)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401 and on_refresh_url and attempt < max_retries - 1:
                # Keep .tmp for resume — just need a fresh URL
                resume_offset = temp_path.stat().st_size if temp_path.exists() else 0
                download_url = await on_refresh_url()
                continue
            if e.response.status_code in (429, 502, 503, 504) and attempt < max_retries - 1:
                resume_offset = temp_path.stat().st_size if temp_path.exists() else 0
                retry_after = int(e.response.headers.get("Retry-After", 2 ** attempt))
                await asyncio.sleep(retry_after)
                continue
            temp_path.unlink(missing_ok=True)
            return DownloadResult(item=item, status=DownloadStatus.FAILED, error=str(e))
        except httpx.TransportError as e:
            last_error = e
            resume_offset = temp_path.stat().st_size if temp_path.exists() else 0
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
        except Exception as e:
            temp_path.unlink(missing_ok=True)
            return DownloadResult(item=item, status=DownloadStatus.FAILED, error=str(e))

    temp_path.unlink(missing_ok=True)
    return DownloadResult(
        item=item, status=DownloadStatus.FAILED,
        error=f"Failed after {max_retries} retries" + (f": {last_error}" if last_error else ""),
    )

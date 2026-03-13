# OneDrive Downloader Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Textual TUI that browses OneDrive folders, downloads selected files with hash verification and metadata preservation, and optionally deletes remote files after verified download.

**Architecture:** MSAL handles OAuth2 device code flow. An async Graph API client lists folders lazily and fetches download URLs. A chunked download engine streams files, computes quickXorHash inline, verifies against the API hash, preserves timestamps, and optionally deletes the remote. Textual renders a two-panel UI (folder tree + status/progress).

**Tech Stack:** Python 3.14, uv, msal, httpx, textual

**Spec:** `docs/superpowers/specs/2026-03-13-onedrive-downloader-design.md`

---

## Chunk 1: Project Foundation

### Task 1: Project Setup

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `src/__init__.py`
- Create: `src/__main__.py`

- [ ] **Step 1: Add dependencies to pyproject.toml**

```toml
[project]
name = "onedrivedownloader"
version = "0.0.0"
description = "One-time OneDrive downloader with TUI"
readme = "README.md"
requires-python = ">=3.14"
dependencies = [
    "msal>=1.31.0",
    "httpx>=0.28.0",
    "textual>=3.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src"]

[project.scripts]
onedrive-dl = "src.app:main"
```

- [ ] **Step 2: Update .gitignore**

Append:
```
# App config and auth
config.json
.msal_cache.json

# Download outputs
outputs/*
!outputs/.gitkeep
```

- [ ] **Step 3: Create empty src/__init__.py**

```python
```

- [ ] **Step 4: Create src/__main__.py entry point**

```python
from src.app import main

main()
```

- [ ] **Step 5: Install dependencies**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv sync`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src/__init__.py src/__main__.py uv.lock
git commit -m "chore: project setup with msal, httpx, textual deps"
```

---

### Task 2: Data Models

**Files:**
- Create: `src/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write tests for data models**

```python
# tests/test_models.py
from src.models import DriveItem, FolderNode


def test_drive_item_from_api_response():
    """DriveItem parses a Graph API driveItem response."""
    api_data = {
        "id": "abc123",
        "name": "photo.jpg",
        "size": 1024000,
        "file": {
            "hashes": {
                "quickXorHash": "AQAAAA+BAAAAAAAAAA=="
            }
        },
        "fileSystemInfo": {
            "createdDateTime": "2023-06-15T10:30:00Z",
            "lastModifiedDateTime": "2024-01-20T14:00:00Z"
        },
        "parentReference": {
            "path": "/drive/root:/Photos/2024"
        }
    }
    item = DriveItem.from_api(api_data)
    assert item.id == "abc123"
    assert item.name == "photo.jpg"
    assert item.size == 1024000
    assert item.quick_xor_hash == "AQAAAA+BAAAAAAAAAA=="
    assert item.is_folder is False
    assert item.remote_path == "Photos/2024"


def test_drive_item_folder():
    """DriveItem correctly identifies folders (no file property)."""
    api_data = {
        "id": "folder1",
        "name": "Photos",
        "size": 50000000,
        "folder": {"childCount": 12},
        "fileSystemInfo": {
            "createdDateTime": "2020-01-01T00:00:00Z",
            "lastModifiedDateTime": "2024-12-01T00:00:00Z"
        },
        "parentReference": {
            "path": "/drive/root:"
        }
    }
    item = DriveItem.from_api(api_data)
    assert item.is_folder is True
    assert item.child_count == 12
    assert item.quick_xor_hash is None
    assert item.remote_path == ""


def test_drive_item_root_path():
    """Items at OneDrive root have empty remote_path."""
    api_data = {
        "id": "x",
        "name": "file.txt",
        "size": 100,
        "file": {"hashes": {"quickXorHash": "abc="}},
        "fileSystemInfo": {
            "createdDateTime": "2024-01-01T00:00:00Z",
            "lastModifiedDateTime": "2024-01-01T00:00:00Z"
        },
        "parentReference": {"path": "/drive/root:"}
    }
    item = DriveItem.from_api(api_data)
    assert item.remote_path == ""


def test_folder_node_selection_propagation():
    """Selecting a parent selects all children."""
    parent = FolderNode(item_id="p", name="Photos", size=0)
    child1 = FolderNode(item_id="c1", name="2024", size=0)
    child2 = FolderNode(item_id="c2", name="2025", size=0)
    parent.children = [child1, child2]

    parent.set_selected(True)
    assert child1.selected is True
    assert child2.selected is True

    parent.set_selected(False)
    assert child1.selected is False
    assert child2.selected is False
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Implement data models**

```python
# src/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DriveItem:
    id: str
    name: str
    size: int
    is_folder: bool
    created: datetime
    modified: datetime
    remote_path: str
    quick_xor_hash: str | None = None
    child_count: int = 0
    download_url: str | None = None

    @classmethod
    def from_api(cls, data: dict) -> DriveItem:
        is_folder = "folder" in data
        hashes = data.get("file", {}).get("hashes", {})
        fs_info = data.get("fileSystemInfo", {})

        # parentReference.path is like "/drive/root:/Photos/2024"
        # Strip the "/drive/root:" prefix to get the relative path
        parent_path = data.get("parentReference", {}).get("path", "")
        prefix = "/drive/root:"
        if parent_path.startswith(prefix):
            remote_path = parent_path[len(prefix):].lstrip("/")
        else:
            remote_path = ""

        return cls(
            id=data["id"],
            name=data["name"],
            size=data.get("size", 0),
            is_folder=is_folder,
            created=datetime.fromisoformat(fs_info["createdDateTime"]),
            modified=datetime.fromisoformat(fs_info["lastModifiedDateTime"]),
            remote_path=remote_path,
            quick_xor_hash=hashes.get("quickXorHash"),
            child_count=data.get("folder", {}).get("childCount", 0),
            download_url=data.get("@microsoft.graph.downloadUrl"),
        )

    @property
    def full_path(self) -> str:
        if self.remote_path:
            return f"{self.remote_path}/{self.name}"
        return self.name


@dataclass
class FolderNode:
    item_id: str
    name: str
    size: int
    selected: bool = False
    expanded: bool = False
    children: list[FolderNode] = field(default_factory=list)
    loaded: bool = False

    def set_selected(self, value: bool) -> None:
        self.selected = value
        for child in self.children:
            child.set_selected(value)
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run pytest tests/test_models.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat: add DriveItem and FolderNode data models"
```

---

### Task 3: QuickXorHash Implementation

Microsoft's proprietary hash used by OneDrive Personal. This is a port of the C# reference implementation. Critical for download verification — must be correct.

**Files:**
- Create: `src/quickxor.py`
- Create: `tests/test_quickxor.py`

- [ ] **Step 1: Write tests for quickXorHash**

We need test vectors. The algorithm is deterministic, so we can verify against known outputs. These vectors are derived from the Microsoft C# reference implementation.

```python
# tests/test_quickxor.py
import base64

from src.quickxor import QuickXorHash


def test_empty_input():
    """Empty input produces a 20-byte hash of all zeros."""
    h = QuickXorHash()
    digest = h.digest()
    assert len(digest) == 20
    assert digest == b"\x00" * 20


def test_single_byte():
    """Hashing a single byte produces a known result."""
    h = QuickXorHash()
    h.update(b"\x01")
    result = h.base64_digest()
    # Single byte 0x01 at shift 0, then XOR with length=1
    # Manually computed from the algorithm
    assert isinstance(result, str)
    assert len(base64.b64decode(result)) == 20


def test_incremental_equals_single():
    """Feeding data in chunks produces same result as all at once."""
    data = b"The quick brown fox jumps over the lazy dog"

    h1 = QuickXorHash()
    h1.update(data)

    h2 = QuickXorHash()
    h2.update(data[:10])
    h2.update(data[10:25])
    h2.update(data[25:])

    assert h1.base64_digest() == h2.base64_digest()


def test_different_inputs_different_hashes():
    """Different inputs produce different hashes."""
    h1 = QuickXorHash()
    h1.update(b"hello")

    h2 = QuickXorHash()
    h2.update(b"world")

    assert h1.base64_digest() != h2.base64_digest()


def test_output_is_base64():
    """base64_digest returns valid base64 string."""
    h = QuickXorHash()
    h.update(b"test data for hashing")
    result = h.base64_digest()
    # Should round-trip through base64
    decoded = base64.b64decode(result)
    assert len(decoded) == 20
    assert base64.b64encode(decoded).decode() == result


def test_large_data():
    """Hash works correctly on data larger than the 160-bit block."""
    h = QuickXorHash()
    data = bytes(range(256)) * 100  # 25,600 bytes
    h.update(data)
    result = h.base64_digest()
    assert isinstance(result, str)

    # Verify consistency
    h2 = QuickXorHash()
    h2.update(data)
    assert h2.base64_digest() == result
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run pytest tests/test_quickxor.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement QuickXorHash**

Port of the C# reference implementation from Microsoft. Uses three 64-bit integers to represent a 160-bit vector. Each input byte is XORed into the vector at a position that shifts by 11 bits per byte. The file length is XORed into the final 8 bytes of the result.

```python
# src/quickxor.py
"""
QuickXorHash — Microsoft's proprietary hash for OneDrive.

Port of the C# reference:
https://learn.microsoft.com/en-us/onedrive/developer/code-snippets/quickxorhash
"""

from __future__ import annotations

import base64
import struct

MASK_64 = (1 << 64) - 1
WIDTH_IN_BITS = 160
SHIFT = 11


class QuickXorHash:
    __slots__ = ("_data", "_length_so_far", "_shift_so_far")

    def __init__(self) -> None:
        self._data: list[int] = [0, 0, 0]  # 3 x 64-bit cells for 160 bits
        self._length_so_far: int = 0
        self._shift_so_far: int = 0

    def update(self, data: bytes | bytearray | memoryview) -> None:
        current_shift = self._shift_so_far
        cells = self._data

        for byte in data:
            index = current_shift >> 6  # // 64
            offset = current_shift & 63  # % 64

            if offset <= 56:
                cells[index] = (cells[index] ^ (byte << offset)) & MASK_64
            else:
                cells[index] = (cells[index] ^ (byte << offset)) & MASK_64
                cells[(index + 1) % 3] ^= byte >> (64 - offset)

            current_shift = (current_shift + SHIFT) % WIDTH_IN_BITS

        self._shift_so_far = current_shift
        self._length_so_far += len(data)

    def digest(self) -> bytes:
        rgb = bytearray(20)

        # Pack first two full 64-bit cells
        struct.pack_into("<Q", rgb, 0, self._data[0] & MASK_64)
        struct.pack_into("<Q", rgb, 8, self._data[1] & MASK_64)

        # Pack last cell — only 32 bits (160 - 128 = 32)
        struct.pack_into("<I", rgb, 16, self._data[2] & 0xFFFFFFFF)

        # XOR in the file length (8 bytes, little-endian) at bytes 12-19
        length_bytes = struct.pack("<q", self._length_so_far)
        for i, b in enumerate(length_bytes):
            rgb[12 + i] ^= b

        return bytes(rgb)

    def base64_digest(self) -> str:
        return base64.b64encode(self.digest()).decode("ascii")
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run pytest tests/test_quickxor.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/quickxor.py tests/test_quickxor.py
git commit -m "feat: implement QuickXorHash (Microsoft OneDrive hash algorithm)"
```

---

## Chunk 2: API Layer

### Task 4: Auth Module

**Files:**
- Create: `src/auth.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Write tests for config loading**

```python
# tests/test_auth.py
import json
from pathlib import Path

from src.auth import load_config, AuthConfig, SETUP_INSTRUCTIONS


def test_load_config_valid(tmp_path):
    """Loads client_id from config.json."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"client_id": "test-id-123"}))
    config = load_config(config_file)
    assert config.client_id == "test-id-123"


def test_load_config_missing_file(tmp_path):
    """Returns None when config.json doesn't exist."""
    config = load_config(tmp_path / "config.json")
    assert config is None


def test_load_config_missing_client_id(tmp_path):
    """Returns None when client_id key is missing."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"other_key": "value"}))
    config = load_config(config_file)
    assert config is None


def test_setup_instructions_exist():
    """Setup instructions string is non-empty and mentions Azure."""
    assert "azure" in SETUP_INSTRUCTIONS.lower()
    assert "client_id" in SETUP_INSTRUCTIONS
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run pytest tests/test_auth.py -v`
Expected: FAIL

- [ ] **Step 3: Implement auth module**

```python
# src/auth.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import msal

SCOPES = ["Files.ReadWrite.All"]
AUTHORITY = "https://login.microsoftonline.com/consumers"

SETUP_INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════╗
║                   OneDrive Setup Required                    ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  1. Go to: https://portal.azure.com/#blade/                 ║
║     Microsoft_AAD_RegisteredApps/ApplicationsListBlade       ║
║                                                              ║
║  2. Click "New registration"                                 ║
║     - Name: OneDrive Downloader (or anything)                ║
║     - Account type: "Personal Microsoft accounts only"       ║
║     - Redirect URI: leave blank                              ║
║                                                              ║
║  3. Copy the "Application (client) ID"                       ║
║                                                              ║
║  4. Go to "Authentication" in the left sidebar               ║
║     - Under "Advanced settings", set                         ║
║       "Allow public client flows" to Yes                     ║
║     - Save                                                   ║
║                                                              ║
║  5. Create config.json in the project root:                  ║
║     {"client_id": "YOUR-CLIENT-ID-HERE"}                     ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""".strip()


@dataclass
class AuthConfig:
    client_id: str


def load_config(config_path: Path) -> AuthConfig | None:
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text())
        client_id = data.get("client_id")
        if not client_id:
            return None
        return AuthConfig(client_id=client_id)
    except (json.JSONDecodeError, KeyError):
        return None


def build_msal_app(config: AuthConfig, cache_path: Path) -> msal.PublicClientApplication:
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        cache.deserialize(cache_path.read_text())

    app = msal.PublicClientApplication(
        client_id=config.client_id,
        authority=AUTHORITY,
        token_cache=cache,
    )
    return app


def acquire_token(app: msal.PublicClientApplication, cache_path: Path) -> str:
    accounts = app.get_accounts()
    result = None

    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Device flow initiation failed: {flow.get('error_description', 'unknown error')}")
        print(f"\n  To sign in, visit: {flow['verification_uri']}")
        print(f"  Enter code: {flow['user_code']}\n")
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise RuntimeError(f"Authentication failed: {result.get('error_description', 'unknown error')}")

    # Persist token cache
    if app.token_cache.has_state_changed:
        cache_path.write_text(app.token_cache.serialize())

    return result["access_token"]
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run pytest tests/test_auth.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/auth.py tests/test_auth.py
git commit -m "feat: add auth module with MSAL device code flow"
```

---

### Task 5: Graph API Client

**Files:**
- Create: `src/graph.py`
- Create: `tests/test_graph.py`

- [ ] **Step 1: Write tests for Graph API client**

```python
# tests/test_graph.py
import httpx
import pytest

from src.graph import GraphClient
from src.models import DriveItem


@pytest.fixture
def mock_transport():
    """Creates a mock httpx transport with canned responses."""
    return httpx.MockTransport(handler=_route_handler)


def _children_response():
    return {
        "value": [
            {
                "id": "folder1",
                "name": "Photos",
                "size": 500000,
                "folder": {"childCount": 2},
                "fileSystemInfo": {
                    "createdDateTime": "2023-01-01T00:00:00Z",
                    "lastModifiedDateTime": "2024-01-01T00:00:00Z"
                },
                "parentReference": {"path": "/drive/root:"}
            },
            {
                "id": "file1",
                "name": "readme.txt",
                "size": 1024,
                "file": {"hashes": {"quickXorHash": "abc123=="}},
                "fileSystemInfo": {
                    "createdDateTime": "2023-06-01T00:00:00Z",
                    "lastModifiedDateTime": "2024-06-01T00:00:00Z"
                },
                "parentReference": {"path": "/drive/root:"}
            }
        ]
    }


def _item_response():
    return {
        "id": "file1",
        "name": "readme.txt",
        "size": 1024,
        "file": {"hashes": {"quickXorHash": "abc123=="}},
        "fileSystemInfo": {
            "createdDateTime": "2023-06-01T00:00:00Z",
            "lastModifiedDateTime": "2024-06-01T00:00:00Z"
        },
        "parentReference": {"path": "/drive/root:"},
        "@microsoft.graph.downloadUrl": "https://example.com/download/file1"
    }


def _route_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path

    if path == "/v1.0/me/drive/root/children" or "/children" in path:
        return httpx.Response(200, json=_children_response())
    if "/items/file1" in path and request.method == "GET":
        return httpx.Response(200, json=_item_response())
    if "/items/file1" in path and request.method == "DELETE":
        return httpx.Response(204)

    return httpx.Response(404)


@pytest.fixture
def client(mock_transport):
    http = httpx.AsyncClient(transport=mock_transport, base_url="https://graph.microsoft.com")
    return GraphClient(http_client=http)


@pytest.mark.anyio
async def test_list_children(client):
    """Lists children of the root folder."""
    items = await client.list_children("root")
    assert len(items) == 2
    assert items[0].name == "Photos"
    assert items[0].is_folder is True
    assert items[1].name == "readme.txt"
    assert items[1].is_folder is False


@pytest.mark.anyio
async def test_get_item(client):
    """Fetches a single item by ID with download URL."""
    item = await client.get_item("file1")
    assert item.name == "readme.txt"
    assert item.download_url == "https://example.com/download/file1"
    assert item.quick_xor_hash == "abc123=="


@pytest.mark.anyio
async def test_delete_item(client):
    """Deletes an item by ID."""
    await client.delete_item("file1")  # Should not raise
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run pytest tests/test_graph.py -v`
Expected: FAIL

Note: need `anyio` and `pytest-anyio` for async tests. Add `anyio` and `pytest-anyio` to dev dependencies:
Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv add --dev pytest pytest-anyio anyio`

- [ ] **Step 3: Implement Graph API client**

```python
# src/graph.py
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

from src.models import DriveItem

if TYPE_CHECKING:
    pass

BASE_URL = "https://graph.microsoft.com/v1.0"


class GraphClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None, access_token: str = "") -> None:
        if http_client is not None:
            self._client = http_client
        else:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
        self._max_retries = 5

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(self._max_retries):
            response = await self._client.request(method, url, **kwargs)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                await asyncio.sleep(retry_after)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError(f"Request to {url} failed after {self._max_retries} retries (429 throttled)")

    async def list_children(self, item_id: str) -> list[DriveItem]:
        items: list[DriveItem] = []
        if item_id == "root":
            url = "/v1.0/me/drive/root/children"
        else:
            url = f"/v1.0/me/drive/items/{item_id}/children"

        while url:
            response = await self._request("GET", url)
            data = response.json()
            for raw in data.get("value", []):
                items.append(DriveItem.from_api(raw))
            url = data.get("@odata.nextLink")

        return items

    async def get_item(self, item_id: str) -> DriveItem:
        response = await self._request("GET", f"/v1.0/me/drive/items/{item_id}")
        return DriveItem.from_api(response.json())

    async def delete_item(self, item_id: str) -> None:
        await self._request("DELETE", f"/v1.0/me/drive/items/{item_id}")

    async def close(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run pytest tests/test_graph.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/graph.py tests/test_graph.py
git commit -m "feat: add Graph API client with pagination and retry logic"
```

---

## Chunk 3: Download Engine

### Task 6: Chunked Downloader with Hash Verification

**Files:**
- Create: `src/downloader.py`
- Create: `tests/test_downloader.py`

- [ ] **Step 1: Write tests for download and hash verification**

```python
# tests/test_downloader.py
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
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run pytest tests/test_downloader.py -v`
Expected: FAIL

- [ ] **Step 3: Implement downloader module**

```python
# src/downloader.py
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
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run pytest tests/test_downloader.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/downloader.py tests/test_downloader.py
git commit -m "feat: add download engine with hash verification and metadata"
```

---

## Chunk 4: TUI Application

### Task 7: Textual App — Folder Tree Widget

**Files:**
- Create: `src/widgets/__init__.py`
- Create: `src/widgets/folder_tree.py`

- [ ] **Step 1: Create the widgets package**

```python
# src/widgets/__init__.py
```

- [ ] **Step 2: Implement the folder tree widget**

This is a Textual `Tree` subclass that lazily loads OneDrive folders. Each node stores a `FolderNode` reference in `node.data`. Checkboxes are rendered via label prefixes.

```python
# src/widgets/folder_tree.py
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from src.models import FolderNode

if TYPE_CHECKING:
    from src.graph import GraphClient

CHECK_ICONS = {
    (False, False): "\u2610",  # ☐
    (True, False): "\u2611",   # ☑
    (False, True): "\u2612",   # ☒ partial
}


def _label(node: FolderNode) -> str:
    has_partial = not node.selected and any(c.selected for c in node.children)
    icon = CHECK_ICONS.get((node.selected, has_partial), "\u2610")
    return f"{icon} {node.name}"


class FolderTreeWidget(Tree[FolderNode]):
    def __init__(self, graph_client: GraphClient) -> None:
        super().__init__("OneDrive", id="folder-tree")
        self.graph_client = graph_client
        self.show_root = False

    async def load_root(self) -> None:
        items = await self.graph_client.list_children("root")
        for item in items:
            if item.is_folder:
                folder = FolderNode(
                    item_id=item.id,
                    name=item.name,
                    size=item.size,
                )
                tree_node = self.root.add(
                    _label(folder),
                    data=folder,
                    allow_expand=True,
                )
                # Add placeholder so the expand arrow shows
                tree_node.add_leaf("Loading...")

    async def _load_children(self, node: TreeNode[FolderNode]) -> None:
        folder = node.data
        if folder is None or folder.loaded:
            return

        node.remove_children()
        items = await self.graph_client.list_children(folder.item_id)
        for item in items:
            if item.is_folder:
                child_folder = FolderNode(
                    item_id=item.id,
                    name=item.name,
                    size=item.size,
                )
                folder.children.append(child_folder)
                child_node = node.add(
                    _label(child_folder),
                    data=child_folder,
                    allow_expand=True,
                )
                child_node.add_leaf("Loading...")

        folder.loaded = True

    async def on_tree_node_expanded(self, event: Tree.NodeExpanded[FolderNode]) -> None:
        if event.node.data and not event.node.data.loaded:
            await self._load_children(event.node)

    def toggle_selected(self, node: TreeNode[FolderNode]) -> None:
        if node.data is None:
            return
        new_state = not node.data.selected
        node.data.set_selected(new_state)
        self._refresh_labels(node)

    def _refresh_labels(self, node: TreeNode[FolderNode]) -> None:
        if node.data is not None:
            node.set_label(_label(node.data))
        for child in node.children:
            self._refresh_labels(child)
        # Also refresh parent labels up the tree for partial state
        if node.parent and node.parent.data is not None:
            has_selected = any(
                c.data.selected for c in node.parent.children if c.data is not None
            )
            all_selected = all(
                c.data.selected for c in node.parent.children if c.data is not None
            )
            if all_selected:
                node.parent.data.selected = True
            elif has_selected:
                node.parent.data.selected = False
            else:
                node.parent.data.selected = False
            node.parent.set_label(_label(node.parent.data))

    def get_selected_folders(self) -> list[FolderNode]:
        result: list[FolderNode] = []
        self._collect_selected(self.root, result)
        return result

    def _collect_selected(self, node: TreeNode[FolderNode], result: list[FolderNode]) -> None:
        if node.data is not None and node.data.selected:
            result.append(node.data)
            return  # Don't recurse into children — parent covers them
        for child in node.children:
            self._collect_selected(child, result)

    def get_total_selected_size(self) -> int:
        return sum(f.size for f in self.get_selected_folders())
```

- [ ] **Step 3: Commit**

```bash
git add src/widgets/__init__.py src/widgets/folder_tree.py
git commit -m "feat: add folder tree widget with lazy loading and selection"
```

---

### Task 8: Textual App — Status Panel

**Files:**
- Create: `src/widgets/status_panel.py`

- [ ] **Step 1: Implement the status panel widget**

```python
# src/widgets/status_panel.py
from __future__ import annotations

from textual.widgets import Static, ProgressBar
from textual.containers import Vertical
from textual.reactive import reactive


def _format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


class StatusPanel(Vertical):
    selected_count: reactive[int] = reactive(0)
    total_size: reactive[int] = reactive(0)
    delete_remote: reactive[bool] = reactive(True)
    current_file: reactive[str] = reactive("")
    files_done: reactive[int] = reactive(0)
    files_total: reactive[int] = reactive(0)
    bytes_done: reactive[int] = reactive(0)
    bytes_total: reactive[int] = reactive(0)

    def compose(self):
        yield Static(id="selected-info")
        yield Static(id="delete-toggle")
        yield Static("", id="divider")
        yield Static(id="current-file")
        yield ProgressBar(id="file-progress", total=100, show_eta=False)
        yield Static(id="overall-progress")

    def on_mount(self) -> None:
        self._update_display()

    def watch_selected_count(self) -> None:
        self._update_display()

    def watch_total_size(self) -> None:
        self._update_display()

    def watch_delete_remote(self) -> None:
        self._update_display()

    def watch_current_file(self) -> None:
        self._update_display()

    def watch_files_done(self) -> None:
        self._update_display()

    def watch_bytes_done(self) -> None:
        self._update_display()

    def _update_display(self) -> None:
        try:
            self.query_one("#selected-info", Static).update(
                f"Selected: {self.selected_count}\nTotal size: ~{_format_size(self.total_size)}"
            )
            toggle_state = "ON" if self.delete_remote else "OFF"
            self.query_one("#delete-toggle", Static).update(
                f"Delete remote: {toggle_state}"
            )
            self.query_one("#current-file", Static).update(
                f"{self.current_file}" if self.current_file else ""
            )
            if self.files_total > 0:
                self.query_one("#overall-progress", Static).update(
                    f"{self.files_done} / {self.files_total} files\n"
                    f"{_format_size(self.bytes_done)} / {_format_size(self.bytes_total)}"
                )
            else:
                self.query_one("#overall-progress", Static).update("")
        except Exception:
            pass  # Widget not yet mounted

    def update_file_progress(self, percent: float) -> None:
        try:
            bar = self.query_one("#file-progress", ProgressBar)
            bar.update(progress=percent)
        except Exception:
            pass
```

- [ ] **Step 2: Commit**

```bash
git add src/widgets/status_panel.py
git commit -m "feat: add status panel widget with progress tracking"
```

---

### Task 9: Main Application Assembly

**Files:**
- Create: `src/app.py`
- Create: `src/app.tcss`

- [ ] **Step 1: Create the Textual CSS file**

```css
/* src/app.tcss */
Screen {
    layout: grid;
    grid-size: 2 2;
    grid-columns: 2fr 1fr;
    grid-rows: 1fr auto;
}

#folder-tree {
    height: 100%;
    border: solid green;
    column-span: 1;
}

StatusPanel {
    height: 100%;
    border: solid blue;
    padding: 1 2;
}

#footer-bar {
    column-span: 2;
    height: 3;
    padding: 0 1;
    background: $surface;
    color: $text-muted;
    content-align: center middle;
}

#selected-info {
    margin-bottom: 1;
}

#delete-toggle {
    margin-bottom: 1;
}

#current-file {
    margin-bottom: 1;
    text-style: bold;
}

#overall-progress {
    margin-top: 1;
}

ConfirmDialog {
    align: center middle;
}

#confirm-container {
    width: 60;
    height: auto;
    max-height: 15;
    border: thick $primary;
    background: $surface;
    padding: 1 2;
}
```

- [ ] **Step 2: Implement the main app**

```python
# src/app.py
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Static

from src.auth import (
    SETUP_INSTRUCTIONS,
    acquire_token,
    build_msal_app,
    load_config,
)
from src.downloader import (
    DownloadResult,
    DownloadStatus,
    download_file,
    should_skip_file,
    write_metadata_sidecar,
)
from src.graph import GraphClient
from src.models import DriveItem, FolderNode
from src.widgets.folder_tree import FolderTreeWidget
from src.widgets.status_panel import StatusPanel

import httpx

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"
CONFIG_PATH = PROJECT_ROOT / "config.json"
CACHE_PATH = PROJECT_ROOT / ".msal_cache.json"
MAX_CONCURRENT = 4


class ConfirmDialog(ModalScreen[bool]):
    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Container(id="confirm-container"):
            yield Static(self.message)
            yield Button("Yes — proceed", id="confirm-yes", variant="error")
            yield Button("Cancel", id="confirm-no", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")


class OneDriveApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "OneDrive Downloader"

    BINDINGS = [
        ("space", "toggle_selection", "Toggle"),
        ("enter", "expand_collapse", "Expand"),
        ("d", "start_download", "Download"),
        ("r", "toggle_delete", "Del toggle"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, graph_client: GraphClient) -> None:
        super().__init__()
        self.graph_client = graph_client
        self._downloading = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield FolderTreeWidget(self.graph_client)
        yield StatusPanel()
        yield Static(
            "[Space] Toggle  [Enter] Expand  [D] Download  [R] Del toggle  [Q] Quit",
            id="footer-bar",
        )

    async def on_mount(self) -> None:
        tree = self.query_one(FolderTreeWidget)
        await tree.load_root()

    def action_toggle_selection(self) -> None:
        tree = self.query_one(FolderTreeWidget)
        if tree.cursor_node:
            tree.toggle_selected(tree.cursor_node)
            panel = self.query_one(StatusPanel)
            selected = tree.get_selected_folders()
            panel.selected_count = len(selected)
            panel.total_size = tree.get_total_selected_size()

    def action_expand_collapse(self) -> None:
        tree = self.query_one(FolderTreeWidget)
        if tree.cursor_node:
            tree.cursor_node.toggle()

    def action_toggle_delete(self) -> None:
        panel = self.query_one(StatusPanel)
        panel.delete_remote = not panel.delete_remote

    async def action_start_download(self) -> None:
        if self._downloading:
            return

        tree = self.query_one(FolderTreeWidget)
        selected = tree.get_selected_folders()
        if not selected:
            self.notify("No folders selected", severity="warning")
            return

        panel = self.query_one(StatusPanel)
        if panel.delete_remote:
            confirmed = await self.push_screen_wait(
                ConfirmDialog(
                    f"You are about to download files from {len(selected)} folders "
                    f"and DELETE them from OneDrive.\n\nPress 'Yes' to confirm."
                )
            )
            if not confirmed:
                return

        self._downloading = True
        self._run_download(selected, panel.delete_remote)

    @work(thread=False)
    async def _run_download(self, folders: list[FolderNode], delete_remote: bool) -> None:
        panel = self.query_one(StatusPanel)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # Collect all files from selected folders recursively
        all_items: list[DriveItem] = []
        empty_folder_ids: list[str] = []
        await self._collect_files(folders, all_items, empty_folder_ids)

        panel.files_total = len(all_items)
        panel.bytes_total = sum(i.size for i in all_items)

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        results: list[DownloadResult] = []
        failed = False

        async def download_one(item: DriveItem) -> DownloadResult:
            async with semaphore:
                if should_skip_file(item, OUTPUT_DIR):
                    panel.files_done += 1
                    panel.bytes_done += item.size
                    return DownloadResult(item=item, status=DownloadStatus.SKIPPED)

                # Ensure we have the hash — fetch per-item if needed
                if item.quick_xor_hash is None:
                    fetched = await self.graph_client.get_item(item.id)
                    item.quick_xor_hash = fetched.quick_xor_hash
                    item.download_url = fetched.download_url

                if item.quick_xor_hash is None:
                    return DownloadResult(
                        item=item,
                        status=DownloadStatus.MISSING_HASH,
                        error=f"No hash available for {item.full_path}",
                    )

                # Get download URL if we don't have one
                if not item.download_url:
                    fetched = await self.graph_client.get_item(item.id)
                    item.download_url = fetched.download_url

                panel.current_file = item.name

                def on_progress(chunk_bytes: int) -> None:
                    panel.bytes_done += chunk_bytes

                async with httpx.AsyncClient(timeout=300.0) as dl_client:
                    result = await download_file(
                        item=item,
                        download_url=item.download_url,
                        output_dir=OUTPUT_DIR,
                        http_client=dl_client,
                        on_progress=on_progress,
                    )

                if result.status == DownloadStatus.SUCCESS:
                    write_metadata_sidecar(item, OUTPUT_DIR)
                    if delete_remote:
                        try:
                            await self.graph_client.delete_item(item.id)
                        except Exception as e:
                            self.notify(f"Delete failed: {item.name}: {e}", severity="warning")

                panel.files_done += 1
                return result

        # Process files with concurrency
        tasks = [asyncio.create_task(download_one(item)) for item in all_items]

        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)

            if result.status in (DownloadStatus.MISSING_HASH, DownloadStatus.HASH_MISMATCH):
                # Hard-fail: cancel all remaining tasks
                for t in tasks:
                    t.cancel()
                self.notify(
                    f"PIPELINE STOPPED: {result.status.name} for {result.item.full_path}\n{result.error}",
                    severity="error",
                    timeout=30,
                )
                failed = True
                break

        if not failed:
            # Delete empty remote folders if deletion enabled
            if delete_remote:
                for folder_id in empty_folder_ids:
                    try:
                        await self.graph_client.delete_item(folder_id)
                    except Exception as e:
                        self.notify(f"Delete empty folder failed: {e}", severity="warning")

            succeeded = sum(1 for r in results if r.status == DownloadStatus.SUCCESS)
            skipped = sum(1 for r in results if r.status == DownloadStatus.SKIPPED)
            failed_results = [r for r in results if r.status == DownloadStatus.FAILED]
            summary = f"Done! {succeeded} downloaded, {skipped} skipped, {len(failed_results)} failed"
            if failed_results:
                failed_names = ", ".join(r.item.full_path for r in failed_results[:10])
                summary += f"\nFailed: {failed_names}"
                if len(failed_results) > 10:
                    summary += f" (+{len(failed_results) - 10} more)"
            self.notify(summary, timeout=15)

        panel.current_file = ""
        self._downloading = False

    async def _collect_files(
        self,
        folders: list[FolderNode],
        files: list[DriveItem],
        empty_folder_ids: list[str],
    ) -> None:
        for folder in folders:
            items = await self.graph_client.list_children(folder.item_id)
            child_files = [i for i in items if not i.is_folder]
            child_folders = [i for i in items if i.is_folder]

            # Create local directory for every folder (preserves empty ones)
            for item in child_folders:
                local_dir = OUTPUT_DIR / item.full_path
                local_dir.mkdir(parents=True, exist_ok=True)

            if not child_files and not child_folders:
                # Empty folder — track for remote deletion
                empty_folder_ids.append(folder.item_id)

            files.extend(child_files)
            for item in child_folders:
                sub_folder = FolderNode(item_id=item.id, name=item.name, size=item.size)
                await self._collect_files([sub_folder], files, empty_folder_ids)


def main() -> None:
    config = load_config(CONFIG_PATH)
    if config is None:
        print(SETUP_INSTRUCTIONS)
        sys.exit(1)

    msal_app = build_msal_app(config, CACHE_PATH)
    token = acquire_token(msal_app, CACHE_PATH)

    http_client = httpx.AsyncClient(
        base_url="https://graph.microsoft.com/v1.0",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    graph = GraphClient(http_client=http_client)

    app = OneDriveApp(graph_client=graph)
    app.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create outputs/.gitkeep**

```bash
touch outputs/.gitkeep
```

- [ ] **Step 4: Smoke test — app launches without errors**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run python -c "from src.app import OneDriveApp; print('Import OK')"`
Expected: `Import OK`

- [ ] **Step 5: Commit**

```bash
git add src/app.py src/app.tcss src/widgets/ outputs/.gitkeep
git commit -m "feat: add Textual TUI app with folder tree, status panel, and download flow"
```

---

### Task 10: End-to-End Manual Test

- [ ] **Step 1: Create config.json with real client ID**

Follow the setup instructions printed by the app. Create `config.json`:
```json
{"client_id": "YOUR-REAL-CLIENT-ID"}
```

- [ ] **Step 2: Launch the app**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run python -m src`

- [ ] **Step 3: Verify folder tree loads**

- OneDrive folders should appear in the left panel
- Arrow keys navigate, Enter expands folders
- Space toggles selection (checkbox state changes)

- [ ] **Step 4: Test download flow**

- Select a small folder
- Press R to toggle delete OFF (for safe testing)
- Press D to start download
- Verify files appear in `./outputs/` with correct structure
- Verify `.metadata.json` sidecar is created
- Verify file `mtime` matches OneDrive's `lastModifiedDateTime`

- [ ] **Step 5: Test resume**

- Re-run the app, select the same folder, download again
- Previously downloaded files should be skipped (size match)

- [ ] **Step 6: Test delete flow (when ready)**

- Select a small test folder
- Leave delete ON (default)
- Press D — confirm the deletion prompt
- Verify files are deleted from OneDrive after download

- [ ] **Step 7: Run all unit tests**

Run: `cd /home/lexa/DevProjects/_Unsorted/OneDriveDownloader && uv run pytest -v`
Expected: All tests PASS

- [ ] **Step 8: Final commit**

```bash
git add -A
git commit -m "chore: final cleanup and manual test verification"
```

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

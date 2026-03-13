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

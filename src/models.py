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

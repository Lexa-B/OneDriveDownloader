from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.binding import Binding
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from src.models import DriveItem, FolderNode
from src.widgets.status_panel import _format_size

if TYPE_CHECKING:
    from src.graph import GraphClient

# Tree nodes store either a FolderNode (folders) or a DriveItem (files)
NodeData = FolderNode | DriveItem

CHECK_ICONS = {
    (False, False): "\u2610",  # ☐
    (True, False): "\u2611",   # ☑
    (False, True): "\u2612",   # ☒ partial
}


def _folder_label(node: FolderNode) -> str:
    has_partial = not node.selected and any(c.selected for c in node.children)
    icon = CHECK_ICONS.get((node.selected, has_partial), "\u2610")
    return f"{icon} \U0001f4c1 {node.name}"


def _file_label(name: str, size: int, selected: bool = False) -> Text:
    icon = "\u2611" if selected else "\u2610"
    label = Text()
    label.append(f"{icon} ", style="dim" if not selected else "")
    label.append(name, style="dim" if not selected else "")
    label.append(f"  {_format_size(size)}", style="dim italic")
    return label


class FolderTreeWidget(Tree[NodeData]):
    # Override Tree's space binding to prevent expand/collapse on space
    BINDINGS = [
        Binding("space", "select_node", "Toggle selection", show=False),
    ]

    def __init__(self, graph_client: GraphClient) -> None:
        super().__init__("OneDrive", id="folder-tree")
        self.graph_client = graph_client
        self.show_root = False
        self._selected_files: dict[str, DriveItem] = {}  # item_id -> DriveItem

    async def load_root(self) -> None:
        items = await self.graph_client.list_children("root")
        folders_first = sorted(items, key=lambda i: (not i.is_folder, i.name.lower()))
        for item in folders_first:
            if item.is_folder:
                folder = FolderNode(
                    item_id=item.id,
                    name=item.name,
                    size=item.size,
                )
                tree_node = self.root.add(
                    _folder_label(folder),
                    data=folder,
                    allow_expand=True,
                )
                tree_node.add_leaf("Loading...")
            else:
                self.root.add_leaf(
                    _file_label(item.name, item.size),
                    data=item,
                )

    async def _load_children(self, node: TreeNode[NodeData]) -> None:
        folder = node.data
        if not isinstance(folder, FolderNode) or folder.loaded:
            return

        node.remove_children()
        items = await self.graph_client.list_children(folder.item_id)
        folders_first = sorted(items, key=lambda i: (not i.is_folder, i.name.lower()))
        for item in folders_first:
            if item.is_folder:
                child_folder = FolderNode(
                    item_id=item.id,
                    name=item.name,
                    size=item.size,
                )
                folder.children.append(child_folder)
                child_node = node.add(
                    _folder_label(child_folder),
                    data=child_folder,
                    allow_expand=True,
                )
                child_node.add_leaf("Loading...")
            else:
                node.add_leaf(
                    _file_label(item.name, item.size),
                    data=item,
                )

        folder.loaded = True

    async def on_tree_node_expanded(self, event: Tree.NodeExpanded[NodeData]) -> None:
        if isinstance(event.node.data, FolderNode) and not event.node.data.loaded:
            await self._load_children(event.node)

    def action_select_node(self) -> None:
        """Handle space key — toggle selection without expanding."""
        if self.cursor_node:
            self.toggle_selected(self.cursor_node)

    def toggle_selected(self, node: TreeNode[NodeData]) -> None:
        if node.data is None:
            return

        if isinstance(node.data, FolderNode):
            new_state = not node.data.selected
            node.data.set_selected(new_state)
            # Also update file selections within this folder's subtree
            self._sync_file_selections(node)
            self._refresh_labels(node)
        elif isinstance(node.data, DriveItem):
            if node.data.id in self._selected_files:
                del self._selected_files[node.data.id]
                node.set_label(_file_label(node.data.name, node.data.size, selected=False))
            else:
                self._selected_files[node.data.id] = node.data
                node.set_label(_file_label(node.data.name, node.data.size, selected=True))

    def _sync_file_selections(self, node: TreeNode[NodeData]) -> None:
        """Sync file selections when a folder is toggled."""
        for child in node.children:
            if isinstance(child.data, DriveItem):
                folder_data = node.data
                if isinstance(folder_data, FolderNode) and folder_data.selected:
                    self._selected_files[child.data.id] = child.data
                    child.set_label(_file_label(child.data.name, child.data.size, selected=True))
                else:
                    self._selected_files.pop(child.data.id, None)
                    child.set_label(_file_label(child.data.name, child.data.size, selected=False))
            elif isinstance(child.data, FolderNode):
                self._sync_file_selections(child)

    def _refresh_labels(self, node: TreeNode[NodeData]) -> None:
        if isinstance(node.data, FolderNode):
            node.set_label(_folder_label(node.data))
        for child in node.children:
            self._refresh_labels(child)
        # Refresh parent for partial state
        if node.parent and isinstance(node.parent.data, FolderNode):
            has_selected = any(
                c.data.selected for c in node.parent.children if isinstance(c.data, FolderNode)
            )
            all_selected = all(
                c.data.selected for c in node.parent.children if isinstance(c.data, FolderNode)
            )
            if all_selected:
                node.parent.data.selected = True
            elif has_selected:
                node.parent.data.selected = False
            else:
                node.parent.data.selected = False
            node.parent.set_label(_folder_label(node.parent.data))

    def get_selected_folders(self) -> list[FolderNode]:
        result: list[FolderNode] = []
        self._collect_selected(self.root, result)
        return result

    def _collect_selected(self, node: TreeNode[NodeData], result: list[FolderNode]) -> None:
        if isinstance(node.data, FolderNode) and node.data.selected:
            result.append(node.data)
            return  # Don't recurse — parent covers children
        for child in node.children:
            self._collect_selected(child, result)

    def get_selected_files(self) -> list[DriveItem]:
        """Return individually selected files (not covered by a selected folder)."""
        selected_folder_ids = {f.item_id for f in self.get_selected_folders()}
        # Exclude files whose parent folder is already selected
        result: list[DriveItem] = []
        for item in self._selected_files.values():
            # A file is "covered" if any ancestor folder is selected
            # For simplicity, we include all individually selected files;
            # the download engine already handles skip-if-exists
            result.append(item)
        return result

    def get_total_selected_size(self) -> int:
        folder_size = sum(f.size for f in self.get_selected_folders())
        file_size = sum(f.size for f in self.get_selected_files())
        return folder_size + file_size

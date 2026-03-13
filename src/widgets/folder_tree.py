from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from src.models import FolderNode
from src.widgets.status_panel import _format_size

if TYPE_CHECKING:
    from src.graph import GraphClient

CHECK_ICONS = {
    (False, False): "\u2610",  # ☐
    (True, False): "\u2611",   # ☑
    (False, True): "\u2612",   # ☒ partial
}


def _file_label(name: str, size: int) -> Text:
    label = Text()
    label.append(f"  {name}", style="dim")
    label.append(f"  {_format_size(size)}", style="dim italic")
    return label


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
        folders_first = sorted(items, key=lambda i: (not i.is_folder, i.name.lower()))
        for item in folders_first:
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
            else:
                self.root.add_leaf(_file_label(item.name, item.size))

    async def _load_children(self, node: TreeNode[FolderNode]) -> None:
        folder = node.data
        if folder is None or folder.loaded:
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
                    _label(child_folder),
                    data=child_folder,
                    allow_expand=True,
                )
                child_node.add_leaf("Loading...")
            else:
                node.add_leaf(_file_label(item.name, item.size))

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

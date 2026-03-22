from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Static

from src.auth import (
    SETUP_INSTRUCTIONS,
    TokenProvider,
    acquire_token,
    build_msal_app,
    load_config,
)
from src.downloader import (
    DownloadResult,
    DownloadStatus,
    download_file,
    should_skip_file,
    verify_local_file,
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
LOG_PATH = PROJECT_ROOT / "onedrive_downloader.log"
MAX_CONCURRENT = 4
MAX_INFLIGHT_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("onedrive_downloader")


class ErrorDialog(ModalScreen[bool]):
    """Modal for pipeline errors. Returns True to stop, False to skip."""

    def __init__(self, message: str, *, allow_skip: bool = False) -> None:
        super().__init__()
        self.message = message
        self._allow_skip = allow_skip

    def compose(self) -> ComposeResult:
        with Container(id="confirm-container"):
            yield Static(self.message)
            if self._allow_skip:
                yield Button("Skip file — continue downloading", id="error-skip", variant="warning")
                yield Button("Stop — cancel all downloads", id="error-stop", variant="error")
            else:
                yield Button("OK", id="error-stop", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "error-stop")


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
    TITLE = "\U0001f4c2 OneDrive Downloader"

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

    def watch_title(self, title: str) -> None:
        """Set the actual terminal tab title via OSC escape sequence."""
        try:
            os.write(sys.stderr.fileno(), f"\033]0;{title}\007".encode())
        except OSError:
            pass

    def notify(self, message, *, title="", severity="information", timeout=None):
        level = {"error": logging.ERROR, "warning": logging.WARNING}.get(
            severity, logging.INFO
        )
        log.log(level, "%s", message)
        kwargs = {}
        if title:
            kwargs["title"] = title
        if timeout is not None:
            kwargs["timeout"] = timeout
        return super().notify(message, severity=severity, **kwargs)

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
            self._update_selection_status()

    def action_expand_collapse(self) -> None:
        tree = self.query_one(FolderTreeWidget)
        if tree.cursor_node:
            tree.cursor_node.toggle()

    def _update_selection_status(self) -> None:
        tree = self.query_one(FolderTreeWidget)
        panel = self.query_one(StatusPanel)
        folders = tree.get_selected_folders()
        files = tree.get_selected_files()
        panel.selected_count = len(folders) + len(files)
        panel.total_size = tree.get_total_selected_size()

    def _update_download_title(self, panel: StatusPanel) -> None:
        if panel.files_total > 0:
            pct = panel.files_done * 100 // panel.files_total
            self.title = f"\u2b07 {pct}% ({panel.files_done}/{panel.files_total}) \u2014 OneDrive Downloader"

    def action_toggle_delete(self) -> None:
        panel = self.query_one(StatusPanel)
        panel.delete_remote = not panel.delete_remote

    def action_start_download(self) -> None:
        if self._downloading:
            return

        tree = self.query_one(FolderTreeWidget)
        selected_folders = tree.get_selected_folders()
        selected_files = tree.get_selected_files()
        if not selected_folders and not selected_files:
            self.notify("Nothing selected", severity="warning")
            return

        panel = self.query_one(StatusPanel)
        if panel.delete_remote:
            count = len(selected_folders) + len(selected_files)

            def on_confirm(confirmed: bool) -> None:
                if confirmed:
                    self._downloading = True
                    self._run_download(selected_folders, selected_files, True)
                else:
                    self.title = "\U0001f4c2 OneDrive Downloader"

            self.title = "\u2753 Confirm \u2014 OneDrive Downloader"
            self.push_screen(
                ConfirmDialog(
                    f"You are about to download {count} item(s) "
                    f"and DELETE them from OneDrive.\n\nPress 'Yes' to confirm."
                ),
                callback=on_confirm,
            )
        else:
            self._downloading = True
            self._run_download(selected_folders, selected_files, False)

    @work(thread=False)
    async def _run_download(
        self, folders: list[FolderNode], individual_files: list[DriveItem], delete_remote: bool
    ) -> None:
        panel = self.query_one(StatusPanel)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # Prevent system sleep during download
        caffeine = None
        if shutil.which("systemd-inhibit"):
            try:
                caffeine = subprocess.Popen(
                    ["systemd-inhibit", "--what=idle:sleep",
                     "--who=OneDrive Downloader", "--why=Downloading files",
                     "sleep", "infinity"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass

        try:
            await self._run_download_inner(folders, individual_files, delete_remote, panel)
        finally:
            if caffeine:
                caffeine.terminate()
                caffeine.wait()

    async def _run_download_inner(
        self,
        folders: list[FolderNode],
        individual_files: list[DriveItem],
        delete_remote: bool,
        panel: StatusPanel,
    ) -> None:
        # Collect all files from selected folders recursively
        all_items: list[DriveItem] = []
        all_folder_ids: list[str] = []
        panel.enum_status = "Enumerating files..."
        self.title = "\U0001f50d Enumerating\u2026"
        await self._collect_files(folders, all_items, all_folder_ids, panel)
        panel.enum_status = ""

        # Add individually selected files (dedup by id)
        seen_ids = {item.id for item in all_items}
        for f in individual_files:
            if f.id not in seen_ids:
                all_items.append(f)
                seen_ids.add(f.id)

        panel.files_total = len(all_items)
        panel.bytes_total = sum(i.size for i in all_items)
        self.title = f"\u2b07 0% (0/{panel.files_total}) \u2014 OneDrive Downloader"

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        inflight_bytes = 0
        inflight_lock = asyncio.Lock()
        inflight_changed = asyncio.Condition(inflight_lock)
        results: list[DownloadResult] = []
        failed = False

        async def download_one(item: DriveItem) -> DownloadResult:
            nonlocal inflight_bytes
            # Wait until adding this file stays under the size cap,
            # OR nothing else is in flight (so a single huge file can proceed)
            async with inflight_changed:
                while inflight_bytes > 0 and inflight_bytes + item.size > MAX_INFLIGHT_BYTES:
                    await inflight_changed.wait()
                inflight_bytes += item.size
            try:
                return await _download_one_impl(item)
            finally:
                async with inflight_changed:
                    inflight_bytes -= item.size
                    inflight_changed.notify_all()

        async def _download_one_impl(item: DriveItem) -> DownloadResult:
            async with semaphore:
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

                # If local file already exists, verify size + hash instead of re-downloading
                if should_skip_file(item, OUTPUT_DIR):
                    result = verify_local_file(item, OUTPUT_DIR)
                    if result.status == DownloadStatus.SKIPPED:
                        # Verified — refresh metadata and delete remote if enabled
                        write_metadata_sidecar(item, OUTPUT_DIR)
                        if delete_remote:
                            try:
                                await self.graph_client.delete_item(item.id)
                            except Exception as e:
                                self.notify(f"Delete failed: {item.name}: {e}", severity="warning")
                    panel.files_done += 1
                    panel.bytes_done += item.size
                    self._update_download_title(panel)
                    return result

                # Always fetch a fresh download URL — cached ones from
                # enumeration expire and cause 401 errors on large batches
                fetched = await self.graph_client.get_item(item.id)
                item.download_url = fetched.download_url

                panel.file_started(item.id, item.name, item.size)

                def on_progress(chunk_bytes: int) -> None:
                    panel.file_progress(item.id, chunk_bytes)

                def on_retry() -> None:
                    panel.file_reset_progress(item.id)

                async def on_refresh_url() -> str:
                    fresh = await self.graph_client.get_item(item.id)
                    return fresh.download_url

                async with httpx.AsyncClient(timeout=300.0) as dl_client:
                    result = await download_file(
                        item=item,
                        download_url=item.download_url,
                        output_dir=OUTPUT_DIR,
                        http_client=dl_client,
                        on_progress=on_progress,
                        on_retry=on_retry,
                        on_refresh_url=on_refresh_url,
                    )

                panel.file_finished(item.id)

                if result.status == DownloadStatus.FAILED:
                    log.error("Download failed: %s: %s", item.full_path, result.error)

                if result.status == DownloadStatus.SUCCESS:
                    write_metadata_sidecar(item, OUTPUT_DIR)
                    if delete_remote:
                        local_path = OUTPUT_DIR / item.full_path
                        if local_path.exists() and local_path.stat().st_size == item.size:
                            try:
                                await self.graph_client.delete_item(item.id)
                            except Exception as e:
                                self.notify(f"Delete failed: {item.name}: {e}", severity="warning")
                        else:
                            self.notify(
                                f"NOT deleting {item.name} — local file missing or wrong size",
                                severity="error",
                                timeout=15,
                            )

                panel.files_done += 1
                panel.bytes_done += item.size
                self._update_download_title(panel)
                return result

        # Process files with concurrency
        tasks = [asyncio.create_task(download_one(item)) for item in all_items]

        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)

            if result.status == DownloadStatus.HASH_MISMATCH:
                # Hard-fail: cancel all remaining tasks
                for t in tasks:
                    t.cancel()
                error_msg = (
                    f"PIPELINE STOPPED: {result.status.name}\n\n"
                    f"File: {result.item.full_path}\n"
                    f"{result.error}"
                )
                log.error("%s", error_msg)
                self.push_screen(ErrorDialog(error_msg))
                failed = True
                break

            if result.status == DownloadStatus.MISSING_HASH:
                if result.item.name.endswith(".one"):
                    reason = (
                        "OneNote files are stored as special containers "
                        "and do not have a hash in the OneDrive API."
                    )
                else:
                    reason = (
                        "The OneDrive API did not return a hash for this file, "
                        "so it cannot be verified after download."
                    )
                error_msg = (
                    f"No hash available\n\n"
                    f"File: {result.item.full_path}\n\n"
                    f"{reason}\n"
                    f"Skip this file or stop all downloads?"
                )
                log.warning("%s", error_msg)
                should_stop = await self.push_screen_wait(
                    ErrorDialog(error_msg, allow_skip=True)
                )
                if should_stop:
                    for t in tasks:
                        t.cancel()
                    failed = True
                    break

        succeeded = sum(1 for r in results if r.status == DownloadStatus.SUCCESS)
        skipped = sum(1 for r in results if r.status == DownloadStatus.SKIPPED)
        failed_results = [r for r in results if r.status == DownloadStatus.FAILED]

        # Delete remote folders bottom-up, but only if every file succeeded
        if not failed and delete_remote and not failed_results:
            for folder_id in reversed(all_folder_ids):
                try:
                    await self.graph_client.delete_item(folder_id)
                except Exception as e:
                    self.notify(f"Delete folder failed: {e}", severity="warning")

        if not failed:
            summary = f"Done! {succeeded} downloaded, {skipped} skipped, {len(failed_results)} failed"
            if failed_results:
                failed_names = ", ".join(r.item.full_path for r in failed_results[:10])
                summary += f"\nFailed: {failed_names}"
                if len(failed_results) > 10:
                    summary += f" (+{len(failed_results) - 10} more)"
            self.notify(summary, timeout=15)

        self._downloading = False

        if failed:
            self.title = "\u274c Failed \u2014 OneDrive Downloader"
        elif failed_results:
            self.title = "\u26a0\ufe0f Done ({} failed) \u2014 OneDrive Downloader".format(len(failed_results))
        else:
            self.title = "\u2705 Done \u2014 OneDrive Downloader"

        tree = self.query_one(FolderTreeWidget)
        await tree.reload()
        panel.selected_count = 0
        panel.total_size = 0

    async def _collect_files(
        self,
        folders: list[FolderNode],
        files: list[DriveItem],
        folder_ids: list[str],
        panel: StatusPanel | None = None,
    ) -> None:
        for folder in folders:
            if panel is not None:
                panel.enum_status = (
                    f"Enumerating: {folder.name}\n"
                    f"{len(files)} files found, {len(folder_ids)} folders scanned"
                )
            items = await self.graph_client.list_children(folder.item_id)
            child_files = [i for i in items if not i.is_folder]
            child_folders = [i for i in items if i.is_folder]

            # Create local directory for every folder (preserves empty ones)
            for item in child_folders:
                local_dir = OUTPUT_DIR / item.full_path
                local_dir.mkdir(parents=True, exist_ok=True)

            files.extend(child_files)
            for item in child_folders:
                sub_folder = FolderNode(item_id=item.id, name=item.name, size=item.size)
                await self._collect_files([sub_folder], files, folder_ids, panel)

            # Add after recursing so children come first (depth-first order)
            folder_ids.append(folder.item_id)


def main() -> None:
    config = load_config(CONFIG_PATH)
    if config is None:
        print(SETUP_INSTRUCTIONS)
        sys.exit(1)

    msal_app = build_msal_app(config, CACHE_PATH)
    token = acquire_token(msal_app, CACHE_PATH)
    token_provider = TokenProvider(msal_app, CACHE_PATH)

    graph = GraphClient(access_token=token, token_provider=token_provider)

    app = OneDriveApp(graph_client=graph)
    app.run()


if __name__ == "__main__":
    main()

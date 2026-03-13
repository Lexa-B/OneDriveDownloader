from __future__ import annotations

from textual.widgets import Static, ProgressBar
from textual.containers import Vertical
from textual.reactive import reactive


def _format_size(size_bytes: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            if unit == "B":
                return f"{int(size_bytes)} {unit}"
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

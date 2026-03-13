from __future__ import annotations

import time

from textual.widgets import Static, ProgressBar
from textual.containers import Vertical
from textual.reactive import reactive


def _format_size(size_bytes: int | float, decimals: int = 1) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            if unit == "B":
                return f"{int(size_bytes)} {unit}"
            return f"{size_bytes:.{decimals}f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.{decimals}f} PB"


def _format_rate(bytes_per_sec: float) -> str:
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    elif bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    else:
        return f"{bytes_per_sec / (1024 * 1024):.2f} MB/s"


def _format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s}s"
    else:
        h, rem = divmod(int(seconds), 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m}m {s}s"


class StatusPanel(Vertical):
    selected_count: reactive[int] = reactive(0)
    total_size: reactive[int] = reactive(0)
    delete_remote: reactive[bool] = reactive(True)
    current_file: reactive[str] = reactive("")
    files_done: reactive[int] = reactive(0)
    files_total: reactive[int] = reactive(0)
    bytes_done: reactive[int] = reactive(0)
    bytes_total: reactive[int] = reactive(0)
    _download_start: float = 0.0

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

    def watch_files_total(self) -> None:
        if self.files_total > 0 and self._download_start == 0.0:
            self._download_start = time.monotonic()
        elif self.files_total == 0:
            self._download_start = 0.0
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
                elapsed = time.monotonic() - self._download_start if self._download_start else 0.0
                rate = self.bytes_done / elapsed if elapsed > 0.5 else 0.0
                remaining = self.bytes_total - self.bytes_done
                eta = remaining / rate if rate > 0 else 0.0
                pct = (self.bytes_done / self.bytes_total * 100) if self.bytes_total else 0.0

                lines = [
                    f"{self.files_done} / {self.files_total} files  ({pct:.1f}%)",
                    f"{_format_size(self.bytes_done, 3)} / {_format_size(self.bytes_total, 3)}",
                ]
                if rate > 0:
                    lines.append(f"{_format_rate(rate)}  —  ETA {_format_eta(eta)}")
                self.query_one("#overall-progress", Static).update("\n".join(lines))
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

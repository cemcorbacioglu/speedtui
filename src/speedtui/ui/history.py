"""History screen: a table of past results from the SQLite file."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from speedtui.storage import History, StorageError


class HistoryScreen(Screen):
    BINDINGS = [("escape,b", "app.pop_screen", "Back")]

    DEFAULT_CSS = """
    HistoryScreen {
        layout: vertical;
        padding: 0 1;
        background: ansi_default;
    }
    HistoryScreen:inline {
        height: 22;
        border: none;
    }
    HistoryScreen #title {
        text-style: bold;
    }
    HistoryScreen #where {
        color: $text-muted;
        margin-bottom: 1;
    }
    HistoryScreen DataTable {
        height: 1fr;
    }
    """

    def __init__(self, history: History) -> None:
        super().__init__()
        self.history = history

    def compose(self) -> ComposeResult:
        yield Static("speedtui · history", id="title")
        yield Static(str(self.history.path), id="where")
        yield DataTable(zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("When", key="when")
        table.add_column("Server", key="server")
        for label in ("Ping ms", "Jitter ms", "Down Mbps", "Up Mbps"):
            table.add_column(label, key=label)
        try:
            entries = self.history.recent()
        except StorageError as exc:
            self.query_one("#where", Static).update(Text(str(exc), style="bold red"))
            return
        if not entries:
            self.query_one("#where", Static).update(
                f"No results yet. They will be saved to {self.history.path}"
            )
        for e in entries:
            table.add_row(
                e.timestamp.astimezone().strftime("%Y-%m-%d %H:%M"),
                e.colo,
                f"{e.ping_ms:8.1f}",
                f"{e.jitter_ms:9.1f}",
                f"{e.download_mbps:9.1f}",
                f"{e.upload_mbps:7.1f}",
            )

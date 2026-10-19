"""Modal shown when a test step fails. Returns "retry" or "quit"."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ErrorScreen(ModalScreen[str]):
    DEFAULT_CSS = """
    ErrorScreen {
        align: center middle;
    }
    ErrorScreen > Vertical {
        width: 64;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }
    ErrorScreen #heading {
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }
    ErrorScreen Horizontal {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }
    ErrorScreen Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("r", "choose('retry')", "Retry"),
        Binding("q,escape", "choose('quit')", "Quit"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("The speed test failed", id="heading")
            yield Static(self.message)
            with Horizontal():
                yield Button("Retry", variant="primary", id="retry")
                yield Button("Quit", id="quit")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id)

    def action_choose(self, choice: str) -> None:
        self.dismiss(choice)

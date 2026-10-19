"""Small block-letter digits, three rows tall, built from half blocks."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

FONT: dict[str, list[str]] = {
    "0": ["█▀█", "█ █", "▀▀▀"],
    "1": ["▄█ ", " █ ", "▀▀▀"],
    "2": ["▀▀█", "█▀▀", "▀▀▀"],
    "3": ["▀▀█", " ▀█", "▀▀▀"],
    "4": ["█ █", "▀▀█", "  ▀"],
    "5": ["█▀▀", "▀▀█", "▀▀▀"],
    "6": ["█▀▀", "█▀█", "▀▀▀"],
    "7": ["▀▀█", "  █", "  ▀"],
    "8": ["█▀█", "█▀█", "▀▀▀"],
    "9": ["█▀█", "▀▀█", "▀▀▀"],
    ".": [" ", " ", "▀"],
    "-": ["   ", "▀▀▀", "   "],
    " ": [" ", " ", " "],
}
HEIGHT = 3


def render_big(value: str) -> list[str]:
    """Return the rows spelling `value` (unknown characters are skipped)."""
    glyphs = [FONT[c] for c in value if c in FONT]
    return [" ".join(g[row] for g in glyphs) for row in range(HEIGHT)]


def format_speed(mbps: float | None) -> str:
    """Short enough for big digits: 92.4, 940, 1234."""
    if mbps is None:
        return "--"
    if mbps < 100:
        return f"{mbps:.1f}"
    return f"{mbps:.0f}"


def format_ms(ms: float | None) -> str:
    if ms is None:
        return "--"
    return f"{ms:.1f}" if ms < 10 else f"{ms:.0f}"


class BigNumber(Static):
    """A number in block digits with a small unit label beside the last row."""

    DEFAULT_CSS = """
    BigNumber {
        height: 3;
        width: auto;
    }
    """

    def __init__(self, value: str = "--", unit: str = "", style: str = "bold", **kwargs) -> None:
        super().__init__(**kwargs)
        self._value = value
        self._unit = unit
        self._style = style

    def on_mount(self) -> None:
        self._redraw()

    def set_value(self, value: str, style: str | None = None) -> None:
        self._value = value
        if style is not None:
            self._style = style
        self._redraw()

    def _redraw(self) -> None:
        text = Text()
        for i, row in enumerate(render_big(self._value)):
            if i:
                text.append("\n")
            text.append(row, style=self._style)
            if i == HEIGHT - 1 and self._unit:
                text.append(f" {self._unit}")
        self.update(text)

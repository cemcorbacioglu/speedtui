"""Speed-over-time chart: thin dotted lines (braille, 2×4 dots per cell), each
labelled where it ends instead of in a legend."""

from __future__ import annotations

import math

from rich.text import Text
from textual.widget import Widget

from speedtui.ui.palette import FAINT, SOFT

# Bit for the dot at (row, col) inside one braille cell.
_DOT = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]]
_AXIS_WIDTH = 5  # "100┤"
_LABEL_ROOM = 10  # columns kept free on the right for "download"


def nice_ceiling(value: float) -> float:
    """Round up to a readable axis maximum: 1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8 × 10^n."""
    if value <= 0:
        return 10.0
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if value <= step * magnitude + 1e-9:
            return step * magnitude
    return 10 * magnitude


def _line(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    """Dot coordinates of a straight segment (Bresenham)."""
    points = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        points.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return points
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


class ThroughputChart(Widget):
    """One dotted line per series on a shared 0..duration time axis."""

    DEFAULT_CSS = """
    ThroughputChart {
        height: 1fr;
        min-height: 4;
    }
    """

    def __init__(self, duration: float, series_styles: dict[str, str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.duration = duration
        self.series_styles = series_styles  # name -> colour; later names draw on top
        self.points: dict[str, list[tuple[float, float]]] = {name: [] for name in series_styles}

    def add_point(self, series: str, seconds: float, mbps: float) -> None:
        self.points[series].append((seconds, mbps))
        self.refresh()

    def render(self) -> Text:
        width, height = self.size.width, self.size.height
        plot_w = width - _AXIS_WIDTH - _LABEL_ROOM
        plot_h = height - 1  # last row: time axis
        if plot_w < 4 or plot_h < 2:
            return Text("")
        peak = max((v for pts in self.points.values() for _, v in pts), default=0.0)
        y_max = nice_ceiling(peak * 1.05)
        dots_w, dots_h = plot_w * 2, plot_h * 4

        bits = [[0] * plot_w for _ in range(plot_h)]
        owner: list[list[str | None]] = [[None] * plot_w for _ in range(plot_h)]
        ends: dict[str, tuple[int, int]] = {}  # series -> (row, col) of its last point
        for name, pts in self.points.items():
            coords = [
                (
                    min(int(t / self.duration * (dots_w - 1)), dots_w - 1),
                    dots_h - 1 - min(int(v / y_max * (dots_h - 1)), dots_h - 1),
                )
                for t, v in pts
            ]
            if not coords:
                continue
            dots = coords[:1]
            for a, b in zip(coords, coords[1:]):
                dots += _line(*a, *b)[1:]
            for x, y in dots:
                row, col = y // 4, x // 2
                bits[row][col] |= _DOT[y % 4][x % 2]
                owner[row][col] = name
            ends[name] = (coords[-1][1] // 4, coords[-1][0] // 2)

        # Direct labels just past each line's end; nudge apart if they collide.
        labels: dict[int, tuple[int, str]] = {}  # row -> (col, series)
        for name, (row, col) in sorted(ends.items(), key=lambda e: e[1][0]):
            while row in labels and row < plot_h - 1:
                row += 1
            labels[row] = (col + 2, name)

        axis_rows = {0: y_max, plot_h // 2: y_max / 2, plot_h - 1: 0.0}
        text = Text()
        for row in range(plot_h):
            if row in axis_rows:
                text.append(f"{axis_rows[row]:>4g}┤", style=SOFT)
            else:
                text.append("    │", style=FAINT)
            line = Text()
            for col in range(plot_w):
                name = owner[row][col]
                line.append(
                    chr(0x2800 + bits[row][col]) if bits[row][col] else " ",
                    style=self.series_styles[name] if name else None,
                )
            line.append(" " * _LABEL_ROOM)
            if row in labels:
                col, name = labels[row]
                label = f" {name}"
                line = line[:col] + Text(label, style=self.series_styles[name]) + line[col + len(label):]
            text.append_text(line[: plot_w + _LABEL_ROOM])
            text.append("\n")

        ticks = [" "] * plot_w
        for frac in (0.0, 0.5, 1.0):
            label = f"{self.duration * frac:g} s"
            pos = min(int(frac * (plot_w - 1)), plot_w - len(label))
            ticks[pos : pos + len(label)] = label
        text.append("    └" + "".join(ticks), style=SOFT)
        return text


class SampleDots(Widget):
    """Every latency sample as one dotted line, scaled to its own min..max."""

    DEFAULT_CSS = """
    SampleDots {
        height: 1fr;
    }
    """

    def __init__(self, style: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.line_style = style
        self.values: list[float] = []

    def show(self, values: list[float]) -> None:
        self.values = list(values)
        self.refresh()

    def render(self) -> Text:
        width, height = self.size.width, self.size.height
        if len(self.values) < 2 or width < 2 or height < 1:
            return Text("")
        dots_w, dots_h = width * 2, height * 4
        low, high = min(self.values), max(self.values)
        span = (high - low) or 1.0
        coords = [
            (
                round(i / (len(self.values) - 1) * (dots_w - 1)),
                dots_h - 1 - round((v - low) / span * (dots_h - 1)),
            )
            for i, v in enumerate(self.values)
        ]
        bits = [[0] * width for _ in range(height)]
        dots = coords[:1]
        for a, b in zip(coords, coords[1:]):
            dots += _line(*a, *b)[1:]
        for x, y in dots:
            bits[y // 4][x // 2] |= _DOT[y % 4][x % 2]
        return Text(
            "\n".join("".join(chr(0x2800 + b) if b else " " for b in row) for row in bits),
            style=self.line_style,
        )

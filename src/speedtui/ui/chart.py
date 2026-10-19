"""Charts drawn with braille dots (2×4 per cell) and eighth blocks.

ThroughputChart  speed over time: dotted lines over tinted areas, a dashed
                 grid, axis ticks and a value chip at the end of each line.
MiniBars         the small bar history inside a speed tile.
SampleDots       the small dotted latency line inside the ping tile.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from rich.style import Style
from rich.text import Text
from textual.widget import Widget

from speedtui.ui.palette import FAINT, GRID, MUTED

# Bit for the dot at (row, col) inside one braille cell.
_DOT = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]]
_BLOCKS = " ▁▂▃▄▅▆▇█"


def nice_ceiling(value: float) -> float:
    """Round up to an axis maximum that splits evenly into four ticks."""
    if value <= 0:
        return 10.0
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 1.2, 1.6, 2, 2.4, 3, 4, 5, 6, 8, 10):
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


def _dotted_path(coords: list[tuple[int, int]], every: int = 2) -> list[tuple[int, int]]:
    """Join points with segments, keeping every n-th dot for a dotted look."""
    path = coords[:1]
    for a, b in zip(coords, coords[1:]):
        path += _line(*a, *b)[1:]
    return path[::every] + path[-1:]


def _blend(hex_a: str, hex_b: str, t: float) -> str:
    a = [int(hex_a[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _fmt_tick(v: float) -> str:
    return f"{v:g}" if v < 1000 else f"{v / 1000:g}k"


class ThroughputChart(Widget):
    """Line chart for several series on a shared 0..x_max axis.

    series: name -> (line colour, area fill colour). Later series are drawn
    in front of earlier ones. Used for speed over time and for ping samples.
    """

    DEFAULT_CSS = """
    ThroughputChart {
        height: 1fr;
        min-height: 6;
    }
    """

    AXIS = 5  # "120 ┤"
    MARGIN = 8  # room on the right for the end-of-line chips

    def __init__(
        self,
        x_max: float,
        series: dict[str, tuple[str, str]],
        x_format: Callable[[float], str] = lambda v: f"{v:g} s",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.x_max = x_max
        self.x_format = x_format
        self.series = series
        self.points: dict[str, list[tuple[float, float]]] = {name: [] for name in series}

    def add_point(self, name: str, seconds: float, mbps: float) -> None:
        self.points[name].append((seconds, mbps))
        self.refresh()

    def render(self) -> Text:
        width, height = self.size.width, self.size.height
        plot_w = width - self.AXIS - self.MARGIN
        plot_h = height - 2  # x axis line + tick labels
        if plot_w < 8 or plot_h < 3:
            return Text("")
        peak = max((v for pts in self.points.values() for _, v in pts), default=0.0)
        y_max = nice_ceiling(peak * 1.08)
        dots_w, dots_h = plot_w * 2, plot_h * 4

        def to_dots(t: float, v: float) -> tuple[int, int]:
            x = min(int(t / self.x_max * (dots_w - 1)), dots_w - 1)
            y = dots_h - 1 - min(int(v / y_max * (dots_h - 1)), dots_h - 1)
            return x, y

        bits = [[0] * plot_w for _ in range(plot_h)]
        dot_owner: list[list[str | None]] = [[None] * plot_w for _ in range(plot_h)]
        fill_owner: list[list[str | None]] = [[None] * plot_w for _ in range(plot_h)]
        ends: dict[str, tuple[int, int, float]] = {}

        for name, pts in self.points.items():
            if not pts:
                continue
            coords = [to_dots(t, v) for t, v in pts]
            # Area: every cell wholly below the line's top in that column.
            tops: dict[int, int] = {}
            for x, y in _dotted_path(coords, every=1):
                col = x // 2
                tops[col] = min(tops.get(col, dots_h), y)
            for col, top_y in tops.items():
                # Tint the line's own cell only when the line sits in its
                # upper half; otherwise the fill would poke out above it.
                first = top_y // 4 if top_y % 4 < 2 else top_y // 4 + 1
                for row in range(first, plot_h):
                    fill_owner[row][col] = name
            for x, y in _dotted_path(coords):
                row, col = y // 4, x // 2
                bits[row][col] |= _DOT[y % 4][x % 2]
                dot_owner[row][col] = name
            last_x, last_y = coords[-1]
            ends[name] = (last_y // 4, last_x // 2, pts[-1][1])

        steps = 4 if plot_h >= 9 else 2  # fewer ticks when short, so labels never touch
        tick_rows = {round(plot_h - 1 - k * (plot_h - 1) / steps): y_max * k / steps for k in range(steps + 1)}
        grid_cols = {round(k * (plot_w - 1) / 4) for k in range(1, 4)}

        # End-of-line chips in the right margin, nudged apart if they collide.
        chips: dict[int, tuple[str, str]] = {}
        for name, (row, col, value) in sorted(ends.items(), key=lambda e: e[1][0]):
            while row in chips and row < plot_h - 1:
                row += 1
            chips[row] = (name, f"{value:.1f}")

        text = Text()
        for row in range(plot_h):
            if row in tick_rows:
                text.append(f"{_fmt_tick(tick_rows[row]):>3} ┤", style=MUTED)
            else:
                text.append("    │", style=FAINT)
            for col in range(plot_w):
                fill = fill_owner[row][col]
                bg = self.series[fill][1] if fill else None
                if bits[row][col]:
                    name = dot_owner[row][col]
                    is_end = name in ends and ends[name][:2] == (row, col)
                    char = "●" if is_end else chr(0x2800 + bits[row][col])
                    text.append(char, style=Style(color=self.series[name][0], bgcolor=bg, bold=is_end))
                elif row in tick_rows and row != plot_h - 1:
                    text.append("╌", style=Style(color=GRID, bgcolor=bg))
                elif col in grid_cols:
                    text.append("┆", style=Style(color=GRID, bgcolor=bg))
                else:
                    text.append(" ", style=Style(bgcolor=bg))
            if row in chips:
                name, label = chips[row]
                line, fill = self.series[name]
                text.append(" ")
                text.append(f" {label} ", style=Style(color=line, bgcolor=fill, bold=True))
            text.append("\n")

        # X axis with ticks at 0, ½ and full duration.
        axis = ["─"] * plot_w
        for frac in (0.0, 0.5, 1.0):
            axis[min(int(frac * (plot_w - 1)), plot_w - 1)] = "┴"
        text.append("    └" + "".join(axis) + "\n", style=FAINT)
        labels = [" "] * plot_w
        for frac in (0.0, 0.5, 1.0):
            label = self.x_format(self.x_max * frac)
            pos = int(frac * (plot_w - 1)) - (len(label) // 2 if frac else 0)
            pos = max(0, min(pos, plot_w - len(label)))
            labels[pos : pos + len(label)] = label
        text.append("     " + "".join(labels), style=MUTED)
        return text


class MiniBars(Widget):
    """Bar history of recent values, older bars dimmer, one gap between bars."""

    DEFAULT_CSS = """
    MiniBars {
        height: 2;
        width: 1fr;
    }
    """

    def __init__(self, color: str, dim: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.color = color
        self.dim = dim
        self.values: list[float] = []

    def add(self, value: float) -> None:
        self.values.append(value)
        self.refresh()

    def render(self) -> Text:
        width, height = self.size.width, self.size.height
        slots = (width + 1) // 2
        if not self.values or slots < 4:  # too narrow to read as a history
            return Text("")
        values = self.values
        if len(values) > slots:  # average into as many buckets as fit
            size = len(values) / slots
            values = [
                sum(values[int(i * size) : int((i + 1) * size)])
                / max(1, int((i + 1) * size) - int(i * size))
                for i in range(slots)
            ]
        top = max(values) or 1.0
        eighths = [round(v / top * height * 8) for v in values]
        pad = width - (len(values) * 2 - 1)
        rows = []
        for row in range(height):
            floor = (height - 1 - row) * 8
            line = Text(" " * max(pad, 0))
            for i, e in enumerate(eighths):
                level = min(max(e - floor, 0), 8)
                shade = _blend(self.dim, self.color, (i + 1) / len(eighths))
                line.append(_BLOCKS[level], style=shade)
                if i < len(eighths) - 1:
                    line.append(" ")
            rows.append(line)
        return Text("\n").join(rows)


class SampleDots(Widget):
    """Every latency sample as one dotted line, scaled to its own min..max."""

    DEFAULT_CSS = """
    SampleDots {
        height: 2;
        width: 1fr;
    }
    """

    def __init__(self, color: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.color = color
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
        for x, y in _dotted_path(coords):
            bits[y // 4][x // 2] |= _DOT[y % 4][x % 2]
        return Text(
            "\n".join("".join(chr(0x2800 + b) if b else " " for b in row) for row in bits),
            style=self.color,
        )

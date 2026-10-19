"""A compact Pong rally between you and the Cloudflare server, shown while
latency is measured.

Each leg of the rally picks up the latest ping sample: higher latency means a
slower ball. The jitter measured so far sets how much the ball's path wobbles,
so a steady connection plays straight shots and a jittery one curves about.
"""

from __future__ import annotations

import math
import random
import time

from rich.text import Text
from textual.widget import Widget

from speedtui.ui.palette import BRIGHT, FAINT, INK, SOFT

# Bit for the dot at (row, col) inside one braille cell.
_DOT = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]]

IDLE_LATENCY_MS = 60.0  # ball speed before the first sample arrives


def leg_seconds(latency_ms: float) -> float:
    """Time for the ball to cross the court once.

    Real pings are far too quick to watch, so latency is scaled up:
    20 ms -> 0.6 s, 100 ms -> 1.9 s, capped at 2.5 s.
    """
    return min(max(0.25 + latency_ms / 60, 0.3), 2.5)


def wobble_rows(jitter_ms: float, court_rows: int) -> float:
    """Amplitude of the ball's sideways wobble, in rows.

    About half a row per ms of jitter, never more than 40% of the court.
    """
    return min(jitter_ms * 0.5, court_rows * 0.4)


def _reflect(y: float, top: float, bottom: float) -> float:
    """Fold a position back into [top, bottom], like bouncing off the walls."""
    span = bottom - top
    if span <= 0:
        return top
    y = (y - top) % (2 * span)
    return top + (y if y <= span else 2 * span - y)


class PongCourt(Widget):
    """The animated court. Feed it latency and jitter; it sizes to its parent."""

    DEFAULT_CSS = """
    PongCourt {
        height: 1fr;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.latency_ms: float | None = None
        self.jitter_ms = 0.0
        self.server_label = "?"
        self._rng = random.Random()
        self._to_server = True
        self._progress = 0.0  # 0..1 along the current leg
        self._leg_seconds = leg_seconds(IDLE_LATENCY_MS)
        self._y_from = 0.5  # normalised 0 (top) .. 1 (bottom)
        self._y_to = 0.5
        self._amplitude = 0.0
        self._waves = 1
        self._paddles = {"you": 0.5, "server": 0.5}
        self._last_tick = time.monotonic()

    def on_mount(self) -> None:
        self._start_leg()
        self._last_tick = time.monotonic()
        self.set_interval(1 / 30, self._tick)

    def _court_rows(self) -> int:
        return max(self.size.height, 2)

    def _start_leg(self) -> None:
        self._y_from = self._y_to
        self._y_to = self._rng.uniform(0.1, 0.9)
        latency = self.latency_ms if self.latency_ms is not None else IDLE_LATENCY_MS
        self._leg_seconds = leg_seconds(latency)
        self._amplitude = wobble_rows(self.jitter_ms, self._court_rows())
        self._amplitude *= self._rng.choice((-1, 1))
        self._waves = self._rng.choice((1, 2, 3))

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last_tick, 0.1)
        self._last_tick = now

        self._progress += dt / self._leg_seconds
        if self._progress >= 1.0:
            self._progress = 0.0
            self._to_server = not self._to_server
            self._start_leg()

        # The receiving paddle moves to meet the ball; the other drifts home.
        receiver, sender = ("server", "you") if self._to_server else ("you", "server")
        ease = min(1.0, dt * 6)
        self._paddles[receiver] += (self._y_to - self._paddles[receiver]) * ease
        self._paddles[sender] += (0.5 - self._paddles[sender]) * min(1.0, dt * 1.5)
        self.refresh()

    def render(self) -> Text:
        """Draw with braille dots (2×4 per cell) so the ball moves smoothly."""
        width, rows = self.size.width, self._court_rows()
        left, right = "you ", f" {self.server_label}"
        court_w = width - len(left) - len(right)
        if court_w < 8:
            return Text("")
        dots_w, dots_h = court_w * 2, rows * 4
        bits = [[0] * court_w for _ in range(rows)]
        layer = [[0] * court_w for _ in range(rows)]  # 1 net, 2 paddle, 3 ball

        def dot(x: int, y: int, kind: int) -> None:
            if 0 <= x < dots_w and 0 <= y < dots_h:
                row, col = y // 4, x // 2
                bits[row][col] |= _DOT[y % 4][x % 2]
                layer[row][col] = max(layer[row][col], kind)

        for y in range(0, dots_h, 3):
            dot(dots_w // 2, y, 1)
        paddle_h = max(3, dots_h // 3)
        for name, x in (("you", 0), ("server", dots_w - 1)):
            top = round(self._paddles[name] * (dots_h - paddle_h))
            for y in range(top, top + paddle_h):
                dot(x, y, 2)

        p = self._progress
        bx = 2 + (dots_w - 6) * (p if self._to_server else 1 - p)
        straight = (self._y_from + (self._y_to - self._y_from) * p) * (dots_h - 2)
        by = straight + self._amplitude * 4 * math.sin(math.pi * self._waves * p)
        bx, by = round(bx), round(_reflect(by, 0, dots_h - 2))
        for dx in (0, 1):
            for dy in (0, 1):
                dot(bx + dx, by + dy, 3)

        styles = {0: None, 1: FAINT, 2: INK, 3: f"bold {BRIGHT}"}
        middle = rows // 2
        text = Text()
        for row in range(rows):
            if row:
                text.append("\n")
            text.append(left if row == middle else " " * len(left), style=SOFT)
            for col in range(court_w):
                b = bits[row][col]
                text.append(chr(0x2800 + b) if b else " ", style=styles[layer[row][col]])
            text.append(right if row == middle else " " * len(right), style=INK)
        return text

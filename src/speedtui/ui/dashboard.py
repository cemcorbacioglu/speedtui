"""The one screen a test runs on, drawn like a line sketch: three metric
tiles, a dotted speed-over-time chart, and a side panel that tracks progress
and then explains the result."""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Footer, Static

from speedtui.comparisons import latency_notes, pick_comparisons
from speedtui.measure import (
    Direction,
    LatencyResult,
    ServerInfo,
    SpeedTestResult,
    TestConfig,
    ThroughputProgress,
    ThroughputResult,
    jitter,
)
from speedtui.storage import History, StorageError
from speedtui.ui.bigdigits import BigNumber, format_ms, format_speed
from speedtui.ui.chart import SampleDots, ThroughputChart
from speedtui.ui.palette import BRIGHT, DOWNLOAD, FAINT, INK, PING, SOFT, UPLOAD
from speedtui.ui.pong import PongCourt

COLORS: dict[str, str] = {"download": DOWNLOAD, "upload": UPLOAD, "ping": PING}
ARROWS = {"download": "↓", "upload": "↑", "ping": "◷"}
RATE_WINDOW_S = 0.6  # instantaneous speed = bytes moved over the last 0.6 s
STEPS = ("server", "latency", "download", "upload")
STEP_LABELS = {"server": "server", "latency": "ping", "download": "download", "upload": "upload"}


# --------------------------------------------------------------------------
# Small drawing widgets
# --------------------------------------------------------------------------


class LineBar(Widget):
    """A one-row progress line: ━━━━━━──────."""

    DEFAULT_CSS = "LineBar { height: 1; }"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fraction = 0.0
        self.style_done = INK

    def set(self, fraction: float, style: str | None = None) -> None:
        self.fraction = max(0.0, min(fraction, 1.0))
        if style:
            self.style_done = style
        self.refresh()

    def render(self) -> Text:
        width = self.size.width
        done = round(self.fraction * width)
        return Text.assemble(("━" * done, self.style_done), ("─" * (width - done), FAINT))


@dataclass
class Row:
    """One line of a Leaders table: marker label ······· value."""

    label: str = ""
    value: str = ""
    marker: str = " "
    style: str = INK  # label and marker
    value_style: str = SOFT
    heading: bool = False  # a plain line of text, no leaders


class Leaders(Widget):
    """Rows joined by dotted leaders, laid out to the widget's width."""

    DEFAULT_CSS = "Leaders { height: 1fr; }"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rows: list[Row] = []

    def show(self, rows: list[Row]) -> None:
        self.rows = rows
        self.refresh()

    def render(self) -> Text:
        width = self.size.width
        text = Text()
        for i, row in enumerate(self.rows):
            if i:
                text.append("\n")
            if row.heading or not row.value:
                text.append(f"{row.marker} " if row.marker.strip() else "", style=row.style)
                text.append(row.label, style=row.style)
                continue
            head = f"{row.marker} {row.label} "
            tail = f" {row.value}"
            dots = max(width - len(head) - len(tail), 1)
            text.append(head, style=row.style)
            text.append("·" * dots, style=FAINT)
            text.append(tail, style=row.value_style)
        return text


# --------------------------------------------------------------------------
# Tiles
# --------------------------------------------------------------------------


class SpeedTile(Vertical):
    """Download or upload: number, peak and volume, progress line."""

    def __init__(self, direction: Direction) -> None:
        super().__init__(id=direction, classes="tile")
        self.direction = direction
        self.border_title = f"{ARROWS[direction]} {direction.capitalize()}"
        self.border_subtitle = "waiting"

    def compose(self) -> ComposeResult:
        yield BigNumber("0.0", "Mbps", style=FAINT)
        yield Static("", classes="sub")
        yield Static("", classes="spacer")
        yield LineBar()

    def update(self, progress: ThroughputProgress, peak: float) -> None:
        if progress.bytes_counted == 0:
            self.border_subtitle = "warming up"
        else:
            self.query_one(BigNumber).set_value(format_speed(progress.mbps), f"bold {COLORS[self.direction]}")
            self.query_one(".sub", Static).update(
                f"peak {peak:.1f} · {progress.bytes_counted / 1e6:,.0f} MB"
            )
            self.border_subtitle = f"{progress.elapsed:.1f} s"
        self.query_one(LineBar).set(progress.fraction, COLORS[self.direction])

    def finish(self, result: ThroughputResult, peak: float) -> None:
        self.query_one(BigNumber).set_value(format_speed(result.mbps), f"bold {COLORS[self.direction]}")
        self.query_one(".sub", Static).update(f"peak {peak:.1f} · {result.bytes_counted / 1e6:,.0f} MB")
        self.query_one(LineBar).set(1.0, SOFT)
        self.border_subtitle = "done"


class PingTile(Vertical):
    """Ping: number, jitter, and Pong while sampling, then every sample."""

    def __init__(self) -> None:
        super().__init__(id="ping", classes="tile")
        self.border_title = "◷ Ping"
        self.border_subtitle = "finding server"
        self.samples: list[float] = []

    def compose(self) -> ComposeResult:
        yield BigNumber("0", "ms", style=FAINT)
        yield Static("", classes="sub")
        with ContentSwitcher(initial="court"):
            yield PongCourt(id="court")
            yield SampleDots(PING, id="samples")

    def set_server(self, server: ServerInfo) -> None:
        self.query_one(PongCourt).server_label = server.colo
        self.border_subtitle = "pinging"

    def add_sample(self, ms: float, total: int) -> None:
        self.samples.append(ms)
        court = self.query_one(PongCourt)
        court.latency_ms = ms
        court.jitter_ms = jitter(self.samples)
        self.query_one(BigNumber).set_value(format_ms(statistics.median(self.samples)), f"bold {PING}")
        self.query_one(".sub", Static).update(f"jitter {jitter(self.samples):.1f} ms")
        self.border_subtitle = f"{len(self.samples)} of {total}"

    def finish(self, result: LatencyResult) -> None:
        court = self.query_one(PongCourt)
        court.latency_ms = result.median_ms
        court.jitter_ms = result.jitter_ms
        self.query_one(BigNumber).set_value(format_ms(result.median_ms), f"bold {PING}")
        self.query_one(".sub", Static).update(f"jitter {result.jitter_ms:.1f} ms")
        self.border_subtitle = f"median of {len(result.samples_ms)}"

    def show_samples(self) -> None:
        """Swap the rally for a graph of every sample once Pong has had its turn."""
        self.query_one(SampleDots).show(self.samples)
        self.query_one(ContentSwitcher).current = "samples"


# --------------------------------------------------------------------------
# Screen
# --------------------------------------------------------------------------


class DashboardScreen(Screen):
    DEFAULT_CSS = f"""
    DashboardScreen {{
        layout: vertical;
        background: ansi_default;
    }}
    DashboardScreen:inline {{
        height: 22;
        border: none;
    }}
    DashboardScreen #header {{
        height: 1;
        padding: 0 1;
    }}
    DashboardScreen #brand {{
        width: 1fr;
    }}
    DashboardScreen #settings {{
        width: auto;
        color: {SOFT};
    }}
    DashboardScreen #tiles {{
        height: 9;
    }}
    DashboardScreen .tile, DashboardScreen #chart-box, DashboardScreen #side {{
        border: round {INK};
        border-title-color: {INK};
        border-title-align: left;
        border-subtitle-color: {SOFT};
        border-subtitle-align: right;
        padding: 0 1;
        background: ansi_default;
    }}
    DashboardScreen .tile {{
        width: 1fr;
        height: 100%;
    }}
    DashboardScreen .tile .sub {{
        color: {SOFT};
        height: 1;
    }}
    DashboardScreen .tile .spacer {{
        height: 1fr;
    }}
    DashboardScreen #ping ContentSwitcher {{
        height: 1fr;
    }}
    DashboardScreen #lower {{
        height: 1fr;
    }}
    DashboardScreen #chart-box {{
        width: 3fr;
    }}
    DashboardScreen #side {{
        width: 2fr;
        min-width: 32;
    }}
    DashboardScreen Footer {{
        background: ansi_default;
    }}
    """

    def __init__(self, config: TestConfig, history: History) -> None:
        super().__init__()
        self.config = config
        self.history = history
        self.step = "server"
        self.step_values: dict[str, str] = {}
        self.recent = self._load_recent()
        self._history: dict[str, list[tuple[float, int]]] = {"download": [], "upload": []}
        self.peaks: dict[str, float] = {"download": 0.0, "upload": 0.0}

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(Text("speedtui", style=f"bold {BRIGHT}"), id="brand")
            yield Static(
                f"{self.config.streams} streams · {self.config.duration:g} s per direction",
                id="settings",
            )
        with Horizontal(id="tiles"):
            yield SpeedTile("download")
            yield SpeedTile("upload")
            yield PingTile()
        with Horizontal(id="lower"):
            with Vertical(id="chart-box"):
                yield ThroughputChart(self.config.duration, {"download": DOWNLOAD, "upload": UPLOAD})
            with Vertical(id="side"):
                yield Leaders()
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#chart-box").border_title = "Speed over time, Mbps"
        self.query_one("#side").border_title = "This run"
        self._render_steps()

    # -- side panel ------------------------------------------------------------

    def _load_recent(self) -> list[Row]:
        """The last few saved results, so this run has something to compare with."""
        try:
            entries = self.history.recent(limit=3)
        except StorageError:
            return []
        if not entries:
            return []
        rows = [Row(heading=True), Row(label="previous runs", style=SOFT, heading=True)]
        for e in entries:
            rows.append(
                Row(
                    label=e.timestamp.astimezone().strftime("%b %d %H:%M"),
                    value=f"↓{e.download_mbps:.0f} ↑{e.upload_mbps:.0f} ◷{e.ping_ms:.0f}",
                    style=SOFT,
                )
            )
        return rows

    def _render_steps(self) -> None:
        current = STEPS.index(self.step) if self.step in STEPS else len(STEPS)
        rows = []
        for i, step in enumerate(STEPS):
            if i < current:
                rows.append(Row(STEP_LABELS[step], self.step_values.get(step, ""), "✓", INK, INK))
            elif i == current:
                rows.append(
                    Row(STEP_LABELS[step], self.step_values.get(step, "…"), "▸", f"bold {BRIGHT}", BRIGHT)
                )
            else:
                rows.append(Row(STEP_LABELS[step], "", "·", FAINT, FAINT, heading=True))
        self.query_one(Leaders).show(rows + self.recent)

    def _advance(self, step: str, value: str = "") -> None:
        self.step = step
        if value:
            self.step_values[step] = value
        self._render_steps()

    # -- events from the measurement -------------------------------------------

    def set_server(self, server: ServerInfo) -> None:
        self.query_one("#brand", Static).update(
            Text.assemble(("speedtui", f"bold {BRIGHT}"), ("   Cloudflare ", SOFT), (server.colo, INK))
        )
        self.step_values["server"] = f"Cloudflare {server.colo}"
        self.query_one(PingTile).set_server(server)
        self._advance("latency")

    def add_sample(self, ms: float) -> None:
        tile = self.query_one(PingTile)
        tile.add_sample(ms, self.config.latency_samples)
        self._advance("latency", f"{len(tile.samples)} of {self.config.latency_samples}")

    def finish_latency(self, result: LatencyResult) -> None:
        self.query_one(PingTile).finish(result)
        self.step_values["latency"] = f"{result.median_ms:.1f} ms"
        self._render_steps()

    def start_direction(self, direction: Direction) -> None:
        if direction == "download":
            self.query_one(PingTile).show_samples()
        self._advance(direction, "starting")

    def update_progress(self, progress: ThroughputProgress) -> None:
        direction = progress.direction
        history = self._history[direction]
        history.append((progress.elapsed, progress.bytes_counted))
        rate = self._instant_rate(history)
        if rate is not None:
            self.peaks[direction] = max(self.peaks[direction], rate)
            self.query_one(ThroughputChart).add_point(direction, progress.elapsed, rate)
        self.query_one(f"#{direction}", SpeedTile).update(progress, self.peaks[direction])
        self.step_values[direction] = f"{progress.elapsed:.1f} / {self.config.duration:g} s"
        self._render_steps()

    def _instant_rate(self, history: list[tuple[float, int]]) -> float | None:
        now, latest = history[-1]
        if latest == 0:
            return None
        counting = [(t, b) for t, b in history if t >= self.config.warmup]
        older = [(t, b) for t, b in counting if now - t >= RATE_WINDOW_S]
        start_t, start_b = older[-1] if older else (self.config.warmup, 0)
        if now - start_t <= 0:
            return None
        return (latest - start_b) * 8 / (now - start_t) / 1e6

    def finish_direction(self, result: ThroughputResult) -> None:
        self.query_one(f"#{result.direction}", SpeedTile).finish(result, self.peaks[result.direction])
        self.step_values[result.direction] = f"{result.mbps:.1f} Mbps"
        self._render_steps()

    def show_results(self, result: SpeedTestResult, saved: bool) -> None:
        when = result.timestamp.astimezone().strftime("%Y-%m-%d %H:%M")
        self.query_one("#settings", Static).update(
            f"{when} · {self.config.streams} streams · {self.config.duration:g} s per direction"
        )
        side = self.query_one("#side")
        side.border_title = "At these speeds, roughly"
        side.border_subtitle = "saved · h for history" if saved else "not saved"
        rows = [
            Row(c.label, c.value, ARROWS[c.topic], INK, COLORS[c.topic])
            for c in pick_comparisons(result.download.mbps, result.upload.mbps)
        ]
        rows.append(Row(heading=True))
        rows += [
            Row(c.label, c.value, ARROWS[c.topic], INK, PING)
            for c in latency_notes(result.latency.median_ms, result.latency.jitter_ms)
        ]
        self.query_one(Leaders).show(rows)

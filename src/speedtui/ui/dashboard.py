"""The one screen a test runs on.

    header      brand, server, settings, running timer
    tiles       download · upload · ping, each with a small graph
    middle      chart (speed over time | ping & jitter) · side panel
    bottom      recent results · server information   (only when tall enough)
    footer      keys · status
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Static

from speedtui.comparisons import latency_notes, load_reference, pick_comparisons
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
from speedtui.storage import History, HistoryEntry, StorageError
from speedtui.ui.bigdigits import BigNumber, format_ms, format_speed
from speedtui.ui.chart import MiniBars, SampleDots, ThroughputChart
from speedtui.ui.palette import (
    BG,
    BLUE,
    BORDER,
    CYAN,
    DOWNLOAD,
    DOWNLOAD_FILL,
    DOWNLOAD_LINE,
    FAINT,
    GREEN,
    GRID,
    ICONS,
    MUTED,
    PING,
    TEXT,
    UPLOAD,
    UPLOAD_FILL,
    UPLOAD_LINE,
)
from speedtui.ui.pong import PongCourt

RATE_WINDOW_S = 0.6  # instantaneous speed = bytes moved over the last 0.6 s
MIN_HEIGHT_FOR_BOTTOM = 38  # rows needed before the bottom panels are shown
AMBER = "#fbbf24"
VERDICT_COLORS = {"good": GREEN, "ok": AMBER, "bad": "#f87171"}
PING_FILL = "#0c1a33"
JITTER_FILL = "#2a1f08"


def keycap(key: str, label: str) -> Text:
    return Text.assemble((f" {key} ", f"bold {TEXT} on {GRID}"), (f" {label}    ", MUTED))


def heading(icon: str, label: str, color: str) -> Text:
    return Text.assemble((f"{ICONS[icon]}  ", color), (label, f"bold {color}"))


# --------------------------------------------------------------------------
# Side panel: headings and icon rows with dotted leaders
# --------------------------------------------------------------------------


@dataclass
class Line:
    kind: str  # "heading", "row" or "divider"
    icon: str = ""
    label: str = ""
    value: str = ""
    color: str = TEXT  # heading colour / label colour
    value_color: str = CYAN


class SidePanel(Widget):
    """Renders Lines to its size; spaces rows out when there is room."""

    DEFAULT_CSS = "SidePanel { height: 1fr; }"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.lines: list[Line] = []

    def show(self, lines: list[Line]) -> None:
        self.lines = lines
        self.refresh()

    def _render_line(self, line: Line, width: int) -> Text:
        if line.kind == "divider":
            return Text("─" * width, style=BORDER)
        if line.kind == "heading":
            return heading(line.icon, line.label, line.color)
        icon = ICONS.get(line.icon, " ")
        head = Text.assemble((f"{icon}  ", MUTED), (f"{line.label} ", line.color))
        if not line.value:
            return head
        tail = Text(f" {line.value}", style=f"bold {line.value_color}")
        dots = max(width - head.cell_len - tail.cell_len, 1)
        return head + Text("." * dots, style=FAINT) + tail

    def render(self) -> Text:
        width, height = self.size.width, self.size.height
        rendered = [self._render_line(line, width) for line in self.lines]

        def spaced(every_row: bool) -> list[Text]:
            out: list[Text] = []
            for i, (line, text) in enumerate(zip(self.lines, rendered)):
                # Blank lines after headings and around dividers, and
                # between rows too when there is room.
                if i and (every_row or line.kind != "row" or self.lines[i - 1].kind == "heading"):
                    out.append(Text(""))
                out.append(text)
            return out

        for candidate in (spaced(True), spaced(False)):
            if len(candidate) <= height:
                return Text("\n").join(candidate)
        return Text("\n").join(rendered)


# --------------------------------------------------------------------------
# Tiles
# --------------------------------------------------------------------------


class Tile(Horizontal):
    """A metric tile: title, big number and detail on the left, a graph on the right."""

    def __init__(self, key: str, title: str, unit: str, color: str) -> None:
        super().__init__(id=key, classes="tile")
        self.key = key
        self.title = title
        self.unit = unit
        self.color = color

    def compose(self) -> ComposeResult:
        with Vertical(classes="info"):
            yield Static(heading(self.key, self.title, self.color), classes="title")
            yield BigNumber("0" if self.key == "ping" else "0.0", self.unit, style=FAINT)
            yield Static("", classes="sub")
        with Vertical(classes="graph"):
            yield Static("", classes="state")
            yield Static("", classes="spacer")
            yield from self.compose_graph()

    def compose_graph(self) -> ComposeResult:
        yield MiniBars(self.color, BORDER)

    def set_state(self, text: str) -> None:
        self.query_one(".state", Static).update(Text(text, style=MUTED, justify="right"))

    def set_number(self, value: str, sub: str) -> None:
        self.query_one(BigNumber).set_value(value, f"bold {TEXT}")
        self.query_one(".sub", Static).update(sub)


class PingTile(Tile):
    def __init__(self) -> None:
        super().__init__("ping", "Ping", "ms", PING)
        self.samples: list[float] = []

    def compose_graph(self) -> ComposeResult:
        with ContentSwitcher(initial="court"):
            yield PongCourt(id="court")
            yield SampleDots(PING, id="samples")


# --------------------------------------------------------------------------
# Screen
# --------------------------------------------------------------------------


class DashboardScreen(Screen):
    BINDINGS = [Binding("t", "toggle_chart", "chart")]

    DEFAULT_CSS = f"""
    DashboardScreen {{
        layout: vertical;
        background: {BG};
        padding: 0 1;
    }}
    DashboardScreen:inline {{
        height: 28;
        border: none;
    }}
    #header, #footer {{
        height: 3;
        border: round {BORDER};
        padding: 0 1;
    }}
    #header Static, #footer Static {{ width: 1fr; }}
    #header #brand {{ width: auto; }}
    .right {{ text-align: right; }}
    #tiles {{ height: 7; }}
    .tile {{
        width: 1fr;
        height: 100%;
        padding: 0 1;
    }}
    #download {{ border: round {DOWNLOAD}; }}
    #upload {{ border: round {UPLOAD}; }}
    #ping {{ border: round {PING}; }}
    .tile .info {{ width: auto; min-width: 26; }}
    .tile .graph {{ width: 1fr; padding-left: 2; }}
    .tile .sub {{ color: {MUTED}; }}
    .tile .state {{ height: 1; }}
    .tile .spacer {{ height: 1fr; }}
    .tile ContentSwitcher {{ height: 2; }}
    #middle {{ height: 1fr; }}
    #chart-panel, #side, #recent, #server {{
        border: round {BORDER};
        padding: 0 1;
        height: 100%;
    }}
    #chart-panel {{ width: 2fr; }}
    #side {{ width: 1fr; min-width: 38; padding: 0 2; }}
    #chart-head {{ height: 1; margin-bottom: 1; }}
    #chart-head #tabs {{ width: 1fr; }}
    #chart-head #legend {{ width: auto; }}
    #charts {{ height: 1fr; }}
    #chart-stats {{ height: 3; }}
    #bottom {{ height: 9; }}
    #recent {{ width: 1fr; }}
    #server {{ width: 1fr; }}
    """

    def __init__(self, config: TestConfig, history: History) -> None:
        super().__init__()
        self.config = config
        self.history = history
        self.started = datetime.now()
        self.t0 = time.monotonic()
        self.finished_after: float | None = None
        self.failed = False
        self.server: ServerInfo | None = None
        self.latency: LatencyResult | None = None
        self.step = "server"
        self.step_values: dict[str, str] = {}
        self.chart_tab = "speed"
        self.rates: dict[str, list[float]] = {"download": [], "upload": []}
        self._progress: dict[str, list[tuple[float, int]]] = {"download": [], "upload": []}
        self.peaks: dict[str, float] = {"download": 0.0, "upload": 0.0}

    # -- layout ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(id="brand")
            yield Static(id="settings", classes="right")
        with Horizontal(id="tiles"):
            yield Tile("download", "Download", "Mbps", DOWNLOAD)
            yield Tile("upload", "Upload", "Mbps", UPLOAD)
            yield PingTile()
        with Horizontal(id="middle"):
            with Vertical(id="chart-panel"):
                with Horizontal(id="chart-head"):
                    yield Static(id="tabs")
                    yield Static(id="legend", classes="right")
                with ContentSwitcher(id="charts", initial="speed-chart"):
                    yield ThroughputChart(
                        self.config.duration,
                        {"download": (DOWNLOAD_LINE, DOWNLOAD_FILL), "upload": (UPLOAD_LINE, UPLOAD_FILL)},
                        id="speed-chart",
                    )
                    yield ThroughputChart(
                        max(self.config.latency_samples - 1, 1),
                        {"ping": (PING, PING_FILL), "jitter": (AMBER, JITTER_FILL)},
                        x_format=lambda v: f"#{v + 1:.0f}",
                        id="ping-chart",
                    )
                yield Static(id="chart-stats", classes="right")
            with Vertical(id="side"):
                yield SidePanel()
        with Horizontal(id="bottom"):
            with Vertical(id="recent"):
                yield Static(heading("history", "Recent results", BLUE))
                yield Static(id="recent-table")
            with Vertical(id="server"):
                yield Static(heading("server-info", "Server information", BLUE))
                yield Static(id="server-table")
        with Horizontal(id="footer"):
            yield Static(
                keycap("r", "run test") + keycap("t", "chart") + keycap("h", "history") + keycap("q", "quit")
            )
            yield Static(id="status", classes="right")

    def on_mount(self) -> None:
        self.query_one("#brand", Static).update(self._brand())
        self._render_header()
        self._render_tabs()
        self._render_stats()
        self._render_progress()
        self._render_recent()
        self._render_server()
        self._set_status("finding the nearest Cloudflare server", running=True)
        self.set_interval(1, self._render_header)

    def on_resize(self, event: Resize) -> None:
        self.query_one("#bottom").display = event.size.height >= MIN_HEIGHT_FOR_BOTTOM

    def action_toggle_chart(self) -> None:
        self.chart_tab = "ping" if self.chart_tab == "speed" else "speed"
        self.query_one("#charts", ContentSwitcher).current = f"{self.chart_tab}-chart"
        self._render_tabs()
        self._render_stats()

    # -- header / footer -----------------------------------------------------------

    def _brand(self) -> Text:
        text = Text.assemble(("∿  ", CYAN), ("speedtui", f"bold {TEXT}"))
        if self.server:
            text.append("  │  ", style=FAINT)
            text.append(f"Cloudflare {self.server.colo}", style=MUTED)
        return text

    def _render_header(self) -> None:
        elapsed = self.finished_after if self.finished_after is not None else time.monotonic() - self.t0
        clock = f"{int(elapsed) // 3600:02d}:{int(elapsed) % 3600 // 60:02d}:{int(elapsed) % 60:02d}"
        if self.failed:
            state = Text.assemble(("● ", "#f87171"), ("Failed", MUTED))
        elif self.finished_after is not None:
            state = Text.assemble(("● ", GREEN), (f"Done in {clock}", GREEN))
        else:
            state = Text.assemble(("● ", GREEN), (f"Running {clock}", GREEN))
        text = Text(
            f"{self.started:%Y-%m-%d %H:%M}  •  {self.config.streams} streams  •  "
            f"{self.config.duration:g} s per direction  •  ",
            style=MUTED,
        )
        self.query_one("#settings", Static).update(text + state)

    def _set_status(self, text: str, running: bool = False) -> None:
        dot = ("● ", AMBER) if running else ("● ", GREEN)
        self.query_one("#status", Static).update(Text.assemble(dot, (text, MUTED)))

    # -- chart panel ---------------------------------------------------------------

    def _render_tabs(self) -> None:
        tabs = Text()
        for key, icon, label, unit in (("speed", "bolt", "Speed over time", "(Mbps)"),
                                       ("ping", "ping", "Ping & jitter", "(ms)")):
            active = key == self.chart_tab
            tabs.append(f"{ICONS[icon]}  ", style=BLUE if active else FAINT)
            tabs.append(label, style=f"bold {TEXT} underline" if active else MUTED)
            tabs.append(f" {unit}", style=MUTED if active else FAINT)
            tabs.append("   │   " if key == "speed" else "", style=FAINT)
        self.query_one("#tabs", Static).update(tabs)
        if self.chart_tab == "speed":
            legend = Text.assemble(("■ ", DOWNLOAD_LINE), ("Download   ", MUTED), ("■ ", UPLOAD_LINE), ("Upload", MUTED))
        else:
            legend = Text.assemble(("■ ", PING), ("Ping   ", MUTED), ("■ ", AMBER), ("Jitter", MUTED))
        self.query_one("#legend", Static).update(legend)

    def _stats_rows(self) -> list[tuple[str, str, list[float]]]:
        if self.chart_tab == "speed":
            return [("Download", DOWNLOAD_LINE, self.rates["download"]), ("Upload", UPLOAD_LINE, self.rates["upload"])]
        samples = self.query_one(PingTile).samples
        jitters = [abs(b - a) for a, b in zip(samples, samples[1:])]
        return [("Ping", PING, samples), ("Jitter", AMBER, jitters)]

    def _render_stats(self) -> None:
        text = Text(f"{'':>12}{'Min':>8}{'Avg':>8}{'Max':>8}", style=MUTED)
        for label, color, values in self._stats_rows():
            text.append("\n")
            text.append("● ", style=color)
            text.append(f"{label:<10}", style=MUTED)
            if values:
                text.append(f"{min(values):>8.1f}{statistics.fmean(values):>8.1f}{max(values):>8.1f}", style=TEXT)
            else:
                text.append(f"{'–':>8}{'–':>8}{'–':>8}", style=FAINT)
        self.query_one("#chart-stats", Static).update(text)

    # -- side panel ----------------------------------------------------------------

    def _render_progress(self) -> None:
        steps = [("server", "server", "Server"), ("latency", "ping", "Ping"),
                 ("download", "download", "Download"), ("upload", "upload", "Upload")]
        order = [s[0] for s in steps]
        current = order.index(self.step) if self.step in order else len(order)
        lines = [Line("heading", "bolt", "This run", color=BLUE)]
        for i, (key, icon, label) in enumerate(steps):
            value = self.step_values.get(key, "")
            if i < current:
                lines.append(Line("row", icon, label, value, TEXT, CYAN))
            elif i == current:
                lines.append(Line("row", icon, label, value or "…", f"bold {TEXT}", TEXT))
            else:
                lines.append(Line("row", icon, label, "", FAINT))
        self.query_one(SidePanel).show(lines)

    def _advance(self, step: str, value: str = "") -> None:
        self.step = step
        if value:
            self.step_values[step] = value
        self._render_progress()

    # -- bottom panels ---------------------------------------------------------------

    def _render_recent(self, highlight_newest: bool = False) -> None:
        try:
            entries: list[HistoryEntry] = self.history.recent(limit=5)
        except StorageError as exc:
            self.query_one("#recent-table", Static).update(Text(str(exc), style="#f87171"))
            return
        cols = f"{'#':<3}{'Time':<11}{'Download':>10}{'Upload':>9}{'Ping':>7}{'Jitter':>8}"
        text = Text(cols, style=MUTED)
        if not entries:
            text.append("\nNo results yet.", style=FAINT)
        for i, e in enumerate(entries, 1):
            row_bg = f" on {'#2a1650'}" if highlight_newest and i == 1 else ""
            text.append("\n")
            text.append(f"{i:<3}{e.timestamp.astimezone():%H:%M:%S}   ", style=f"{TEXT}{row_bg}")
            text.append(f"{e.download_mbps:>10.1f}", style=f"{DOWNLOAD_LINE}{row_bg}")
            text.append(f"{e.upload_mbps:>9.1f}", style=f"{UPLOAD_LINE}{row_bg}")
            text.append(f"{e.ping_ms:>7.0f}", style=f"{PING}{row_bg}")
            text.append(f"{e.jitter_ms:>8.1f}", style=f"{AMBER}{row_bg}")
        self.query_one("#recent-table", Static).update(text)

    def _max_km(self) -> float:
        """Upper bound on fibre distance: light covers the ping as a round trip."""
        assert self.latency is not None
        return self.latency.median_ms * load_reference()["latency"]["fiber_km_per_ms"] / 2

    def _render_server(self) -> None:
        s = self.server
        rows = [
            ("Provider", "Cloudflare"),
            ("Data centre", s.colo if s else "…"),
            ("Your IP", s.ip if s else "…"),
            ("Protocol", ("IPv6" if ":" in s.ip else "IPv4") if s else "…"),
            ("Distance", f"≤ {self._max_km():,.0f} km (from ping)" if self.latency else "…"),
            ("Tested at", f"{self.started:%Y-%m-%d %H:%M:%S}"),
        ]
        text = Text()
        for i, (label, value) in enumerate(rows):
            if i:
                text.append("\n")
            text.append(f"{label:<14}", style=MUTED)
            text.append(value, style=TEXT)
        self.query_one("#server-table", Static).update(text)

    # -- events from the measurement ---------------------------------------------------

    def set_server(self, server: ServerInfo) -> None:
        self.server = server
        self.query_one("#brand", Static).update(self._brand())
        self.step_values["server"] = f"Cloudflare {server.colo}"
        tile = self.query_one(PingTile)
        tile.query_one(PongCourt).server_label = server.colo
        tile.set_state("pinging")
        self._render_server()
        self._advance("latency")
        self._set_status(f"measuring ping to Cloudflare {server.colo}", running=True)

    def add_sample(self, ms: float) -> None:
        tile = self.query_one(PingTile)
        tile.samples.append(ms)
        court = tile.query_one(PongCourt)
        court.latency_ms = ms
        court.jitter_ms = jitter(tile.samples)
        tile.set_number(format_ms(statistics.median(tile.samples)), f"jitter {jitter(tile.samples):.1f} ms")
        progress = f"{len(tile.samples)} of {self.config.latency_samples}"
        tile.set_state(progress)
        chart = self.query_one("#ping-chart", ThroughputChart)
        i = len(tile.samples) - 1
        chart.add_point("ping", i, ms)
        if i:
            chart.add_point("jitter", i, abs(ms - tile.samples[-2]))
        if self.chart_tab == "ping":
            self._render_stats()
        self._advance("latency", progress)

    def finish_latency(self, result: LatencyResult) -> None:
        self.latency = result
        tile = self.query_one(PingTile)
        court = tile.query_one(PongCourt)
        court.latency_ms = result.median_ms
        court.jitter_ms = result.jitter_ms
        tile.set_number(format_ms(result.median_ms), f"jitter {result.jitter_ms:.1f} ms")
        tile.set_state(f"median of {len(result.samples_ms)}")
        self.step_values["latency"] = f"{result.median_ms:.1f} ms"
        self._render_server()
        self._render_progress()

    def start_direction(self, direction: Direction) -> None:
        if direction == "download":
            tile = self.query_one(PingTile)
            tile.query_one(SampleDots).show(tile.samples)
            tile.query_one(ContentSwitcher).current = "samples"
        self.query_one(f"#{direction}", Tile).set_state("warming up")
        self._advance(direction, "starting")
        self._set_status(f"measuring {direction} · {self.config.streams} streams", running=True)

    def update_progress(self, progress: ThroughputProgress) -> None:
        direction = progress.direction
        history = self._progress[direction]
        history.append((progress.elapsed, progress.bytes_counted))
        tile = self.query_one(f"#{direction}", Tile)
        rate = self._instant_rate(history)
        if rate is not None:
            self.rates[direction].append(rate)
            self.peaks[direction] = max(self.peaks[direction], rate)
            self.query_one("#speed-chart", ThroughputChart).add_point(direction, progress.elapsed, rate)
            tile.query_one(MiniBars).add(rate)
            if self.chart_tab == "speed":
                self._render_stats()
        if progress.bytes_counted:
            tile.set_number(
                format_speed(progress.mbps),
                f"peak {self.peaks[direction]:.1f}  •  {progress.bytes_counted / 1e6:,.0f} MB",
            )
            tile.set_state(f"{progress.elapsed:.1f} / {self.config.duration:g} s")
        self.step_values[direction] = f"{progress.elapsed:.1f} / {self.config.duration:g} s"
        self._render_progress()

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
        tile = self.query_one(f"#{result.direction}", Tile)
        tile.set_number(
            format_speed(result.mbps),
            f"peak {self.peaks[result.direction]:.1f}  •  {result.bytes_counted / 1e6:,.0f} MB",
        )
        tile.set_state("")
        self.step_values[result.direction] = f"{result.mbps:.1f} Mbps"
        self._render_progress()

    def show_failed(self) -> None:
        self.failed = True
        self.finished_after = time.monotonic() - self.t0
        self._render_header()
        self.query_one("#status", Static).update(Text.assemble(("● ", "#f87171"), ("test failed", MUTED)))

    def show_results(self, result: SpeedTestResult, saved: bool) -> None:
        self.finished_after = time.monotonic() - self.t0
        self._render_header()
        self.query_one(PingTile).set_state("")
        lines = [Line("heading", "bolt", "At these speeds, roughly", color=BLUE)]
        for c in pick_comparisons(result.download.mbps, result.upload.mbps):
            lines.append(Line("row", c.icon, c.label, c.value, TEXT, CYAN))
        lines += [Line("divider"), Line("heading", "signal", "Connection quality", color=GREEN)]
        for c in latency_notes(result.latency.median_ms, result.latency.jitter_ms):
            lines.append(Line("row", c.icon, c.label, c.value, TEXT, VERDICT_COLORS.get(c.verdict, CYAN)))
        self.query_one(SidePanel).show(lines)
        self._render_recent(highlight_newest=saved)
        self._set_status("saved  •  h for history" if saved else "not saved")

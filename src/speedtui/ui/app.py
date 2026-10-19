"""The Textual app: runs the measurement and feeds the dashboard."""

from __future__ import annotations

import asyncio
import time

from textual import work
from textual.app import App
from textual.binding import Binding

from speedtui.measure import (
    LatencyResult,
    ServerInfo,
    SpeedTestError,
    SpeedTestResult,
    TestConfig,
    ThroughputResult,
)
from speedtui.runner import DoneEvent, ProgressEvent, SampleEvent, ServerEvent, run_step
from speedtui.storage import History, StorageError
from speedtui.ui.dashboard import DashboardScreen
from speedtui.ui.errors import ErrorScreen
from speedtui.ui.history import HistoryScreen
from speedtui.ui.palette import THEME

# The pings themselves finish in well under a second on a good line, so the
# Pong rally stays on screen at least this long before throughput starts.
PONG_MIN_SECONDS = 4.0


class SpeedTuiApp(App):
    TITLE = "speedtui"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("r", "rerun", "Run again"),
        Binding("h", "show_history", "History"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config: TestConfig, history: History | None = None) -> None:
        super().__init__()
        self.config = config
        self.history = history or History()

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = THEME.name
        self.run_speed_test()

    def action_rerun(self) -> None:
        self.run_speed_test()

    def action_show_history(self) -> None:
        if not isinstance(self.screen, HistoryScreen):
            self.push_screen(HistoryScreen(self.history))

    def _on_error_choice(self, choice: str | None) -> None:
        if choice == "retry":
            self.run_speed_test()
        else:
            self.exit()

    @work(exclusive=True)
    async def run_speed_test(self) -> None:
        while len(self.screen_stack) > 1:
            await self.pop_screen()
        dashboard = DashboardScreen(self.config, self.history)
        await self.push_screen(dashboard)
        try:
            server, latency = await self._latency_phase(dashboard)
            down = await self._throughput_phase(dashboard, "download")
            up = await self._throughput_phase(dashboard, "upload")
        except SpeedTestError as exc:
            dashboard.show_failed()
            self.push_screen(ErrorScreen(str(exc)), self._on_error_choice)
            return

        result = SpeedTestResult(server, latency, down, up)
        try:
            self.history.save(result, self.config.duration)
            saved = True
        except StorageError as exc:
            saved = False
            self.notify(str(exc), title="History", severity="warning")
        dashboard.show_results(result, saved)

    async def _latency_phase(self, dashboard: DashboardScreen) -> tuple[ServerInfo, LatencyResult]:
        shown_at = time.monotonic()
        server: ServerInfo | None = None
        latency: LatencyResult | None = None
        async for event in run_step("latency", self.config):
            if isinstance(event, ServerEvent):
                server = event.server
                dashboard.set_server(server)
            elif isinstance(event, SampleEvent):
                dashboard.add_sample(event.ms)
            elif isinstance(event, DoneEvent):
                assert isinstance(event.result, LatencyResult)
                latency = event.result
                dashboard.finish_latency(latency)
        assert server is not None and latency is not None
        await asyncio.sleep(max(0.0, PONG_MIN_SECONDS - (time.monotonic() - shown_at)))
        return server, latency

    async def _throughput_phase(self, dashboard: DashboardScreen, direction: str) -> ThroughputResult:
        dashboard.start_direction(direction)  # type: ignore[arg-type]
        async for event in run_step(direction, self.config):  # type: ignore[arg-type]
            if isinstance(event, ProgressEvent):
                dashboard.update_progress(event.progress)
            elif isinstance(event, DoneEvent):
                assert isinstance(event.result, ThroughputResult)
                dashboard.finish_direction(event.result)
                return event.result
        raise SpeedTestError(f"The {direction} test ended without a result.")

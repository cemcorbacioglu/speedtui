"""Speed measurement against Cloudflare's speed test endpoints.

This module has no UI code. Screens call the async functions here and receive
live updates through plain callbacks, so the measurement method can change
without touching the UI.

Method (modelled loosely on how Ookla's CLI measures):

* Latency: ~20 sequential HEAD requests over one warm keep-alive connection.
  Reported as the median; jitter is the mean absolute difference between
  consecutive samples. Every sample is kept.
* Throughput: N parallel streams (default 6). Each stream loops over
  fixed-size requests (starting at ~10 MB, growing when a request finishes
  quickly) until the time limit. Bytes moved during the first 300 ms warm-up
  are ignored; speed = counted bytes / seconds elapsed since the warm-up ended.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import httpx

BASE_URL = "https://speed.cloudflare.com"
DOWN_URL = f"{BASE_URL}/__down"
UP_URL = f"{BASE_URL}/__up"
TRACE_URL = f"{BASE_URL}/cdn-cgi/trace"

# Cloudflare rejects __down requests somewhere above 50 MB with a 403.
MAX_CHUNK_BYTES = 50_000_000
# When rate-limited (HTTP 429), request sizes are halved down to this floor.
MIN_CHUNK_BYTES = 1_000_000
UPLOAD_BLOCK = 64 * 1024
USER_AGENT = "speedtui/0.1"

Direction = Literal["download", "upload"]


class SpeedTestError(Exception):
    """A test step failed. The message is meant to be shown to the user."""


@dataclass
class TestConfig:
    streams: int = 6
    duration: float = 10.0  # seconds per direction, warm-up included
    warmup: float = 0.3  # seconds ignored at the start of each direction
    latency_samples: int = 20
    chunk_bytes: int = 10_000_000  # starting request size per stream
    update_interval: float = 0.2  # seconds between progress callbacks
    timeout: float = 10.0  # per-request connect/read timeout


@dataclass
class ServerInfo:
    colo: str  # Cloudflare data center (IATA airport code), e.g. "FRA"
    country: str  # the client's country as seen by Cloudflare
    ip: str


@dataclass
class LatencyResult:
    samples_ms: list[float]
    median_ms: float
    jitter_ms: float


@dataclass
class ThroughputProgress:
    direction: Direction
    elapsed: float  # seconds since the direction started
    fraction: float  # 0..1 of the configured duration
    mbps: float  # current average since the warm-up ended
    bytes_counted: int


@dataclass
class ThroughputResult:
    direction: Direction
    mbps: float
    bytes_counted: int
    seconds: float  # measured window (after warm-up)
    streams: int
    errors: int = 0  # requests that failed but were retried


@dataclass
class SpeedTestResult:
    server: ServerInfo
    latency: LatencyResult
    download: ThroughputResult
    upload: ThroughputResult
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def make_client(config: TestConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(config.timeout, connect=config.timeout),
        limits=httpx.Limits(max_connections=config.streams + 2),
        headers={"User-Agent": USER_AGENT},
    )


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return "Could not connect to speed.cloudflare.com. Check your internet connection."
    if isinstance(exc, httpx.TimeoutException):
        return "The connection to speed.cloudflare.com timed out."
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 429:
            return (
                "Cloudflare is rate-limiting this connection (HTTP 429), usually after "
                "many tests in a short time. Wait a few minutes and try again."
            )
        return f"Cloudflare answered with HTTP {exc.response.status_code}."
    return f"Network error: {exc.__class__.__name__}: {exc}"


# --------------------------------------------------------------------------
# Server info
# --------------------------------------------------------------------------


async def fetch_server_info(client: httpx.AsyncClient) -> ServerInfo:
    try:
        response = await client.get(TRACE_URL)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SpeedTestError(_describe(exc)) from exc
    fields = dict(
        line.split("=", 1) for line in response.text.splitlines() if "=" in line
    )
    return ServerInfo(
        colo=fields.get("colo", "?"),
        country=fields.get("loc", "?"),
        ip=fields.get("ip", "?"),
    )


# --------------------------------------------------------------------------
# Latency
# --------------------------------------------------------------------------


def jitter(samples: list[float]) -> float:
    """Mean absolute difference between consecutive samples."""
    if len(samples) < 2:
        return 0.0
    return statistics.fmean(abs(b - a) for a, b in zip(samples, samples[1:]))


_SERVER_TIMING_METRICS = ("cfSpeedEdge", "cfSpeedWorker", "cfRequestDuration")


def server_time_ms(response: httpx.Response) -> float:
    """Processing time Cloudflare reports in its Server-Timing header.

    The __down worker sometimes takes hundreds of ms to answer; that is server
    work, not network latency, so it is subtracted from each sample (Cloudflare's
    own speed test does the same).
    """
    total = 0.0
    for header in response.headers.get_list("server-timing"):
        for metric in header.split(","):
            name, _, params = metric.strip().partition(";")
            if name not in _SERVER_TIMING_METRICS:
                continue
            for param in params.split(";"):
                key, _, value = param.strip().partition("=")
                if key == "dur":
                    try:
                        total += float(value)
                    except ValueError:
                        pass
    return total


async def measure_latency(
    client: httpx.AsyncClient,
    config: TestConfig,
    on_sample: Callable[[float], None] | None = None,
) -> LatencyResult:
    """Time `config.latency_samples` HEAD requests on a warm connection.

    One untimed request runs first so DNS, TCP and TLS setup don't count.
    Each sample is round-trip time minus the server time Cloudflare reports.
    """
    url = f"{DOWN_URL}?bytes=0"
    samples: list[float] = []
    try:
        (await client.head(url)).raise_for_status()
        for _ in range(config.latency_samples):
            start = time.perf_counter()
            response = await client.head(url)
            elapsed_ms = (time.perf_counter() - start) * 1000
            response.raise_for_status()
            sample = max(elapsed_ms - server_time_ms(response), 0.1)
            samples.append(sample)
            if on_sample:
                on_sample(sample)
    except httpx.HTTPError as exc:
        raise SpeedTestError(_describe(exc)) from exc
    return LatencyResult(
        samples_ms=samples,
        median_ms=statistics.median(samples),
        jitter_ms=jitter(samples),
    )


# --------------------------------------------------------------------------
# Throughput
# --------------------------------------------------------------------------


class _Meter:
    """Shared byte counter that ignores everything before the warm-up ends."""

    def __init__(self, start: float, warmup: float) -> None:
        self.start = start
        self.counting_from = start + warmup
        self.bytes = 0
        self.errors = 0
        self.last_error: Exception | None = None
        self.fatal: httpx.HTTPStatusError | None = None
        self.max_chunk = MAX_CHUNK_BYTES  # lowered for every stream on HTTP 429

    def add(self, n: int) -> None:
        if time.perf_counter() >= self.counting_from:
            self.bytes += n

    def mbps(self, now: float) -> float:
        window = now - self.counting_from
        if window <= 0:
            return 0.0
        return self.bytes * 8 / window / 1_000_000


async def _upload_body(size: int, meter: _Meter) -> AsyncIterator[bytes]:
    block = b"0" * UPLOAD_BLOCK
    sent = 0
    while sent < size:
        piece = block if size - sent >= UPLOAD_BLOCK else block[: size - sent]
        yield piece
        # Counted once handed to the socket layer; the OS send buffer makes
        # this run slightly ahead of what has actually left the machine.
        meter.add(len(piece))
        sent += len(piece)


async def _download_once(client: httpx.AsyncClient, size: int, meter: _Meter) -> None:
    async with client.stream("GET", f"{DOWN_URL}?bytes={size}") as response:
        response.raise_for_status()
        async for chunk in response.aiter_raw():
            meter.add(len(chunk))


async def _upload_once(client: httpx.AsyncClient, size: int, meter: _Meter) -> None:
    response = await client.post(
        UP_URL,
        content=_upload_body(size, meter),
        headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)},
    )
    response.raise_for_status()


async def _stream_loop(
    client: httpx.AsyncClient,
    direction: Direction,
    config: TestConfig,
    meter: _Meter,
) -> None:
    once = _download_once if direction == "download" else _upload_once
    size = config.chunk_bytes
    while True:
        size = min(size, meter.max_chunk)
        started = time.perf_counter()
        try:
            await once(client, size, meter)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and size > MIN_CHUNK_BYTES:
                # After heavy use Cloudflare rate-limits only large requests;
                # smaller ones still go through. Lower the cap for all streams.
                meter.max_chunk = max(size // 2, MIN_CHUNK_BYTES)
                meter.errors += 1
                continue
            # The server refused the request; retrying won't help.
            meter.fatal = exc
            return
        except httpx.HTTPError as exc:
            # Dropped or timed-out connection: retry until the time limit.
            meter.errors += 1
            meter.last_error = exc
            await asyncio.sleep(0.2)
            continue
        # Fast request: grow the chunk so per-request overhead stays small.
        if time.perf_counter() - started < 1.0:
            size = min(size * 2, meter.max_chunk)


async def measure_throughput(
    client: httpx.AsyncClient,
    direction: Direction,
    config: TestConfig,
    on_progress: Callable[[ThroughputProgress], None] | None = None,
) -> ThroughputResult:
    """Run `config.streams` parallel transfers for `config.duration` seconds."""
    start = time.perf_counter()
    meter = _Meter(start, config.warmup)
    tasks = [
        asyncio.create_task(_stream_loop(client, direction, config, meter))
        for _ in range(config.streams)
    ]
    deadline = start + config.duration
    try:
        while (now := time.perf_counter()) < deadline and meter.fatal is None:
            await asyncio.sleep(min(config.update_interval, deadline - now))
            now = time.perf_counter()
            if on_progress:
                on_progress(
                    ThroughputProgress(
                        direction=direction,
                        elapsed=now - start,
                        fraction=min((now - start) / config.duration, 1.0),
                        mbps=meter.mbps(now),
                        bytes_counted=meter.bytes,
                    )
                )
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    end = time.perf_counter()
    name = direction.capitalize()
    if meter.fatal is not None:
        raise SpeedTestError(f"{name} test failed. {_describe(meter.fatal)}")
    if meter.bytes == 0:
        if meter.last_error is not None:
            raise SpeedTestError(f"{name} test failed. {_describe(meter.last_error)}")
        raise SpeedTestError(
            f"{name} test moved no data in {config.duration:g} s. "
            "The connection may be down or blocked."
        )
    return ThroughputResult(
        direction=direction,
        mbps=meter.mbps(end),
        bytes_counted=meter.bytes,
        seconds=end - meter.counting_from,
        streams=config.streams,
        errors=meter.errors,
    )


# --------------------------------------------------------------------------
# Full run
# --------------------------------------------------------------------------


async def run_full_test(
    config: TestConfig | None = None,
    on_server: Callable[[ServerInfo], None] | None = None,
    on_latency_sample: Callable[[float], None] | None = None,
    on_latency_done: Callable[[LatencyResult], None] | None = None,
    on_progress: Callable[[ThroughputProgress], None] | None = None,
) -> SpeedTestResult:
    """Server info, latency, download, then upload. Raises SpeedTestError."""
    config = config or TestConfig()
    async with make_client(config) as client:
        server = await fetch_server_info(client)
        if on_server:
            on_server(server)
        latency = await measure_latency(client, config, on_latency_sample)
        if on_latency_done:
            on_latency_done(latency)
        download = await measure_throughput(client, "download", config, on_progress)
        upload = await measure_throughput(client, "upload", config, on_progress)
    return SpeedTestResult(server, latency, download, upload)

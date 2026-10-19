"""Plain-text runner: the measurement without any UI. Useful for scripts and debugging."""

from __future__ import annotations

import asyncio
import sys

from speedtui.measure import (
    LatencyResult,
    ServerInfo,
    SpeedTestError,
    TestConfig,
    ThroughputProgress,
    run_full_test,
)
from speedtui.storage import History, StorageError


def _on_server(server: ServerInfo) -> None:
    print(f"Server:   Cloudflare {server.colo}  (client {server.ip}, {server.country})")


def _on_sample(ms: float) -> None:
    print(f"  ping {ms:7.2f} ms", flush=True)


def _on_latency(result: LatencyResult) -> None:
    print(f"Latency:  {result.median_ms:.1f} ms median, {result.jitter_ms:.1f} ms jitter "
          f"({len(result.samples_ms)} samples)")


def _on_progress(p: ThroughputProgress) -> None:
    bar = "#" * int(p.fraction * 30)
    print(f"\r  {p.direction:<8} [{bar:<30}] {p.mbps:8.1f} Mbps", end="", flush=True)
    if p.fraction >= 1.0:
        print()


def run(config: TestConfig) -> int:
    try:
        result = asyncio.run(run_full_test(
            config,
            on_server=_on_server,
            on_latency_sample=_on_sample,
            on_latency_done=_on_latency,
            on_progress=_on_progress,
        ))
    except SpeedTestError as exc:
        print(f"\nTest failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    for t in (result.download, result.upload):
        print(f"{t.direction.capitalize():<9} {t.mbps:.1f} Mbps  "
              f"({t.bytes_counted / 1e6:.0f} MB in {t.seconds:.1f} s, "
              f"{t.streams} streams, {t.errors} retried requests)")
    history = History()
    try:
        history.save(result, config.duration)
        print(f"Saved to {history.path}")
    except StorageError as exc:
        print(f"Warning: {exc}", file=sys.stderr)
    return 0

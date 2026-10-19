"""Run measurement steps in a child process and stream their events back.

A UI shares the Python interpreter (and its GIL) with anything running in a
thread, and rendering then delays the measurement's network reads: latency
samples taken next to a 30 fps Textual app read ~10 ms high. A separate
process has its own interpreter, so timings match the plain CLI.

The child is this same program started with a hidden flag
(`python -m speedtui --measure-child <step> <config-json>`, or the frozen
executable with the same arguments). It writes one JSON event per line.

Usage:

    async for event in run_step("latency", config):
        ...  # ServerEvent, SampleEvent / ProgressEvent ..., then DoneEvent

Errors in the child arrive as a SpeedTestError raised from the iterator.
Closing the iterator early (e.g. on cancel) kills the child.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any, Literal

from speedtui.measure import (
    LatencyResult,
    ServerInfo,
    SpeedTestError,
    TestConfig,
    ThroughputProgress,
    ThroughputResult,
    fetch_server_info,
    make_client,
    measure_latency,
    measure_throughput,
)

Step = Literal["latency", "download", "upload"]

CHILD_FLAG = "--measure-child"


@dataclass
class ServerEvent:
    server: ServerInfo


@dataclass
class SampleEvent:
    ms: float


@dataclass
class ProgressEvent:
    progress: ThroughputProgress


@dataclass
class DoneEvent:
    result: LatencyResult | ThroughputResult


Event = ServerEvent | SampleEvent | ProgressEvent | DoneEvent


# --------------------------------------------------------------------------
# Child side
# --------------------------------------------------------------------------


def _emit(kind: str, data: Any) -> None:
    sys.stdout.write(json.dumps({"type": kind, "data": data}) + "\n")
    sys.stdout.flush()


async def _run_child(step: Step, config: TestConfig) -> None:
    async with make_client(config) as client:
        if step == "latency":
            _emit("server", asdict(await fetch_server_info(client)))
            result: Any = await measure_latency(
                client, config, lambda ms: _emit("sample", ms)
            )
        else:
            result = await measure_throughput(
                client, step, config, lambda p: _emit("progress", asdict(p))
            )
    _emit("done", asdict(result))


def child_main(argv: list[str]) -> None:
    """Entry point for the child process; argv is [step, config-json]."""
    step = argv[0]
    try:
        asyncio.run(_run_child(step, TestConfig(**json.loads(argv[1]))))  # type: ignore[arg-type]
    except SpeedTestError as exc:
        _emit("error", str(exc))
    except Exception as exc:  # report bugs instead of dying silently
        _emit("error", f"Internal error in the {step} test: {exc!r}")


# --------------------------------------------------------------------------
# Parent side
# --------------------------------------------------------------------------


def _decode(step: Step, kind: str, data: Any) -> Event:
    if kind == "server":
        return ServerEvent(ServerInfo(**data))
    if kind == "sample":
        return SampleEvent(float(data))
    if kind == "progress":
        return ProgressEvent(ThroughputProgress(**data))
    if kind == "done":
        result_type = LatencyResult if step == "latency" else ThroughputResult
        return DoneEvent(result_type(**data))
    raise ValueError(f"unknown event {kind!r}")


def _child_command() -> list[str]:
    if getattr(sys, "frozen", False):  # PyInstaller executable
        return [sys.executable, CHILD_FLAG]
    return [sys.executable, "-m", "speedtui", CHILD_FLAG]


async def run_step(step: Step, config: TestConfig) -> AsyncIterator[Event]:
    process = await asyncio.create_subprocess_exec(
        *_child_command(), step, json.dumps(asdict(config)),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None and process.stderr is not None
    try:
        async for line in process.stdout:
            message = json.loads(line)
            if message["type"] == "error":
                raise SpeedTestError(message["data"])
            event = _decode(step, message["type"], message["data"])
            yield event
            if isinstance(event, DoneEvent):
                return
        stderr = (await process.stderr.read()).decode(errors="replace").strip()
        detail = stderr.splitlines()[-1] if stderr else "no output"
        raise SpeedTestError(f"The {step} test stopped unexpectedly ({detail}).")
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()

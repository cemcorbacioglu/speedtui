"""Turn measured speeds into real-world comparisons.

The reference numbers live in data/comparisons.toml, each with a short note
on where it comes from. Everything here is approximate by design.
"""

from __future__ import annotations

import random
import tomllib
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any


@dataclass
class Comparison:
    text: str  # full sentence
    source: str
    topic: str = ""  # "download", "upload" or "ping"
    label: str = ""  # compact form for tables: label ········ value
    value: str = ""
    icon: str = ""  # icon name, see speedtui.ui.palette.ICONS
    verdict: str = ""  # "good", "ok" or "bad" for quality notes


@cache
def load_reference() -> dict[str, Any]:
    data = resources.files("speedtui").joinpath("data/comparisons.toml").read_text("utf-8")
    return tomllib.loads(data)


def short_duration(seconds: float) -> str:
    if seconds < 1:
        return "< 1 s"
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    if seconds < 48 * 3600:
        hours = seconds / 3600
        return f"{hours:.1f} h" if hours < 10 else f"{hours:.0f} h"
    return f"{seconds / 86400:.1f} days"


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return "under a second"
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} minutes"
    if seconds < 48 * 3600:
        hours = seconds / 3600
        return f"{hours:.1f} hours" if hours < 10 else f"{hours:.0f} hours"
    return f"{seconds / 86400:.1f} days"


def describe(item: dict[str, Any], mbps: float) -> Comparison:
    if item["kind"] == "rate":
        count = int(mbps // item["mbps"])
        if count == 0:
            text = f"Not quite enough for one of {item['name']} (~{item['mbps']:g} Mbps each)"
        else:
            text = f"About {count} {item['name']} at once (~{item['mbps']:g} Mbps each)"
        label, value = item["short"], f"{count} at once"
    else:
        seconds = item["gb"] * 8_000 / mbps  # GB -> megabits
        verb = "Upload" if item["direction"] == "upload" else "Download"
        text = f"{verb} {item['name']} (~{item['gb']:g} GB): {format_duration(seconds)}"
        label, value = f"{item['short']}, {item['gb']:g} GB", short_duration(seconds)
    return Comparison(text, item["source"], item["direction"], label, value, item.get("icon", ""))


def pick_comparisons(
    download_mbps: float,
    upload_mbps: float,
    count: int | None = None,
    rng: random.Random | None = None,
) -> list[Comparison]:
    """3 or 4 random comparisons, always at least one per direction."""
    rng = rng or random.Random()
    count = count or rng.choice((3, 4))
    items = load_reference()["item"]
    downs = [i for i in items if i["direction"] == "download"]
    ups = [i for i in items if i["direction"] == "upload"]
    chosen = [rng.choice(downs), rng.choice(ups)]
    rest = [i for i in items if i not in chosen]
    chosen += rng.sample(rest, min(count - 2, len(rest)))
    chosen.sort(key=lambda i: i["direction"])  # downloads first
    speeds = {"download": download_mbps, "upload": upload_mbps}
    return [describe(i, speeds[i["direction"]]) for i in chosen if speeds[i["direction"]] > 0]


def latency_notes(ping_ms: float, jitter_ms: float) -> list[Comparison]:
    """What the ping means: distance light covers, gaming and video calls."""
    ref = load_reference()["latency"]
    km = ping_ms * ref["fiber_km_per_ms"]
    notes = [
        Comparison(
            f"In {ping_ms:.0f} ms light in fibre travels ~{km:,.0f} km, so the server is "
            f"at most ~{km / 2:,.0f} km of fibre away (the ping is a round trip)",
            ref["fiber_source"],
            "ping",
            "Server within",
            f"{km / 2:,.0f} km",
            "server",
        )
    ]
    tier = next(t for t in ref["gaming"] if ping_ms <= t["max_ms"])
    notes.append(
        Comparison(
            f"Gaming: {tier['verdict']}", ref["gaming_source"], "ping", "Gaming", tier["short"],
            "shield", "good" if tier["max_ms"] <= 60 else "ok" if tier["max_ms"] <= 100 else "bad",
        )
    )

    calls = ref["calls"]
    if ping_ms / 2 > calls["max_one_way_ms"]:
        verdict = "delay is high enough that people will talk over each other"
        short, quality = "talk-over delay", "bad"
    elif jitter_ms > calls["max_jitter_ms"]:
        verdict = f"delay is fine, but {jitter_ms:.0f} ms jitter may cause choppy audio"
        short, quality = "choppy audio", "ok"
    else:
        verdict = "fine; delay and jitter are well within recommended limits"
        short, quality = "fine", "good"
    notes.append(
        Comparison(f"Video calls: {verdict}", calls["source"], "ping", "Video calls", short, "video", quality)
    )
    tier = next(t for t in ref["stability"] if jitter_ms <= t["max_ms"])
    notes.append(
        Comparison(
            f"Stability: {tier['verdict']} ({jitter_ms:.1f} ms jitter)", ref["stability_source"], "ping",
            "Stability (jitter)", tier["verdict"], "gauge",
            "good" if tier["max_ms"] <= 15 else "ok" if tier["max_ms"] <= 30 else "bad",
        )
    )
    return notes

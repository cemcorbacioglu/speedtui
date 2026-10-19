"""Terminal speed test against Cloudflare's speed endpoints."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from speedtui.measure import TestConfig

# How to run a command in each terminal emulator, tried in this order after
# $TERMINAL. The command and its arguments are appended to each list.
TERMINALS: list[list[str]] = [
    ["konsole", "-e"],
    ["alacritty", "-e"],
    ["kitty"],
    ["ghostty", "-e"],
    ["wezterm", "start", "--"],
    ["foot"],
    ["gnome-terminal", "--"],
    ["xfce4-terminal", "-x"],
    ["xterm", "-e"],
]
RELAUNCHED_ENV = "SPEEDTUI_RELAUNCHED"


def _own_command() -> list[str]:
    if getattr(sys, "frozen", False):  # PyInstaller executable
        return [sys.executable]
    return [sys.executable, "-m", "speedtui"]


def ensure_terminal() -> None:
    """Make sure the TUI has a terminal to draw in.

    Started from a file manager or app launcher there is no terminal, and the
    app would run invisibly. Reopen it inside a terminal emulator instead.
    """
    if sys.stdin.isatty() and sys.stdout.isatty():
        return
    graphical = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if graphical and not os.environ.get(RELAUNCHED_ENV):
        candidates = TERMINALS
        if preferred := os.environ.get("TERMINAL"):
            candidates = [[preferred, "-e"], *TERMINALS]
        for launcher in candidates:
            if shutil.which(launcher[0]):
                env = {**os.environ, RELAUNCHED_ENV: "1"}
                subprocess.Popen(
                    [*launcher, *_own_command(), *sys.argv[1:]],
                    env=env,
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                sys.exit(0)
    message = (
        "speedtui needs a terminal. Run it from a terminal window, "
        "or use --cli for plain-text output."
    )
    print(message, file=sys.stderr)
    if graphical and shutil.which("notify-send"):
        subprocess.run(["notify-send", "speedtui", message], check=False)
    sys.exit(2)


def print_history() -> int:
    from speedtui.storage import History, StorageError

    history = History()
    try:
        entries = history.recent()
    except StorageError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not entries:
        print(f"No results yet ({history.path}).")
        return 0
    print(f"{'When':<17} {'Server':<6} {'Ping ms':>8} {'Jitter':>7} {'Down Mbps':>10} {'Up Mbps':>8}")
    for e in entries:
        when = e.timestamp.astimezone().strftime("%Y-%m-%d %H:%M")
        print(f"{when:<17} {e.colo:<6} {e.ping_ms:8.1f} {e.jitter_ms:7.1f} "
              f"{e.download_mbps:10.1f} {e.upload_mbps:8.1f}")
    return 0


def main() -> None:
    # Measurement child process (see speedtui.runner); never typed by users.
    from speedtui.runner import CHILD_FLAG

    if len(sys.argv) > 1 and sys.argv[1] == CHILD_FLAG:
        from speedtui.runner import child_main

        child_main(sys.argv[2:])
        return

    parser = argparse.ArgumentParser(description=__doc__)  # prog = name it was run as
    parser.add_argument("--cli", action="store_true", help="plain-text output, no TUI")
    parser.add_argument("--history", action="store_true", help="print past results and exit")
    parser.add_argument("--fullscreen", action="store_true",
                        help="take over the whole terminal instead of drawing below the prompt")
    parser.add_argument("--streams", type=int, default=6, help="parallel streams (default 6)")
    parser.add_argument("--duration", type=float, default=10.0,
                        help="seconds per direction (default 10)")
    args = parser.parse_args()
    if args.streams < 1 or args.duration <= 0.5:
        parser.error("--streams must be >= 1 and --duration > 0.5")
    config = TestConfig(streams=args.streams, duration=args.duration)

    if args.history:
        sys.exit(print_history())
    if args.cli:
        from speedtui import cli

        sys.exit(cli.run(config))

    ensure_terminal()
    from speedtui.ui.app import SpeedTuiApp

    # Inline: draw below the prompt and leave the final result in the scrollback.
    SpeedTuiApp(config).run(inline=not args.fullscreen, inline_no_clear=True)


if __name__ == "__main__":
    main()

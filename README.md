# speedtui

A terminal internet speed test built with [Textual](https://textual.textualize.io/).
It measures against Cloudflare's public speed test endpoints only; no
Ookla/speedtest.net servers are contacted.

It draws below your prompt like a line sketch: one ink colour, thin rounded
frames, dotted lines, small block digits. When you quit, the final result
stays in your terminal's scrollback. (`--fullscreen` takes over the whole
terminal instead.)

- **Three tiles** with download, upload and ping, live while the test runs,
  plus peak speed, data moved and jitter.
- **Ping as Pong**: while latency is measured, a small dot-drawn Pong rally
  plays in the ping tile; the ball's speed follows your ping and its wobble
  your jitter. Afterwards the tile plots every latency sample.
- **Speed over time**: a live dotted chart of download and upload speed.
- **Side panel**: progress and your previous runs during the test; afterwards,
  real-world comparisons (how long a 4K movie or a big game would take, what
  your ping means for gaming and video calls), all approximate.
- **History**: every result is saved to a local SQLite file (`h` to view).

## Run it

### Prebuilt executable

```sh
./dist/speedtui            # full TUI
./dist/speedtui --cli      # plain text output
./dist/speedtui --history  # print past results
```

The executable is self-contained (Python included) and built for Linux x86-64.

### Install as a command

Copy the executable onto your `PATH` under whatever name you want to type.
`~/.local/bin` is on the PATH by default on most distributions (including
CachyOS with fish):

```sh
install -m 755 dist/speedtui ~/.local/bin/speedtui
speedtui
```

To update, rebuild with `./build-exe.sh` and run the same `install` line again.
To uninstall, `rm ~/.local/bin/speedtui` (history stays in
`~/.local/share/speedtui/` unless you delete that too).

If you start it without a terminal (double-click in a file manager, app
launcher), it reopens itself in a terminal: `$TERMINAL` if set, otherwise the
first of konsole, alacritty, kitty, ghostty, wezterm, foot, gnome-terminal,
xfce4-terminal, xterm.

### From source

Requires Python 3.11+. With [uv](https://docs.astral.sh/uv/):

```sh
uv run speedtui
```

Or with a plain virtual environment (no global pip installs needed):

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/speedtui
```

### Options

| Option | Default | Meaning |
|---|---|---|
| `--streams N` | 6 | parallel connections for download/upload |
| `--duration S` | 10 | seconds per direction |
| `--cli` | | plain-text mode, no TUI |
| `--fullscreen` | | use the whole terminal instead of drawing below the prompt |
| `--history` | | print saved results and exit |

### Keys

`r` run again · `h` history · `esc` back · `q` quit

## Build the executable

```sh
./build-exe.sh
```

Installs PyInstaller into the project environment (uv if available, otherwise
`.venv`) and writes a single file to `dist/speedtui`.

## Tests

Offline unit tests (no network) for the measurement helpers, comparisons,
digit font and storage:

```sh
.venv/bin/python -m unittest     # or: uv run python -m unittest
```

## How it measures

All measurement code lives in `src/speedtui/measure.py`, separate from the UI.

| Step | Endpoint | Method |
|---|---|---|
| Location | `GET /cdn-cgi/trace` | reads the `colo` field (Cloudflare data centre, an airport code) |
| Latency | `HEAD /__down?bytes=0` | 1 untimed warm-up request, then 20 timed requests on one connection. Reports the median; jitter is the mean absolute difference between consecutive samples. |
| Download | `GET /__down?bytes=N` | 6 parallel streams, each looping over requests that start at 10 MB and double (up to 50 MB) when one finishes in under a second |
| Upload | `POST /__up` | same as download, sending generated data |

- **Warm-up**: bytes in the first 300 ms of each direction are ignored.
- **Speed** = bytes counted ÷ seconds since the warm-up ended, in Mbps (10⁶ bits/s).
- **Duration**: fixed at 10 s per direction (including warm-up); no early stop.
- **Server time is subtracted from ping.** Cloudflare's endpoint sometimes takes
  hundreds of ms to answer; it reports that processing time in a
  `Server-Timing` header, and speedtui subtracts it from each sample (as
  Cloudflare's own test does). Without this, ping and jitter read far too high.
- **Measurement runs in a child process** (`src/speedtui/runner.py`). Inside
  the TUI, rendering competes with the measurement for Python's GIL and
  inflated latency samples by ~5–10 ms; a separate process avoids that.
- **Upload bytes** are counted when handed to the OS, which runs slightly
  ahead of the wire; the warm-up window absorbs most of this.

Cloudflare rate-limits heavy use. After many runs in a short time it answers
HTTP 429, and speedtui will tell you to wait a few minutes.

## Real-world comparisons

Reference sizes and thresholds are in `src/speedtui/data/comparisons.toml`,
each with a note on where the number comes from. They are approximate and
meant for rough comparison. Each result shows 3 or 4 picked at random (at
least one for download and one for upload) plus what the ping means: how far
light travels in fibre in that time (~200 km per ms) and whether it is fine
for gaming and video calls.

## History

Results are stored in `~/.local/share/speedtui/history.db`
(or `$XDG_DATA_HOME/speedtui/history.db`), table `results`, including every
latency sample as JSON.

```sh
sqlite3 ~/.local/share/speedtui/history.db 'SELECT * FROM results'
```

## Project layout

```
src/speedtui/
├── __main__.py          entry point and options
├── measure.py           measurement (no UI code)
├── runner.py            runs measurement steps in a child process for the TUI
├── cli.py               plain-text mode
├── comparisons.py       turns speeds into real-world comparisons
├── data/comparisons.toml reference sizes with sources
├── storage.py           SQLite history
└── ui/
    ├── app.py           Textual app and screen flow
    ├── dashboard.py     the test screen: tiles, chart, side panel
    ├── chart.py         speed-over-time area chart
    ├── pong.py          Pong rally in the ping tile
    ├── palette.py       colours and theme
    ├── history.py       history screen
    ├── bigdigits.py     block-letter digit font
    └── errors.py        error dialog
```

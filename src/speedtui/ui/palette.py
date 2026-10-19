"""Colours and icons shared by every screen.

Deep navy panels; each metric owns one hue: green download tile, purple
upload, blue ping. In the chart download is drawn in cyan so it reads apart
from the green "good" verdicts.
"""

from textual.theme import Theme

BG = "#0b1020"
PANEL = "#0e1527"
BORDER = "#24304d"
GRID = "#1a2440"
TEXT = "#d5ddf0"
MUTED = "#8b97b5"
FAINT = "#3a4766"

GREEN = "#34d399"
PURPLE = "#a855f7"
BLUE = "#4aa3ff"
CYAN = "#38bdf8"

DOWNLOAD = GREEN  # tile
UPLOAD = PURPLE
PING = BLUE
DOWNLOAD_LINE = CYAN  # chart
UPLOAD_LINE = PURPLE
DOWNLOAD_FILL = "#0c2034"
UPLOAD_FILL = "#1a1233"

# Nerd Font glyphs (Font Awesome range, present in every Nerd Font).
ICONS = {
    "download": "\uf063",  # arrow-down
    "upload": "\uf062",  # arrow-up
    "ping": "\uf017",  # clock
    "server": "\uf0ac",  # globe
    "bolt": "\uf0e7",
    "signal": "\uf012",
    "gamepad": "\uf11b",
    "film": "\uf008",
    "play": "\uf04b",
    "music": "\uf001",
    "disc": "\uf0a0",  # hdd
    "book": "\uf02d",
    "tv": "\uf26c",
    "image": "\uf03e",
    "video": "\uf03d",
    "shield": "\uf132",
    "history": "\uf1da",
    "gauge": "\uf0e4",  # tachometer
    "server-info": "\uf233",  # server
    "list": "\uf0ca",
}

THEME = Theme(
    name="speedtui",
    primary=CYAN,
    secondary=PURPLE,
    accent=BLUE,
    foreground=TEXT,
    background=BG,
    surface=PANEL,
    panel=BORDER,
    success=GREEN,
    warning="#fbbf24",
    error="#f87171",
    dark=True,
)

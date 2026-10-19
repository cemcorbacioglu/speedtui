"""One ink on the terminal's own background, in a few strengths.

The look is a line drawing: thin rounded frames, dotted lines and small block
digits all in the same periwinkle ink. Metrics are told apart by brightness
and by labels written next to them, not by separate colours.
"""

from textual.theme import Theme

BRIGHT = "#dfe3ff"  # live values, the current step
INK = "#aab4f4"  # frames, labels, most text
SOFT = "#7f88c8"  # secondary text
FAINT = "#4f5687"  # axes, guides, things not started yet

# Per-metric shades, all from the same ink.
DOWNLOAD = BRIGHT
UPLOAD = "#9aa3e6"
PING = INK

THEME = Theme(
    name="speedtui",
    primary=INK,
    secondary=SOFT,
    accent=BRIGHT,
    foreground=INK,
    background="ansi_default",
    surface="ansi_default",
    panel=FAINT,
    success=INK,
    warning=BRIGHT,
    error="#f4a3b4",
    dark=True,
)

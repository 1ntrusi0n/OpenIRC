"""IRCX mode metadata shares the central channel mode registry."""
from .modes import CHANNEL_MODES

IRCX_MODES = {key: value for key, value in CHANNEL_MODES.items() if value.ircx}

"""
config.py
=========
Single source of truth for project-wide configuration.
"""

import os

# =============================================================================
# POKEMON SHOWDOWN SERVER (LIVE)
# =============================================================================

LIVE_SHOWDOWN_URI  = "sim3.psim.us"
LIVE_SHOWDOWN_PORT = 443

# =============================================================================
# BATTLE FORMAT
# =============================================================================

DEFAULT_FORMAT = "gen1ou"

# =============================================================================
# LOGGING
# =============================================================================

# poke-env log level (40 = ERROR, 25 = INFO-ish, 10 = DEBUG)
POKE_ENV_LOG_LEVEL = int(os.environ.get("LOG_LEVEL", "40"))

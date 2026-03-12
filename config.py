"""
config.py
=========
Single source of truth for project-wide configuration.

Change the LLM model, server targets, or format settings here —
every module imports from this file.
"""

import os

# =============================================================================
# LLM CONFIGURATION
# =============================================================================

# Ollama model used for all LLM calls (battle decisions, team building, lead pick)
# Override with env var: LLM_MODEL=deepseek-r1:14b python3 competitive_player.py
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-r1:7b")

# Hard timeout (seconds) for LLM calls during live battles.
# If the model doesn't respond in time, Python fallback kicks in.
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT", "30"))

# =============================================================================
# POKEMON SHOWDOWN SERVER
# =============================================================================

# Local server (for training / stress testing)
LOCAL_SHOWDOWN_HOST = "localhost"
LOCAL_SHOWDOWN_PORT = 8000

# Live server (for ladder / challenges)
LIVE_SHOWDOWN_URI = "sim3.psim.us"
LIVE_SHOWDOWN_PORT = 443

# Path to local Showdown install (for tier data in gen1_data.py)
SHOWDOWN_INSTALL_PATH = os.path.expanduser(
    os.environ.get("SHOWDOWN_PATH", "~/pokemon-showdown")
)

# =============================================================================
# BATTLE FORMAT
# =============================================================================

DEFAULT_FORMAT = "gen1ou"
DEFAULT_TIER = "OU"

# =============================================================================
# LOGGING
# =============================================================================

# poke-env log level (40 = ERROR, 25 = INFO-ish, 10 = DEBUG)
POKE_ENV_LOG_LEVEL = int(os.environ.get("LOG_LEVEL", "40"))

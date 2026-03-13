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

# Ollama model used for all LLM calls (battle decisions, lead pick)
# Override with env var: LLM_MODEL=deepseek-r1:7b python3 main.py --ladder 50
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-r1:14b")

# Context length for LLM calls. Default models allocate 128K which wastes
# compute and VRAM on empty context. Battle prompts are ~500 tokens, so
# 2048 is more than enough. This is passed via options.num_ctx at runtime
# so it works with any Ollama model — no custom Modelfile needed.
LLM_CONTEXT_LENGTH = int(os.environ.get("LLM_CONTEXT", "2048"))

# Hard timeout (seconds) for LLM calls during live battles.
# If the model doesn't respond in time, Python fallback kicks in.
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT", "30"))

# Shorter timeout for live play — the event loop must stay responsive
# for websocket pings and Showdown's battle timer.
#
# Showdown timer: 150 seconds total + 60 second grace period per battle.
# With the damage calc handling KOs, Thunder Wave, and healing,
# LLM calls are now rare (~3-5 per game). At 20s per call that's
# 60-100 seconds max, well within the 210-second limit.
LLM_LIVE_TIMEOUT_SECONDS = int(os.environ.get("LLM_LIVE_TIMEOUT", "25"))

# =============================================================================
# POKEMON SHOWDOWN SERVER (LIVE)
# =============================================================================

LIVE_SHOWDOWN_URI = "sim3.psim.us"
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
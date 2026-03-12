"""
live_challenge.py
=================
Connect to the live Pokemon Showdown server and challenge a specific user.

Usage:
    python3 live_challenge.py --accept                     # RECOMMENDED: wait for challenge
    python3 live_challenge.py --opponent YourUsername       # send challenge (may be IP-blocked for new accounts)

For new bot accounts, use --accept mode:
  Showdown blocks challenges from new accounts on flagged IPs.
  In --accept mode, YOU send the challenge from your browser to the bot.
  This bypasses the restriction entirely.

Prerequisites:
    1. Ollama running with your model loaded
    2. credentials.py with bot account details:
         username = "YourBotUsername"    # lowercase 'username'
         password = "YourBotPassword"   # lowercase 'password'
    3. A team file (team_ou_iteration_N.txt)
    4. Your opponent account logged into play.pokemonshowdown.com
"""

import asyncio
import argparse
import glob
import logging
import os
import re
import sys

from poke_env import AccountConfiguration, ShowdownServerConfiguration
from poke_env.player import Player

from config import LLM_MODEL, POKE_ENV_LOG_LEVEL, LLM_LIVE_TIMEOUT_SECONDS
from competitive_player import (
    CompetitivePlayer, load_latest_team, random_suffix, Tee,
)
from llm_bridge import ensure_ollama_running


# =============================================================================
# LOG FILTER — suppress the protocol spam while keeping useful messages
# =============================================================================

class ShowdownLogFilter(logging.Filter):
    """
    Filter poke-env's raw protocol messages to keep output readable.
    Passes through connection events, errors, and our own messages.
    Blocks the massive |formats| list and raw |challstr| dumps.
    """
    # Lines containing these are always blocked
    BLOCK_PATTERNS = [
        '|formats|',       # Hundreds of lines of format definitions
        '|challstr|',      # The auth challenge string (huge)
        '|/trn ',          # The auth response (huge)
        '|updatesearch|',  # Search state updates (noise)
        '|request|',       # Full JSON team/move state (huge, we parse it internally)
        '|t:|',            # Timestamp lines
        '|-damage|',       # Damage events (our code prints these cleaner)
        '|move|',          # Move events (our code prints these cleaner)
        '|switch|',        # Switch events
        '|faint|',         # Faint events
        '|-status|',       # Status events
        '|-boost|',        # Stat boost events
        '|-unboost|',      # Stat drop events
        '|-supereffective|',
        '|-resisted|',
        '|-crit|',
        '|-miss|',
        '|-heal|',
        '|-immune|',
        '|turn|',          # Turn markers
        '|upkeep',         # Turn upkeep
        '|cant|',          # Can't move events
        '|-activate|',     # Ability/item activation
        '|-start|',        # Volatile status start
        '|-end|',          # Volatile status end
        '|-curestatus|',   # Status cure
    ]

    # Lines containing these are always shown
    PASS_PATTERNS = [
        '|updateuser|',    # Login confirmation (but we'll reformat it)
        '|/challenge',     # Challenge sent
        '|/utm',           # Team uploaded
        '|error',          # Errors
        '|pm|',            # Private messages (challenge responses, errors)
        'Event logged',    # poke-env internal events
        'Starting listening',
        'Sending authentication',
    ]

    def filter(self, record):
        msg = record.getMessage()

        # Block known-harmless warnings before the general pass-through
        HARMLESS_WARNINGS = [
            'Unmanaged move message',   # Gen 1 Wrap/Bind/Clamp continuation turns
            'not in that room',          # Move sent to battle that already ended (forfeit race)
            'nothing to choose',         # Same race condition from poke-env side
        ]
        if record.levelno == logging.WARNING:
            for pattern in HARMLESS_WARNINGS:
                if pattern in msg:
                    return False

        # Always show genuine warnings and errors
        if record.levelno >= logging.WARNING:
            return True

        # Block known spam
        for pattern in self.BLOCK_PATTERNS:
            if pattern in msg:
                return False

        # Always show useful events
        for pattern in self.PASS_PATTERNS:
            if pattern in msg:
                return True

        # Block raw protocol lines (start with <<< or >>>)
        if '<<< ' in msg or '>>> ' in msg:
            return False

        return True


def setup_filtered_logging(bot_name):
    """Apply the log filter and short timestamp format to poke-env's logger."""
    logger = logging.getLogger(bot_name)
    logger.addFilter(ShowdownLogFilter())

    # Shorten timestamps to HH:MM:SS (no milliseconds)
    formatter = logging.Formatter('%(asctime)s %(name)s %(message)s', datefmt='%H:%M:%S')
    for handler in logger.handlers:
        handler.setFormatter(formatter)


# =============================================================================
# CREDENTIAL LOADING
# =============================================================================

def get_bot_account():
    """
    Load bot credentials from credentials.py.
    Provides specific error messages for common mistakes.
    """
    if not os.path.exists("credentials.py"):
        print("  ❌ credentials.py not found")
        print("     Create it with:")
        print('       username = "YourBotUsername"')
        print('       password = "YourBotPassword"')
        raise SystemExit(1)

    try:
        import credentials
    except Exception as e:
        print(f"  ❌ Error loading credentials.py: {e}")
        raise SystemExit(1)

    # Check for common naming mistakes
    if not hasattr(credentials, 'username'):
        if hasattr(credentials, 'USERNAME'):
            print("  ❌ credentials.py uses 'USERNAME' — must be lowercase 'username'")
        else:
            print("  ❌ credentials.py is missing 'username' variable")
        raise SystemExit(1)

    if not hasattr(credentials, 'password'):
        if hasattr(credentials, 'PASSWORD'):
            print("  ❌ credentials.py uses 'PASSWORD' — must be lowercase 'password'")
        else:
            print("  ❌ credentials.py is missing 'password' variable")
        raise SystemExit(1)

    username = credentials.username
    password = credentials.password

    if not username or not isinstance(username, str):
        print("  ❌ credentials.py 'username' is empty or not a string")
        raise SystemExit(1)

    if not password or not isinstance(password, str):
        print("  ❌ credentials.py 'password' is empty or not a string")
        raise SystemExit(1)

    print(f"  ✅ Bot account: {username}")
    return AccountConfiguration(username, password)


# =============================================================================
# TEAM FORMAT CONVERSION
# =============================================================================

def convert_team_to_showdown_format(team_text):
    """
    Convert our simple team format to Showdown's text export format.

    Our format:           Showdown export format:
        Gengar                Gengar
        - thunderbolt         - Thunderbolt
        - icebeam             - Ice Beam
        - hypnosis            - Hypnosis
        - dreameater          - Dream Eater
                                                    ← blank line between Pokemon
        Exeggutor             Exeggutor
        - psychic             - Psychic
        ...                   ...

    The KEY requirement is blank lines between Pokemon blocks.
    poke-env uses these to split the team into individual Pokemon.
    Without them, all moves get concatenated into one Pokemon.
    """
    blocks = []
    current_block = []

    for line in team_text.strip().split('\n'):
        stripped = line.strip()

        if not stripped:
            # Blank line = end of current Pokemon block
            if current_block:
                blocks.append('\n'.join(current_block))
                current_block = []
            continue

        if stripped.startswith('-'):
            # Move line
            current_block.append(stripped)
        else:
            # Pokemon name — if we already have a block building, close it
            if current_block:
                blocks.append('\n'.join(current_block))
                current_block = []
            current_block.append(stripped)

    # Don't forget the last block
    if current_block:
        blocks.append('\n'.join(current_block))

    # Join with double newlines — this is what poke-env needs
    result = '\n\n'.join(blocks)
    return result


# =============================================================================
# CHALLENGE MODE
# =============================================================================

async def run_challenge(opponent_name, team, n_battles=1, format_name="gen1ou"):
    """
    Challenge a specific user on the live Showdown server.

    NOTE: New accounts on flagged IPs may be blocked from sending challenges.
    If you get a spam error, use --accept mode instead.
    """
    bot_account = get_bot_account()
    setup_filtered_logging(bot_account.username)

    print(f"\n🌐 Connecting to live Showdown...")
    print(f"   Format:   {format_name}")
    print(f"   Opponent: {opponent_name}")
    print(f"   LLM:      {LLM_MODEL}")

    player = CompetitivePlayer(
        battle_format=format_name,
        team=team,
        server_configuration=ShowdownServerConfiguration,
        account_configuration=bot_account,
        log_level=20,
        start_listening=True,
        start_timer_on_battle_start=True,
        verbose=False,
        live_timeout=LLM_LIVE_TIMEOUT_SECONDS,
    )

    # Wait for connection + auth
    print(f"   Authenticating...")
    await asyncio.sleep(3)

    print(f"\n⚔️  Sending challenge to {opponent_name}...")
    print(f"   Accept in your browser at play.pokemonshowdown.com\n")

    try:
        for i in range(n_battles):
            if n_battles > 1:
                print(f"\n{'='*60}")
                print(f"BATTLE {i+1} of {n_battles}")
                print(f"{'='*60}")
            await player.send_challenges(opponent_name, n_challenges=1)
    except Exception as e:
        print(f"\n  ❌ Challenge failed: {e}")
        print(f"     Try --accept mode instead (you challenge the bot from your browser)")
        return

    wins = sum(1 for b in player.battles.values() if b.won)
    print(f"\n📊 Final: {wins}/{n_battles} wins vs {opponent_name}")
    print(f"   Python decisions: {player._python_call_count}")
    print(f"   LLM decisions:    {player._llm_call_count}")


# =============================================================================
# ACCEPT MODE (recommended for new accounts)
# =============================================================================

async def run_accept(team, n_battles=1, format_name="gen1ou"):
    """
    Wait for an incoming challenge on the live Showdown server.
    YOU send the challenge from your browser. The bot accepts automatically.

    This bypasses Showdown's IP-based spam restrictions on new accounts.
    """
    bot_account = get_bot_account()
    setup_filtered_logging(bot_account.username)

    print(f"\n🌐 Connecting to live Showdown...")
    print(f"   Format: {format_name}")
    print(f"   Mode:   accepting challenges")
    print(f"   LLM:    {LLM_MODEL}")

    player = CompetitivePlayer(
        battle_format=format_name,
        team=team,
        server_configuration=ShowdownServerConfiguration,
        account_configuration=bot_account,
        log_level=20,
        start_listening=True,
        start_timer_on_battle_start=True,
        verbose=False,
        live_timeout=LLM_LIVE_TIMEOUT_SECONDS,
    )

    # Wait for connection + auth
    print(f"   Authenticating...")
    await asyncio.sleep(3)

    bot_name = bot_account.username
    print(f"\n⏳ Bot is online and waiting for a challenge.")
    print(f"   In your browser (logged into your personal account), type:")
    print(f"")
    print(f"     /challenge {bot_name}, {format_name}")
    print(f"")
    print(f"   Or click {bot_name}'s name and select 'Challenge'.\n")

    try:
        await player.accept_challenges(None, n_challenges=n_battles)
    except Exception as e:
        print(f"\n  ❌ Error: {e}")
        return

    wins = sum(1 for b in player.battles.values() if b.won)
    print(f"\n📊 Final: {wins}/{n_battles} wins")
    print(f"   Python decisions: {player._python_call_count}")
    print(f"   LLM decisions:    {player._llm_call_count}")


# =============================================================================
# LADDER MODE — queue for ranked matchmaking
# =============================================================================

async def run_ladder(team, n_games=5, format_name="gen1ou"):
    """
    Play games on the Showdown ranked ladder.
    The bot queues for matchmaking and plays against whoever it's paired with.

    Requires the bot account to have played enough games to not be rate-limited.
    If you get errors, build trust on the account first with --accept mode.
    """
    bot_account = get_bot_account()
    setup_filtered_logging(bot_account.username)

    print(f"\n🌐 Connecting to live Showdown...")
    print(f"   Format: {format_name}")
    print(f"   Mode:   ladder ({n_games} games)")
    print(f"   LLM:    {LLM_MODEL}")

    player = CompetitivePlayer(
        battle_format=format_name,
        team=team,
        server_configuration=ShowdownServerConfiguration,
        account_configuration=bot_account,
        log_level=20,
        start_listening=True,
        start_timer_on_battle_start=True,
        verbose=False,
        live_timeout=LLM_LIVE_TIMEOUT_SECONDS,
    )

    # Wait for connection + auth
    print(f"   Authenticating...")
    await asyncio.sleep(3)

    print(f"\n🏆 Searching for ladder games...")
    print(f"   The bot will queue and play {n_games} ranked game(s).\n")

    try:
        await player.ladder(n_games)
    except Exception as e:
        print(f"\n  ❌ Ladder error: {e}")
        print(f"     The bot account may need more activity before ladder is available.")
        print(f"     Try --accept mode first to build trust on the account.")
        return

    wins = sum(1 for b in player.battles.values() if b.won)
    losses = sum(1 for b in player.battles.values() if b.lost)
    print(f"\n📊 Ladder results: {wins}W / {losses}L across {n_games} games")
    print(f"   Python decisions: {player._python_call_count}")
    print(f"   LLM decisions:    {player._llm_call_count}")

    # Show rating if available
    for battle in player.battles.values():
        if battle.rating:
            print(f"   Rating: {battle.rating}")
            break


# =============================================================================
# ENTRY POINT
# =============================================================================


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Play Pokemon Showdown — challenge, accept, or climb the ladder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  --accept (RECOMMENDED)    Bot logs in and waits. YOU challenge it from your browser.
                            Bypasses IP spam restrictions on new bot accounts.

  --opponent <name>         Bot sends the challenge. Opponent accepts in browser.
                            May be blocked if your IP is flagged by Showdown.

  --ladder <n>              Queue for ranked matchmaking. Plays n games on the ladder.
                            Bot account needs some activity first (use --accept to warm up).

Troubleshooting:
  "spam from your internet provider"
    -> Use --accept mode instead. You challenge the bot from your browser.
    -> Or play some manual games on the bot account to build trust.

  Challenge doesn't appear
    -> Make sure opponent is logged in at play.pokemonshowdown.com
    -> Username must match exactly (case insensitive)

  "credentials.py uses USERNAME"
    -> Use lowercase: username = "Bot"  (not USERNAME = "Bot")
        """
    )
    parser.add_argument(
        "--opponent", type=str, default=None,
        help="Showdown username to challenge"
    )
    parser.add_argument(
        "--accept", action="store_true",
        help="Wait for incoming challenges (recommended for new accounts)"
    )
    parser.add_argument(
        "--ladder", type=int, default=None, metavar="N",
        help="Play N games on the ranked ladder"
    )
    parser.add_argument("--format", default="gen1ou", help="Battle format")
    parser.add_argument(
        "--battles", type=int, default=1,
        help="Number of battles (for --accept and --opponent modes)"
    )
    parser.add_argument(
        "--team-format", default="ou",
        help="Team format for loading team files (ou, uu, etc)"
    )
    args = parser.parse_args()

    # Preflight
    print("Preflight checks...\n")
    if not ensure_ollama_running():
        print("Please start Ollama: ollama serve")
        exit(1)

    # Load and convert team
    team_raw = load_latest_team(args.team_format)
    team = convert_team_to_showdown_format(team_raw)

    # Show team preview (just Pokemon names)
    print(f"\nTeam:")
    for line in team.split('\n'):
        if line.strip() and not line.strip().startswith('-'):
            print(f"   {line.strip()}")
    print()

    # Auto-number log
    import os as _os
    _os.makedirs("live_logs", exist_ok=True)
    existing = glob.glob("live_logs/live_log_*.txt")
    def _num(p):
        m = re.search(r'_(\d+)\.txt$', p)
        return int(m.group(1)) if m else 0
    next_num = max((_num(p) for p in existing), default=0) + 1
    log_path = f"live_logs/live_log_{next_num:03d}.txt"

    tee = Tee(log_path)
    print(f"Logging to: {log_path}")

    try:
        if args.ladder:
            asyncio.run(run_ladder(
                team, n_games=args.ladder, format_name=args.format
            ))
        elif args.opponent:
            asyncio.run(run_challenge(
                args.opponent, team,
                n_battles=args.battles, format_name=args.format
            ))
        elif args.accept:
            asyncio.run(run_accept(
                team, n_battles=args.battles, format_name=args.format
            ))
        else:
            print("No mode specified, defaulting to --accept.")
            print("(This is recommended for new bot accounts.)\n")
            asyncio.run(run_accept(
                team, n_battles=args.battles, format_name=args.format
            ))
    except KeyboardInterrupt:
        print("\n\nInterrupted")
    finally:
        tee.close()
        print(f"\nLog saved to: {log_path}")
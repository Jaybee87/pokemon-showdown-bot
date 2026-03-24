#!/usr/bin/env python3
"""
main.py
=======
Entry point for the Pokemon Showdown Bot — battle mode only.

Run with no arguments for the interactive menu, or jump straight to a mode:
    python3 main.py                          # interactive menu
    python3 main.py --ladder 50              # 50 ladder games
    python3 main.py --accept                 # wait for challenges
    python3 main.py --opponent <user>        # challenge a specific user
"""

import argparse
import asyncio
import os
import re
import glob
import sys

from config import POKE_ENV_LOG_LEVEL


# =============================================================================
# PREFLIGHT CHECKS
# =============================================================================

def check_credentials():
    """Check if credentials.py exists for live play."""
    if os.path.exists("credentials.py"):
        try:
            import credentials
            if hasattr(credentials, 'username') and hasattr(credentials, 'password'):
                print(f"  ✅ Credentials found (account: {credentials.username})")
                return True
            else:
                print(f"  ❌ credentials.py exists but missing username/password")
                print(f"     Use lowercase: username = 'Bot' / password = 'pass'")
                return False
        except Exception as e:
            print(f"  ❌ credentials.py error: {e}")
            return False
    print(f"  ⚠️  credentials.py not found (needed for live play)")
    print(f"     Create it: username = 'YourBot' / password = 'YourPass'")
    return False


def check_poke_env():
    """Check if poke-env is installed."""
    try:
        import poke_env
        print(f"  ✅ poke-env installed")
        return True
    except ImportError:
        print(f"  ❌ poke-env not installed")
        print(f"     pip install poke-env --break-system-packages")
        return False


def run_preflight():
    """Run preflight checks. Returns True if battle mode can proceed."""
    print(f"\n🔧 Preflight checks")

    poke_env_ok    = check_poke_env()
    credentials_ok = check_credentials()

    print()

    if not poke_env_ok:
        print("❌ Missing dependencies for battling")
        return False
    if not credentials_ok:
        print("❌ Credentials required for live play")
        return False
    return True


# =============================================================================
# TEAM DISCOVERY
# =============================================================================

def find_latest_team():
    """Find the latest team file in teams/ directory."""
    os.makedirs("teams", exist_ok=True)
    team_files = glob.glob("teams/team_*_iteration_*.txt")
    if not team_files:
        return None

    def extract_num(path):
        m = re.search(r'_(\d+)\.txt$', path)
        return int(m.group(1)) if m else 0

    team_files.sort(key=extract_num)
    return team_files[-1]


def load_team(path):
    """Load a team file and return (team_string, pokemon_names)."""
    with open(path) as f:
        team_str = f.read()

    pokemon = []
    for line in team_str.strip().split('\n'):
        line = line.strip()
        if line and not line.startswith('-'):
            pokemon.append(line)

    return team_str, pokemon


# =============================================================================
# BATTLE MODE
# =============================================================================

async def run_battle(team_path, mode, n_battles, format_name, opponent=None):
    """Launch live battles with the given team."""
    team_str, pokemon = load_team(team_path)

    print(f"\n📂 Using team: {team_path}")
    print(f"   {' / '.join(pokemon[:6])}")

    if mode == 'ladder':
        from live_challenge import run_ladder
        await run_ladder(team_str, n_games=n_battles, format_name=format_name)
    elif mode == 'accept':
        from live_challenge import run_accept
        await run_accept(team_str, n_battles=n_battles, format_name=format_name)
    elif mode == 'challenge' and opponent:
        from live_challenge import run_challenge
        await run_challenge(opponent, team_str, n_battles=n_battles, format_name=format_name)


# =============================================================================
# INTERACTIVE MENU
# =============================================================================

def show_menu(latest):
    """Show the main menu and return the user's choice."""
    print(f"\n{'='*60}")
    print(f"Pokemon Showdown Bot — Gen 1 OU")
    if latest:
        _, pokemon = load_team(latest)
        print(f"   Team: {latest}")
        print(f"         {' / '.join(pokemon[:6])}")
    else:
        print(f"   Team: (none — place a team file in teams/)")
    print(f"{'='*60}")
    print()
    print(f"  1. Battle (ladder)  — play ranked games on Showdown")
    print(f"  2. Battle (accept)  — wait for challenges from your browser")
    print()

    while True:
        try:
            choice = input("Select: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if choice in ('1', '2'):
            return choice
        print("  Enter 1 or 2.")


def setup_logging():
    """Create a log file and return (log_path, tee)."""
    from competitive_player import Tee
    os.makedirs("live_logs", exist_ok=True)
    existing_logs = glob.glob("live_logs/live_log_*.txt")
    def _num(p):
        m = re.search(r'_(\d+)\.txt$', p)
        return int(m.group(1)) if m else 0
    next_num = max((_num(p) for p in existing_logs), default=0) + 1
    log_path = f"live_logs/live_log_{next_num:03d}.txt"
    tee = Tee(log_path)
    print(f"Logging to: {log_path}")
    return log_path, tee


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pokemon Showdown Bot — battle on the live ladder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ladder",   type=int,          default=None,    help="Number of ladder games")
    parser.add_argument("--accept",   action="store_true",                help="Accept mode (wait for challenges)")
    parser.add_argument("--opponent", default=None,                        help="Challenge a specific user")
    parser.add_argument("--battles",  type=int,          default=10,      help="Battles per session (default: 10)")
    parser.add_argument("--format",   default="gen1ou",                    help="Format (default: gen1ou)")

    args = parser.parse_args()

    if not run_preflight():
        sys.exit(1)

    latest = find_latest_team()
    if not latest:
        print("❌ No team found. Place a team file in teams/")
        print(f"\n  teams/team_ou_iteration_N.txt")
        print(f"\n  Example:")
        print(f"    Tauros")
        print(f"    - bodyslam")
        print(f"    - hyperbeam")
        print(f"    - earthquake")
        print(f"    - blizzard")
        sys.exit(1)

    if args.ladder or args.accept or args.opponent:
        if args.ladder:
            mode, n = 'ladder', args.ladder
        elif args.opponent:
            mode, n = 'challenge', args.battles
        else:
            mode, n = 'accept', args.battles

        log_path, tee = setup_logging()
        try:
            asyncio.run(run_battle(latest, mode, n, args.format, args.opponent))
        except KeyboardInterrupt:
            print("\n\nInterrupted")
        finally:
            tee.close()
            print(f"\nLog saved to: {log_path}")
        sys.exit(0)

    # Interactive menu
    choice = show_menu(latest)

    if choice == '1':
        try:
            n = int(input("How many ladder games? [20]: ").strip() or "20")
        except (ValueError, EOFError, KeyboardInterrupt):
            n = 20
        log_path, tee = setup_logging()
        try:
            asyncio.run(run_battle(latest, 'ladder', n, args.format))
        except KeyboardInterrupt:
            print("\n\nInterrupted")
        finally:
            tee.close()
            print(f"\nLog saved to: {log_path}")

    elif choice == '2':
        log_path, tee = setup_logging()
        try:
            asyncio.run(run_battle(latest, 'accept', 1, args.format))
        except KeyboardInterrupt:
            print("\n\nInterrupted")
        finally:
            tee.close()
            print(f"\nLog saved to: {log_path}")

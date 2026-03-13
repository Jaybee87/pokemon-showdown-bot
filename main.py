#!/usr/bin/env python3
"""
main.py
=======
Entry point for the Pokemon Showdown Bot.

Two functions:
  1. Build a team  → generates a competitive team via LLM iteration
  2. Battle        → takes a team to the live Showdown ladder

Run with no arguments for the interactive menu, or jump straight to a mode:
    python3 main.py                          # interactive menu
    python3 main.py --build                  # jump to team builder
    python3 main.py --battle                 # jump to battle (uses latest team)
    python3 main.py --battle --ladder 50     # 50 ladder games
"""

import argparse
import asyncio
import os
import re
import socket
import sys
import glob

from config import (
    LLM_MODEL, LLM_CONTEXT_LENGTH, LLM_LIVE_TIMEOUT_SECONDS,
    DEFAULT_FORMAT, DEFAULT_TIER, SHOWDOWN_INSTALL_PATH,
    LOCAL_SHOWDOWN_HOST, LOCAL_SHOWDOWN_PORT,
)


# =============================================================================
# PREFLIGHT CHECKS
# =============================================================================

def check_ollama():
    """Check if Ollama is running and the configured model is available."""
    try:
        from llm_bridge import ensure_ollama_running
        return ensure_ollama_running()
    except Exception:
        print(f"  ❌ Ollama not running or model '{LLM_MODEL}' not found")
        print(f"     Install: curl -fsSL https://ollama.com/install.sh | sh")
        print(f"     Pull:    ollama pull {LLM_MODEL}")
        return False


def check_showdown_server():
    """Check if local Pokemon Showdown server is reachable."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((LOCAL_SHOWDOWN_HOST, LOCAL_SHOWDOWN_PORT))
        sock.close()
        if result == 0:
            print(f"  ✅ Showdown server running on {LOCAL_SHOWDOWN_HOST}:{LOCAL_SHOWDOWN_PORT}")
            return True
    except Exception:
        pass
    print(f"  ⚠️  Local Showdown server not running (needed for team building)")
    print(f"     cd ~/pokemon-showdown && node pokemon-showdown start --no-security")
    return False


def check_showdown_install():
    """Check if Pokemon Showdown source is installed (for gen1_data)."""
    path = SHOWDOWN_INSTALL_PATH
    if os.path.isdir(path) and os.path.exists(os.path.join(path, "data")):
        print(f"  ✅ Showdown install found at {path}")
        return True
    print(f"  ⚠️  Showdown install not found at {path} (needed for gen1_data)")
    print(f"     git clone https://github.com/smogon/pokemon-showdown.git {path}")
    return False


def check_gen1_data():
    """Check if gen1_data.json exists."""
    if os.path.exists("gen1_data.json"):
        import json
        with open("gen1_data.json") as f:
            data = json.load(f)
        print(f"  ✅ gen1_data.json exists ({len(data)} Pokemon)")
        return True
    print(f"  ⚠️  gen1_data.json not found (will be built on first team generation)")
    return False


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


def run_preflight(mode="all"):
    """
    Run preflight checks. Returns True if the requested mode can proceed.
    mode: 'build' (team building), 'battle' (live play), 'all' (both)
    """
    print(f"\n🔧 Preflight checks")

    poke_env_ok = check_poke_env()
    ollama_ok = check_ollama()

    if mode in ('build', 'all'):
        showdown_install = check_showdown_install()
        showdown_server = check_showdown_server()
        gen1_data = check_gen1_data()

    if mode in ('battle', 'all'):
        credentials_ok = check_credentials()

    print()

    if mode == 'build':
        if not poke_env_ok or not ollama_ok:
            print("❌ Missing dependencies for team building")
            return False
        return True

    if mode == 'battle':
        if not poke_env_ok or not ollama_ok:
            print("❌ Missing dependencies for battling")
            return False
        if not credentials_ok:
            print("❌ Credentials required for live play")
            return False
        return True

    return poke_env_ok and ollama_ok


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
# BUILD MODE
# =============================================================================

async def run_build(format_name, anchor, n_iterations, n_battles):
    """Team building: generate → test → improve → repeat."""
    from gen1_data import load_format_data
    from team_generator import generate_team
    from battle_runner import run_all_battles

    print(f"Loading Gen 1 {format_name} data...")
    format_data = load_format_data(format_name)
    print(f"Pool: {len(format_data)} Pokemon")

    # Anchor selection
    if not anchor:
        anchor = select_anchor_interactive(format_data)
        if not anchor:
            print("No anchor selected, exiting.")
            return

    if anchor not in format_data:
        available = ', '.join(sorted(format_data.keys()))
        print(f"\n⚠️  Anchor '{anchor}' not found in {format_name} pool.")
        print(f"   Available: {available}")
        anchor = select_anchor_interactive(format_data)
        if not anchor:
            return

    feedback = None
    for iteration in range(n_iterations):
        print(f"\n{'='*60}")
        print(f"ITERATION {iteration + 1} of {n_iterations}")
        print(f"{'='*60}")

        team = generate_team(
            format_data,
            format_name=format_name,
            anchor=anchor,
            battle_feedback=feedback,
        )
        if not team:
            print("❌ Failed to generate valid team, stopping")
            break

        os.makedirs("teams", exist_ok=True)
        team_file = f"teams/team_{format_name.lower()}_iteration_{iteration+1}.txt"
        with open(team_file, "w") as f:
            f.write(team)
        print(f"💾 Saved team to {team_file}")

        print(f"\n⚔️  Running {n_battles} battles per test...")
        feedback = await run_all_battles(team, n_battles=n_battles)

        feedback_file = f"teams/feedback_{format_name.lower()}_iteration_{iteration+1}.txt"
        with open(feedback_file, "w") as f:
            f.write(feedback)

        print(f"\n📋 Feedback for next iteration:")
        print(feedback)

    final_team = f"teams/team_{format_name.lower()}_iteration_{n_iterations}.txt"
    if os.path.exists(final_team):
        print(f"\n{'='*60}")
        print(f"✅ DONE — {n_iterations} iterations complete")
        print(f"   Final team: {final_team}")
        print(f"   Run: python3 main.py --battle")
        print(f"{'='*60}")


def select_anchor_interactive(format_data):
    """Interactive anchor Pokemon selection."""
    top_12 = [
        'tauros', 'snorlax', 'chansey', 'exeggutor', 'alakazam', 'starmie',
        'zapdos', 'rhydon', 'jynx', 'gengar', 'cloyster', 'jolteon',
    ]
    available_top = [p for p in top_12 if p in format_data]

    print(f"\n{'='*60}")
    print(f"SELECT YOUR ANCHOR POKEMON")
    print(f"{'='*60}")
    for i, p in enumerate(available_top, 1):
        data = format_data[p]
        types = '/'.join(t for t in [data.get('type1'), data.get('type2')] if t)
        print(f"  {i:>2}. {data['name']:<12s} ({types})")
    print(f"\n  Or type any Pokemon name from the pool.")
    print(f"{'='*60}")

    while True:
        try:
            choice = input("\nSelect (number or name): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        if not choice:
            continue

        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(available_top):
                selected = available_top[idx]
                display = format_data[selected]['name']
                print(f"\n  ✅ Anchor: {display}\n")
                return selected
            else:
                print(f"  Enter 1-{len(available_top)} or a Pokemon name.")
                continue

        name = choice.lower().replace(' ', '').replace('-', '').replace('.', '').replace("'", '')
        match = next(
            (k for k in format_data if k.lower().replace('-', '') == name),
            None
        )
        if match:
            display = format_data[match]['name']
            print(f"\n  ✅ Anchor: {display}\n")
            return match
        else:
            print(f"  '{choice}' not found in the pool. Try again.")


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

def show_menu():
    """Show the main menu and return the user's choice."""
    latest = find_latest_team()

    print(f"\n{'='*60}")
    print(f"Pokemon Showdown Bot")
    print(f"   LLM:     {LLM_MODEL} (ctx: {LLM_CONTEXT_LENGTH})")
    print(f"   Timeout: {LLM_LIVE_TIMEOUT_SECONDS}s")
    if latest:
        _, pokemon = load_team(latest)
        print(f"   Team:    {latest}")
        print(f"            {' / '.join(pokemon[:6])}")
    else:
        print(f"   Team:    (none — build one first)")
    print(f"{'='*60}")
    print()
    print(f"  1. Build a team     — generate a new team with LLM assistance")
    print(f"  2. Battle (ladder)  — play ranked games on Showdown")
    print(f"  3. Battle (accept)  — wait for challenges from your browser")
    if latest:
        print(f"  4. Import a team    — place your own team file in teams/")
    print()

    while True:
        try:
            choice = input("Select: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if choice in ('1', '2', '3', '4'):
            return choice
        print("  Enter 1, 2, 3, or 4.")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pokemon Showdown Bot — build teams and battle on the ladder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--build", action="store_true", help="Jump to team builder")
    parser.add_argument("--battle", action="store_true", help="Jump to battle mode")
    parser.add_argument("--ladder", type=int, default=None, help="Number of ladder games")
    parser.add_argument("--accept", action="store_true", help="Accept mode (wait for challenges)")
    parser.add_argument("--opponent", default=None, help="Challenge a specific user")
    parser.add_argument("--format", default=DEFAULT_FORMAT, help=f"Format (default: {DEFAULT_FORMAT})")
    parser.add_argument("--anchor", default=None, help="Anchor Pokemon for team building")
    parser.add_argument("--iterations", type=int, default=5, help="Build iterations (default: 5)")
    parser.add_argument("--battles", type=int, default=10, help="Battles per test (default: 10)")
    parser.add_argument("--rebuild-data", action="store_true", help="Force rebuild gen1_data.json")

    args = parser.parse_args()

    # Direct mode via flags
    if args.build:
        if not run_preflight("build"):
            sys.exit(1)
        try:
            asyncio.run(run_build(args.format, args.anchor, args.iterations, args.battles))
        except KeyboardInterrupt:
            print("\n\nInterrupted")
        sys.exit(0)

    if args.battle or args.ladder or args.accept or args.opponent:
        if not run_preflight("battle"):
            sys.exit(1)

        latest = find_latest_team()
        if not latest:
            print("❌ No team found. Run: python3 main.py --build")
            sys.exit(1)

        # Determine battle mode
        if args.ladder:
            mode = 'ladder'
            n = args.ladder
        elif args.opponent:
            mode = 'challenge'
            n = args.battles
        else:
            mode = 'accept'
            n = args.battles

        # Set up logging
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

        try:
            asyncio.run(run_battle(latest, mode, n, args.format, args.opponent))
        except KeyboardInterrupt:
            print("\n\nInterrupted")
        finally:
            tee.close()
            print(f"\nLog saved to: {log_path}")
        sys.exit(0)

    # Interactive menu
    if args.rebuild_data:
        from gen1_data import build_gen1_data
        build_gen1_data()

    choice = show_menu()

    if choice == '1':
        if not run_preflight("build"):
            sys.exit(1)
        try:
            asyncio.run(run_build(args.format, args.anchor, args.iterations, args.battles))
        except KeyboardInterrupt:
            print("\n\nInterrupted")

    elif choice == '2':
        if not run_preflight("battle"):
            sys.exit(1)
        latest = find_latest_team()
        if not latest:
            print("❌ No team found. Build one first (option 1).")
            sys.exit(1)

        try:
            n = int(input("How many ladder games? [20]: ").strip() or "20")
        except (ValueError, EOFError, KeyboardInterrupt):
            n = 20

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

        try:
            asyncio.run(run_battle(latest, 'ladder', n, args.format))
        except KeyboardInterrupt:
            print("\n\nInterrupted")
        finally:
            tee.close()
            print(f"\nLog saved to: {log_path}")

    elif choice == '3':
        if not run_preflight("battle"):
            sys.exit(1)
        latest = find_latest_team()
        if not latest:
            print("❌ No team found. Build one first (option 1).")
            sys.exit(1)

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

        try:
            asyncio.run(run_battle(latest, 'accept', 1, args.format))
        except KeyboardInterrupt:
            print("\n\nInterrupted")
        finally:
            tee.close()
            print(f"\nLog saved to: {log_path}")

    elif choice == '4':
        print(f"\n  Place your team file in teams/ with the format:")
        print(f"    teams/team_ou_iteration_N.txt")
        print(f"\n  The bot uses the highest-numbered iteration.")
        print(f"  Format: one Pokemon per block, moves prefixed with '- '")
        print(f"\n  Example:")
        print(f"    Tauros")
        print(f"    - bodyslam")
        print(f"    - hyperbeam")
        print(f"    - earthquake")
        print(f"    - blizzard")
        print(f"\n  Separate Pokemon with a blank line.")
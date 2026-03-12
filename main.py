#!/usr/bin/env python3
"""
main.py
=======
Single entry point for the Pokemon Showdown Bot.

Run this file and it handles everything:
  1. Preflight checks (Showdown server, Ollama, gen1_data.json)
  2. Build gen1_data.json from pokered if it doesn't exist
  3. Run the team generation + battle iteration loop
  4. Output a ready-to-use team file for competitive play

Usage:
    python3 main.py                        # defaults: OU format, gengar anchor, 5 iterations
    python3 main.py --format OU            # explicit format
    python3 main.py --anchor alakazam      # different anchor Pokemon
    python3 main.py --iterations 3         # fewer iteration cycles
    python3 main.py --battles 20           # more battles per iteration
    python3 main.py --rebuild-data         # force rebuild gen1_data.json

Prerequisites:
    1. Local Pokemon Showdown server running:
       cd ~/pokemon-showdown && node pokemon-showdown start --no-security

    2. Ollama running with a model loaded:
       ollama pull deepseek-r1:7b
       ollama serve

    See INSTALL.md for full setup instructions.
"""

import asyncio
import argparse
import os
import re
import sys
import socket

from config import (
    LLM_MODEL, DEFAULT_TIER, SHOWDOWN_INSTALL_PATH,
    LOCAL_SHOWDOWN_HOST, LOCAL_SHOWDOWN_PORT,
)


# =============================================================================
# PREFLIGHT CHECKS
# =============================================================================

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

    print(f"  ❌ Showdown server not found on {LOCAL_SHOWDOWN_HOST}:{LOCAL_SHOWDOWN_PORT}")
    print(f"     Start it with:")
    print(f"       cd {SHOWDOWN_INSTALL_PATH}")
    print(f"       node pokemon-showdown start --no-security")
    return False


def check_showdown_install():
    """Check if Pokemon Showdown is installed locally (needed for tier data)."""
    formats_path = os.path.join(SHOWDOWN_INSTALL_PATH, "data/mods/gen1/formats-data.ts")
    if os.path.exists(formats_path):
        print(f"  ✅ Showdown install found at {SHOWDOWN_INSTALL_PATH}")
        return True

    print(f"  ❌ Showdown install not found at {SHOWDOWN_INSTALL_PATH}")
    print(f"     Install it with:")
    print(f"       git clone https://github.com/smogon/pokemon-showdown.git {SHOWDOWN_INSTALL_PATH}")
    print(f"       cd {SHOWDOWN_INSTALL_PATH} && npm install")
    return False


def check_ollama():
    """Check if Ollama is running and the configured model is available."""
    from llm_bridge import ensure_ollama_running
    return ensure_ollama_running()


def check_gen1_data(force_rebuild=False):
    """Check if gen1_data.json exists, build it if not."""
    path = "gen1_data.json"

    if force_rebuild and os.path.exists(path):
        os.remove(path)
        print(f"  🔄 Removed existing {path} (--rebuild-data)")

    if os.path.exists(path):
        import json
        with open(path) as f:
            data = json.load(f)
        print(f"  ✅ gen1_data.json exists ({len(data)} Pokemon)")
        return True

    print(f"  📦 gen1_data.json not found — will build on first load")
    return True  # load_format_data handles the build automatically


def run_preflight(force_rebuild=False):
    """Run all preflight checks. Returns True if everything is ready."""
    print("\n🔧 Preflight checks\n")

    ok = True

    if not check_showdown_install():
        ok = False

    if not check_showdown_server():
        ok = False

    if not check_ollama():
        ok = False

    check_gen1_data(force_rebuild)

    if not ok:
        print("\n❌ Preflight failed — fix the issues above and try again.")
        print("   See INSTALL.md for full setup instructions.\n")
        return False

    print("\n✅ All checks passed\n")
    return True


# =============================================================================
# INTERACTIVE ANCHOR SELECTION
# =============================================================================

# Gen 1 OU staples — shown as numbered options with brief descriptions.
# Order is roughly by competitive viability / popularity.
OU_ANCHOR_OPTIONS = [
    ("tauros",    "Tauros",    "The king of Gen 1 — Body Slam + Hyper Beam + unmatched speed"),
    ("alakazam",  "Alakazam",  "Fastest Psychic — Psychic + Recover + Thunder Wave"),
    ("starmie",   "Starmie",   "Versatile Water/Psychic — Surf + Thunderbolt + Recover"),
    ("snorlax",   "Snorlax",   "Bulky physical tank — Body Slam + Earthquake + Amnesia"),
    ("gengar",    "Gengar",    "Ghost/Poison — immune to Normal, Hypnosis + Dream Eater"),
    ("exeggutor", "Exeggutor", "Grass/Psychic — Sleep Powder + Psychic + Explosion"),
    ("chansey",   "Chansey",   "Special wall — Soft-Boiled + Thunder Wave + Seismic Toss"),
    ("zapdos",    "Zapdos",    "Electric/Flying — Thunderbolt + Drill Peck + Thunder Wave"),
    ("cloyster",  "Cloyster",  "Ice/Water — Blizzard + Explosion, massive Defence"),
    ("jynx",      "Jynx",      "Ice/Psychic — Lovely Kiss (sleep) + Blizzard + Psychic"),
    ("rhydon",    "Rhydon",    "Ground/Rock — Earthquake + Rock Slide, hits everything"),
    ("jolteon",   "Jolteon",   "Fastest Electric — Thunderbolt + Thunder Wave + Pin Missile"),
]


def select_anchor_interactive(format_data):
    """
    Interactive anchor selection. Shows numbered OU staples and allows
    free text input for any Pokemon in the pool.

    Returns the chosen anchor name (lowercase internal name).
    """
    print(f"\n{'='*60}")
    print("CHOOSE YOUR ANCHOR POKEMON")
    print(f"{'='*60}")
    print("Your team will be built around this Pokemon.\n")

    # Filter options to only those actually in the format pool
    available_options = [
        (internal, display, desc)
        for internal, display, desc in OU_ANCHOR_OPTIONS
        if internal in format_data
    ]

    for i, (internal, display, desc) in enumerate(available_options, 1):
        print(f"  {i:2d}. {display:12s} — {desc}")

    print(f"\n   Or type any Pokemon name from the {len(format_data)}-mon pool.")
    print(f"   Full pool: {', '.join(sorted(format_data.keys()))}\n")

    while True:
        try:
            choice = input("Select (number or name): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n")
            return None

        if not choice:
            continue

        # Try as a number
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(available_options):
                picked = available_options[idx]
                print(f"\n  ✅ Anchor: {picked[1]}\n")
                return picked[0]
            else:
                print(f"  Enter 1-{len(available_options)} or a Pokemon name.")
                continue

        # Try as a name
        name = choice.lower().replace(' ', '').replace('-', '').replace('.', '').replace("'", '')
        # Match against internal names
        match = next(
            (k for k in format_data
             if k.lower().replace('-', '') == name),
            None
        )
        if match:
            display = format_data[match]['name']
            print(f"\n  ✅ Anchor: {display}\n")
            return match
        else:
            print(f"  '{choice}' not found in the pool. Try again.")


# =============================================================================
# MAIN LOOP
# =============================================================================

async def run_iterations(format_name, anchor, n_iterations, n_battles):
    """
    Core iteration loop:
      1. Load/build Pokemon data
      2. Generate team (LLM picks moves, Python picks composition)
      3. Stress test team vs random + self-play
      4. Feed results back into next iteration
      5. Repeat
    """
    from gen1_data import load_format_data
    from team_generator import generate_team
    from battle_runner import run_all_battles

    print(f"Loading Gen 1 {format_name} data...")
    format_data = load_format_data(format_name)
    print(f"Pool: {len(format_data)} Pokemon")

    # ── Check for existing teams ─────────────────────────────────────────
    import glob as _glob
    existing_teams = sorted(
        _glob.glob(f"teams/team_{format_name.lower()}_iteration_*.txt"),
        key=lambda p: int(re.search(r'_(\d+)\.txt$', p).group(1))
            if re.search(r'_(\d+)\.txt$', p) else 0
    )

    if existing_teams and not anchor:
        latest = existing_teams[-1]
        n_existing = len(existing_teams)

        # Read team names from the latest file
        team_pokemon = []
        with open(latest) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('-'):
                    team_pokemon.append(line)

        print(f"\n{'='*60}")
        print(f"EXISTING TEAM FOUND")
        print(f"   File:       {latest} ({n_existing} iteration{'s' if n_existing != 1 else ''})")
        print(f"   Team:       {' / '.join(team_pokemon[:6])}")
        print(f"{'='*60}")
        print(f"\n   1. Use existing team — skip to battle")
        print(f"   2. Build a new team — pick a new anchor and start fresh")

        while True:
            try:
                choice = input("\nSelect (1 or 2): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n")
                return

            if choice == '1':
                print(f"\n   Using existing team: {latest}")
                print(f"\n{'='*60}")
                print(f"   Next steps:")
                print(f"     python3 competitive_player.py --battles 5          # test locally")
                print(f"     python3 live_challenge.py --accept                 # wait for challenge (recommended)")
                print(f"     python3 live_challenge.py --opponent <username>    # challenge a player")
                print(f"     python3 live_challenge.py --ladder 5               # play 5 ladder games")
                print(f"{'='*60}")
                return
            elif choice == '2':
                print()
                break
            else:
                print("  Enter 1 or 2.")

    # ── Anchor selection ─────────────────────────────────────────────────
    # Interactive anchor selection if none provided via --anchor
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
            print("No anchor selected, exiting.")
            return

    feedback = None

    for iteration in range(n_iterations):
        print(f"\n{'='*60}")
        print(f"ITERATION {iteration + 1} of {n_iterations}")
        print(f"{'='*60}")

        # Generate team
        team = generate_team(
            format_data,
            format_name=format_name,
            anchor=anchor,
            battle_feedback=feedback,
        )
        if not team:
            print("❌ Failed to generate valid team, stopping")
            break

        # Save current team
        os.makedirs("teams", exist_ok=True)
        team_file = f"teams/team_{format_name.lower()}_iteration_{iteration+1}.txt"
        with open(team_file, "w") as f:
            f.write(team)
        print(f"💾 Saved team to {team_file}")

        # Run battles and collect feedback
        print(f"\n⚔️  Running {n_battles} battles per test...")
        feedback = await run_all_battles(team, n_battles=n_battles)

        # Save feedback
        feedback_file = f"teams/feedback_{format_name.lower()}_iteration_{iteration+1}.txt"
        with open(feedback_file, "w") as f:
            f.write(feedback)

        print(f"\n📋 Feedback for next iteration:")
        print(feedback)

    # Summary
    final_team = f"teams/team_{format_name.lower()}_iteration_{n_iterations}.txt"
    if os.path.exists(final_team):
        print(f"\n{'='*60}")
        print(f"✅ DONE — {n_iterations} iterations complete")
        print(f"   Final team: {final_team}")
        print(f"\n   Next steps:")
        print(f"     python3 competitive_player.py --battles 5          # test locally")
        print(f"     python3 live_challenge.py --accept                 # wait for challenge (recommended)")
        print(f"     python3 live_challenge.py --opponent <username>    # challenge a player")
        print(f"     python3 live_challenge.py --ladder 5               # play 5 ladder games")
        print(f"{'='*60}")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pokemon Showdown Bot — build and test competitive Gen 1 teams",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 main.py                          # interactive anchor selection
  python3 main.py --anchor gengar          # skip prompt, use Gengar
  python3 main.py --anchor starmie         # skip prompt, use Starmie
  python3 main.py --iterations 3           # quick 3-cycle test
  python3 main.py --rebuild-data           # force refresh Pokemon data

After main.py finishes, use the generated team:
  python3 competitive_player.py --battles 5    # test locally with smart AI
  python3 live_challenge.py --opponent Rival   # challenge a real player
        """
    )
    parser.add_argument(
        "--format", default=DEFAULT_TIER,
        help=f"Format tier (default: {DEFAULT_TIER})"
    )
    parser.add_argument(
        "--anchor", default=None,
        help="Anchor Pokemon to build around (interactive prompt if omitted)"
    )
    parser.add_argument(
        "--iterations", type=int, default=5,
        help="Number of generate-test-improve cycles (default: 5)"
    )
    parser.add_argument(
        "--battles", type=int, default=10,
        help="Battles per test in each iteration (default: 10)"
    )
    parser.add_argument(
        "--rebuild-data", action="store_true",
        help="Force rebuild gen1_data.json from pokered source"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Pokemon Showdown Bot")
    print(f"   Format:     Gen 1 {args.format}")
    print(f"   Anchor:     {args.anchor or '(interactive)'}")
    print(f"   Iterations: {args.iterations}")
    print(f"   Battles:    {args.battles} per iteration")
    print(f"   LLM:        {LLM_MODEL}")
    print("=" * 60)

    if not run_preflight(force_rebuild=args.rebuild_data):
        sys.exit(1)

    try:
        asyncio.run(run_iterations(
            format_name=args.format,
            anchor=args.anchor,
            n_iterations=args.iterations,
            n_battles=args.battles,
        ))
    except KeyboardInterrupt:
        print("\n\n⏹️  Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        raise
import asyncio
from poke_env.player import RandomPlayer
from poke_env.player import Player
from poke_env import LocalhostServerConfiguration, AccountConfiguration
import json
import random
import string

def random_suffix(length=6):
    return ''.join(random.choices(string.ascii_lowercase, k=length))

def load_team(path="current_team.txt"):
    with open(path) as f:
        return f.read()


class StatTrackingPlayer(Player):
    """Player that uses the team and tracks detailed battle stats"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.battle_stats = []

    def choose_move(self, battle):
        """Simple max damage move selection for now"""
        if battle.available_moves:
            # Pick move with highest base power
            best_move = max(
                battle.available_moves,
                key=lambda m: m.base_power
            )
            return self.create_order(best_move)
        # If no moves available, switch or struggle
        if battle.available_switches:
            return self.create_order(battle.available_switches[0])
        return self.choose_default_move()

    def collect_battle_stats(self, battle):
        """Extract useful stats from a completed battle"""
        stats = {
            "won": battle.won,
            "turns": battle.turn,
            "team": {}
        }

        for pokemon_id, pokemon in battle.team.items():
            stats["team"][pokemon.species] = {
                "fainted": pokemon.fainted,
                "hp_remaining": pokemon.current_hp_fraction * 100 if not pokemon.fainted else 0,
                "moves_used": []
            }

        self.battle_stats.append(stats)

    def get_summary(self):
        """Summarise all battles for feedback to Qwen"""
        if not self.battle_stats:
            return "No battles recorded"

        total = len(self.battle_stats)
        wins = sum(1 for b in self.battle_stats if b["won"])
        avg_turns = sum(b["turns"] for b in self.battle_stats) / total

        # Pokemon performance across all battles
        pokemon_stats = {}
        for battle in self.battle_stats:
            for poke_name, poke_data in battle["team"].items():
                if poke_name not in pokemon_stats:
                    pokemon_stats[poke_name] = {
                        "fainted_count": 0,
                        "survived_count": 0,
                        "total_hp_remaining": 0
                    }
                if poke_data["fainted"]:
                    pokemon_stats[poke_name]["fainted_count"] += 1
                else:
                    pokemon_stats[poke_name]["survived_count"] += 1
                    pokemon_stats[poke_name]["total_hp_remaining"] += poke_data["hp_remaining"]

        # Build summary text
        lines = []
        lines.append(f"Win rate: {wins}/{total} ({int(wins/total*100)}%)")
        lines.append(f"Average battle length: {avg_turns:.1f} turns")
        lines.append("\nPokemon performance:")

        for poke_name, stats in pokemon_stats.items():
            total_battles = stats["fainted_count"] + stats["survived_count"]
            faint_rate = int(stats["fainted_count"] / total_battles * 100)
            avg_hp = (
                stats["total_hp_remaining"] / stats["survived_count"]
                if stats["survived_count"] > 0 else 0
            )
            lines.append(
                f"  {poke_name}: fainted {faint_rate}% of battles, "
                f"avg HP when survived: {avg_hp:.0f}%"
            )

        return "\n".join(lines)


async def run_vs_random(team, n_battles=10):
    """Run team against a basic opponent"""
    print(f"\n⚔️  Running {n_battles} battles vs opponent...")

    player = StatTrackingPlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(f"TeamBot_{random_suffix()}", None),
        log_level=20
    )

    opponent = StatTrackingPlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(f"RandomBot_{random_suffix()}", None),
        log_level=20
    )

    await player.battle_against(opponent, n_battles=n_battles)

    # Collect stats from completed battles
    for battle in player.battles.values():
        player.collect_battle_stats(battle)

    summary = player.get_summary()
    print("\n📊 vs Random results:")
    print(summary)
    return summary


async def run_vs_self(team, n_battles=10):
    """Run team against itself"""
    print(f"\n⚔️  Running {n_battles} self-play battles...")

    player1 = StatTrackingPlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(f"SelfBot1_{random_suffix()}", None),
        log_level=20
    )
    player2 = StatTrackingPlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(f"SelfBot2_{random_suffix()}", None),
        log_level=20
    )

    await player1.battle_against(player2, n_battles=n_battles)

    # Collect stats
    for battle in player1.battles.values():
        player1.collect_battle_stats(battle)
    for battle in player2.battles.values():
        player2.collect_battle_stats(battle)

    summary1 = player1.get_summary()
    summary2 = player2.get_summary()

    print("\n📊 Self-play results (Player 1):")
    print(summary1)

    # For self-play we care about which pokemon fainted most
    combined = "\n[Self-play - Side 1]\n" + summary1 + "\n[Self-play - Side 2]\n" + summary2
    return combined


async def run_all_battles(team, n_battles=10):
    """Run both battle types and return combined feedback"""
    vs_random = await run_vs_random(team, n_battles)
    vs_self = await run_vs_self(team, n_battles)

    feedback = f"""
BATTLE RESULTS FOR TEAM EVALUATION:

=== vs Random Player ({n_battles} battles) ===
{vs_random}

=== Self-Play ({n_battles} battles) ===
{vs_self}

Use these results to improve the team:
- Pokemon that faint frequently are weak links — consider replacing them
- Pokemon that consistently survive may not be contributing enough damage
- Low win rate vs random suggests fundamental team weakness
- Self-play imbalance suggests one or two Pokemon are carrying the whole team
"""
    return feedback


if __name__ == "__main__":
    print("Loading team...")
    team = load_team()
    print(f"Team loaded:\n{team}")

    feedback = asyncio.run(run_all_battles(team, n_battles=10))

    print("\n📋 Full feedback for Qwen:")
    print(feedback)

    # Save feedback for use by team generator
    with open("battle_feedback.txt", "w") as f:
        f.write(feedback)
    print("\nSaved to battle_feedback.txt")
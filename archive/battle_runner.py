"""
battle_runner.py
================
Runs Gen 1 OU battles and collects detailed performance feedback.

Tracks per-Pokemon AND per-move stats by intercepting raw PS protocol messages:
  move    : move used, by whom, against whom
  -miss   : links back to last move event
  -damage : hp delta dealt by last move
  -status : status condition inflicted by last move
  -heal   : hp recovered by last move (recover, softboiled, dreameater drain)

Move selection:
  70% max base power — exploits best moves
  30% random        — ensures all 4 moves get data so LLM can evaluate all of them
  Struggle is always filtered — switches instead to prevent infinite PP loops
"""

import asyncio
import random
import string
from typing import List

from poke_env.player import Player
from poke_env import LocalhostServerConfiguration, AccountConfiguration


# =============================================================================
# HELPERS
# =============================================================================

def random_suffix(length=6):
    return ''.join(random.choices(string.ascii_lowercase, k=length))


def load_team(path="current_team_ou.txt"):
    with open(path) as f:
        return f.read()


# =============================================================================
# STAT TRACKING PLAYER
# =============================================================================

class StatTrackingPlayer(Player):
    """
    Player that intercepts raw PS protocol messages to track move-level stats.
    All existing poke-env behaviour is preserved via super() calls.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.battle_stats = []
        self._move_log    = {}
        self._last_move   = {}
        self._prev_hp     = {}

    # -------------------------------------------------------------------------
    # Raw message interception
    # -------------------------------------------------------------------------

    async def _handle_battle_message(self, split_messages: List[List[str]]):
        battle_tag = split_messages[0][0]

        for msg in split_messages[1:]:
            if not msg or len(msg) < 2:
                continue

            msg_type = msg[1]

            if msg_type == 'move':
                if battle_tag not in self._move_log:
                    self._move_log[battle_tag] = []

                pokemon = (
                    msg[2].split(':')[1].strip().lower()
                    if len(msg) > 2 and ':' in msg[2]
                    else msg[2].lower() if len(msg) > 2 else 'unknown'
                )
                move = (
                    msg[3].lower().replace(' ', '').replace('-', '')
                    if len(msg) > 3 else 'unknown'
                )
                target = (
                    msg[4].split(':')[1].strip().lower()
                    if len(msg) > 4 and ':' in msg[4]
                    else None
                )
                event = {
                    'pokemon': pokemon,
                    'move':    move,
                    'target':  target,
                    'missed':  False,
                    'damage':  0,
                    'status':  None,
                    'healed':  0,
                }
                self._move_log[battle_tag].append(event)
                self._last_move[battle_tag] = event

            elif msg_type == '-miss':
                if battle_tag in self._last_move:
                    self._last_move[battle_tag]['missed'] = True

            elif msg_type == '-damage':
                if battle_tag in self._last_move and len(msg) > 3:
                    hp_str    = msg[3].split()[0]
                    hp_parts  = hp_str.split('/')
                    target_id = msg[2] if len(msg) > 2 else None
                    if len(hp_parts) == 2:
                        try:
                            current_hp = int(hp_parts[0])
                            max_hp     = int(hp_parts[1])
                            prev = self._prev_hp.get(battle_tag, {}).get(target_id)
                            if prev is not None:
                                damage = int(prev * max_hp) - current_hp
                                if damage > 0:
                                    self._last_move[battle_tag]['damage'] += damage
                            if battle_tag not in self._prev_hp:
                                self._prev_hp[battle_tag] = {}
                            self._prev_hp[battle_tag][target_id] = current_hp / max_hp
                        except (ValueError, ZeroDivisionError):
                            pass

            elif msg_type == '-status':
                if battle_tag in self._last_move and len(msg) > 3:
                    self._last_move[battle_tag]['status'] = msg[3]

            elif msg_type == '-heal':
                if battle_tag in self._last_move and len(msg) > 3:
                    hp_str    = msg[3].split()[0]
                    hp_parts  = hp_str.split('/')
                    target_id = msg[2] if len(msg) > 2 else None
                    if len(hp_parts) == 2:
                        try:
                            current_hp = int(hp_parts[0])
                            max_hp     = int(hp_parts[1])
                            prev = self._prev_hp.get(battle_tag, {}).get(target_id)
                            if prev is not None:
                                healed = current_hp - int(prev * max_hp)
                                if healed > 0:
                                    self._last_move[battle_tag]['healed'] += healed
                            if battle_tag not in self._prev_hp:
                                self._prev_hp[battle_tag] = {}
                            self._prev_hp[battle_tag][target_id] = current_hp / max_hp
                        except (ValueError, ZeroDivisionError):
                            pass

        await super()._handle_battle_message(split_messages)

    # -------------------------------------------------------------------------
    # Move selection
    # -------------------------------------------------------------------------

    def choose_move(self, battle):
        """
        70% max base power, 30% random — ensures all 4 moves get usage data.
        Filters Struggle and switches instead to prevent PP exhaustion loops.
        """
        real_moves = [m for m in battle.available_moves if m.id != 'struggle']

        if real_moves:
            if random.random() < 0.3:
                return self.create_order(random.choice(real_moves))
            return self.create_order(max(real_moves, key=lambda m: m.base_power))

        if battle.available_switches:
            return self.create_order(random.choice(battle.available_switches))

        return self.choose_default_move()

    # -------------------------------------------------------------------------
    # Stats collection
    # -------------------------------------------------------------------------

    def collect_battle_stats(self, battle):
        stats = {
            "won":   battle.won,
            "turns": battle.turn,
            "team":  {}
        }
        for pokemon_id, pokemon in battle.team.items():
            stats["team"][pokemon.species] = {
                "fainted":      pokemon.fainted,
                "hp_remaining": pokemon.current_hp_fraction * 100 if not pokemon.fainted else 0,
            }
        self.battle_stats.append(stats)

    def get_move_summary(self):
        """Aggregate move stats. Excludes struggle — PP artifact not a real choice."""
        move_stats = {}

        for battle_tag, events in self._move_log.items():
            for event in events:
                m = event['move']
                if not m or m in ('unknown', 'struggle'):
                    continue
                if m not in move_stats:
                    move_stats[m] = {
                        'pokemon':      event['pokemon'],
                        'used':         0,
                        'hit':          0,
                        'miss':         0,
                        'total_damage': 0,
                        'statuses':     0,
                        'healed':       0,
                    }
                move_stats[m]['used']         += 1
                move_stats[m]['total_damage'] += event['damage']
                move_stats[m]['healed']       += event['healed']
                if event['missed']:
                    move_stats[m]['miss'] += 1
                else:
                    move_stats[m]['hit']  += 1
                if event['status']:
                    move_stats[m]['statuses'] += 1

        for m, s in move_stats.items():
            s['avg_damage']   = int(s['total_damage'] / s['used']) if s['used'] > 0 else 0
            s['accuracy_pct'] = int(s['hit'] / s['used'] * 100)   if s['used'] > 0 else 0

        return move_stats

    def get_summary(self):
        if not self.battle_stats:
            return "No battles recorded"

        total     = len(self.battle_stats)
        wins      = sum(1 for b in self.battle_stats if b["won"])
        avg_turns = sum(b["turns"] for b in self.battle_stats) / total

        pokemon_stats = {}
        for battle in self.battle_stats:
            for poke_name, poke_data in battle["team"].items():
                if poke_name not in pokemon_stats:
                    pokemon_stats[poke_name] = {
                        "fainted_count":  0,
                        "survived_count": 0,
                        "total_hp":       0,
                    }
                if poke_data["fainted"]:
                    pokemon_stats[poke_name]["fainted_count"] += 1
                else:
                    pokemon_stats[poke_name]["survived_count"] += 1
                    pokemon_stats[poke_name]["total_hp"]       += poke_data["hp_remaining"]

        lines = []
        lines.append(f"Win rate: {wins}/{total} ({int(wins/total*100)}%)")
        lines.append(f"Average battle length: {avg_turns:.1f} turns")
        lines.append("\nPokemon performance:")

        for poke_name, stats in pokemon_stats.items():
            total_battles = stats["fainted_count"] + stats["survived_count"]
            faint_rate    = int(stats["fainted_count"] / total_battles * 100)
            avg_hp        = (
                stats["total_hp"] / stats["survived_count"]
                if stats["survived_count"] > 0 else 0
            )
            lines.append(
                f"  {poke_name}: fainted {faint_rate}% of battles, "
                f"avg HP when survived: {avg_hp:.0f}%"
            )

        move_stats = self.get_move_summary()
        if move_stats:
            lines.append("\nMove performance:")
            by_pokemon = {}
            for move, stats in move_stats.items():
                poke = stats['pokemon']
                if poke not in by_pokemon:
                    by_pokemon[poke] = []
                by_pokemon[poke].append((move, stats))

            for poke, moves in sorted(by_pokemon.items()):
                lines.append(f"  {poke}:")
                for move, s in sorted(moves, key=lambda x: -x[1]['used']):
                    parts = [f"used {s['used']}x"]
                    if s['miss'] > 0:
                        parts.append(f"hit {s['hit']}x / miss {s['miss']}x ({s['accuracy_pct']}% acc)")
                    else:
                        parts.append(f"hit {s['hit']}x (100% acc)")
                    if s['total_damage'] > 0:
                        parts.append(f"avg dmg {s['avg_damage']}")
                    else:
                        parts.append("0 damage")
                    if s['statuses'] > 0:
                        parts.append(f"inflicted status {s['statuses']}x")
                    if s['healed'] > 0:
                        parts.append(f"healed {s['healed']} HP total")

                    verdict = ""
                    if (s['total_damage'] == 0
                            and s['statuses'] == 0
                            and s['healed'] == 0
                            and s['used'] >= 3):
                        verdict = " ⚠ DEAD WEIGHT — no damage, no status, no heal"
                    elif s['accuracy_pct'] < 50 and s['used'] >= 3:
                        verdict = " ⚠ LOW ACCURACY — consider more reliable alternative"
                    elif s['total_damage'] > 0 and s['accuracy_pct'] == 100:
                        verdict = " ✓ RELIABLE"

                    lines.append(f"    {move}: {', '.join(parts)}{verdict}")

        return "\n".join(lines)


# =============================================================================
# PROGRESS DISPLAY
# =============================================================================

def _progress_bar(wins, total, width=10):
    filled = int(width * wins / total) if total > 0 else 0
    return "█" * filled + "░" * (width - filled)


# =============================================================================
# BATTLE RUNNERS
# =============================================================================

async def run_vs_random(team, n_battles=10):
    print(f"\n⚔️  vs Random ({n_battles} battles)")

    player = StatTrackingPlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(f"TeamBot_{random_suffix()}", None),
        log_level=40
    )
    opponent = StatTrackingPlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(f"RandBot_{random_suffix()}", None),
        log_level=40
    )

    await player.battle_against(opponent, n_battles=n_battles)

    wins = 0
    for i, battle in enumerate(player.battles.values(), 1):
        player.collect_battle_stats(battle)
        if battle.won:
            wins += 1
        result = "✓" if battle.won else "✗"
        bar    = _progress_bar(wins, n_battles)
        print(f"  Battle {i:2d}/{n_battles} {result}  [{bar}] {wins}/{i}", flush=True)

    summary = player.get_summary()
    return summary


async def run_vs_self(team, n_battles=10):
    print(f"\n🔄  Self-play ({n_battles} battles)")

    player1 = StatTrackingPlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(f"Self1_{random_suffix()}", None),
        log_level=40
    )
    player2 = StatTrackingPlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(f"Self2_{random_suffix()}", None),
        log_level=40
    )

    await player1.battle_against(player2, n_battles=n_battles)

    wins = 0
    for i, battle in enumerate(player1.battles.values(), 1):
        player1.collect_battle_stats(battle)
        if battle.won:
            wins += 1
        result = "✓" if battle.won else "✗"
        bar    = _progress_bar(wins, n_battles)
        print(f"  Battle {i:2d}/{n_battles} {result}  [{bar}] {wins}/{i}", flush=True)

    for battle in player2.battles.values():
        player2.collect_battle_stats(battle)

    summary1 = player1.get_summary()
    summary2 = player2.get_summary()
    combined  = "\n[Self-play - Side 1]\n" + summary1 + "\n[Self-play - Side 2]\n" + summary2
    return combined


async def run_all_battles(team, n_battles=10):
    vs_random = await run_vs_random(team, n_battles)
    vs_self   = await run_vs_self(team, n_battles)

    feedback = f"""
BATTLE RESULTS FOR TEAM EVALUATION:

=== vs Random Player ({n_battles} battles) ===
{vs_random}

=== Self-Play ({n_battles} battles) ===
{vs_self}

Use these results to improve the team:
- Moves marked DEAD WEIGHT should be replaced immediately
- Moves marked LOW ACCURACY may still be worth keeping for utility (sleep, paralysis)
- Pokemon that faint frequently are weak links — consider replacing them
- Move combos (hypnosis+dreameater, lovelykiss+dreameater) need both moves present
- Pokemon that survive but deal 0 damage are not contributing — check their moves
"""
    return feedback


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    print("Loading team...")
    team = load_team()
    print(f"Team loaded:\n{team}")

    feedback = asyncio.run(run_all_battles(team, n_battles=10))

    print("\n📋 Full feedback:")
    print(feedback)

    with open("battle_feedback.txt", "w") as f:
        f.write(feedback)
    print("\nSaved to battle_feedback.txt")
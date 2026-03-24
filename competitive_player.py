"""
competitive_player.py
=====================
Competitive Gen 1 OU player using a hybrid Python/Rust decision engine.

Decision hierarchy — Python only handles mechanical certainties.
Rust handles everything strategic.

  1. FORCED      — only one legal action (struggle / recharge / single switch)
  2. RECHARGE    — locked after Hyper Beam, no choice
  3. ASLEEP      — we can't act; queue best move or switch if clean
  4. FAINT SWITCH — Rust picks the send-in; Python fallback if Rust errors
  5. GUARANTEED KO — Python math says we finish them this turn → do it
  6. IMMUNE       — opponent known moves do 0x to us → stay in and hit
  7. SLEEP MOVE   — opponent has no status and we have a sleep move → use it
  8. RUST ENGINE  — all other decisions: switch timing, Hyperbeam risk,
                    status moves, matchup evaluation, stall breaks
  9. PYTHON HARD FALLBACK — best type-effective move if Rust errors

Python fast-paths that previously fired before Rust (danger switch, dominant
matchup, matchup switch, Thunder Wave, heal, Dream Eater, sleep follow-up)
have all been moved AFTER the Rust engine or removed. The engine has full
context and makes better decisions on those than hard-coded thresholds.
"""

import asyncio
import argparse
import random
import string
import re
import glob
import sys
import time

from poke_env.player import Player
from poke_env import LocalhostServerConfiguration, AccountConfiguration

try:
    from poke_env.battle.side_condition import SideCondition
except ImportError:
    from poke_env.environment.side_condition import SideCondition

try:
    from poke_env.environment.move import Move as PokeEnvMove
except ImportError:
    try:
        from poke_env.data.move import Move as PokeEnvMove
    except ImportError:
        PokeEnvMove = None  # type lookup via poke-env unavailable; gen1_data cache still works

from config import POKE_ENV_LOG_LEVEL
from gen1_engine import (
    type_effectiveness, get_pokemon_types, best_move_effectiveness,
    worst_incoming_effectiveness, find_best_switch, resolve_move_types,
    register_move_type, get_move_type,
    calc_damage_pct, can_ko, find_ko_move, outspeeds, get_speed,
    evaluate_matchup, find_best_matchup_switch,
    freeze_chance_value, secondary_effect_value,
    get_substitute_hp, can_break_substitute,
    FIXED_DAMAGE_MOVES, OHKO_MOVES, SLEEP_MOVES, IGNORE_MOVES,
)
from rust_engine_bridge import RustEngine, build_state, action_to_poke_env

# Measured Rust engine throughput (nodes/sec) on the 9800X3D with 6 threads.
# Used to convert a time budget (ms) into an iteration cap so the time limit
# and iteration cap fire at approximately the same point — no idle time wasted.
NODES_PER_SEC = 57_000


# =============================================================================
# TIME MANAGER
# =============================================================================

class TimeManager:
    """
    Allocates the Showdown time bank across turns.

    Showdown ladder rules:
      - 210 seconds total bank per game (shared across all turns)
      - Hard cap of 150 seconds per individual turn
      - Timer only counts when it's YOUR turn to move

    Strategy: score each position for complexity (0.0–1.0) and allocate
    compute time proportionally, preserving enough bank for remaining turns.

    Complexity factors:
      - Late game (few mons alive): higher — fewer branches but each matters more
      - HP close / both sides chipped: higher — outcome less certain
      - First turn: higher — unknown opponent team, widest decision space
      - Faint switch: higher — irreversible, sets next N turns
      - Clear winning position (3+ mon advantage): lower — don't waste time
      - Python fast-path fired: zero — time manager not consulted at all

    Time budget floor: 300ms  (never slower than original for trivial spots)
    Time budget ceiling: 30,000ms per turn (leaves headroom in bank)
    Default base budget: 2,000ms when bank is healthy
    """

    # Showdown limits
    BANK_TOTAL_S   = 210.0   # total bank seconds
    TURN_CAP_S     = 150.0   # hard per-turn cap
    BANK_FLOOR_S   = 10.0    # keep 10s reserve (reduced from 15)
    MIN_MS         = 500     # floor — minimum search quality
    MAX_MS         = 30_000  # ceiling 30s per turn for critical endgame decisions
    BASE_MS        = 5_000   # raised from 2000 — we were leaving 95% of bank unused

    def __init__(self):
        self._bank_remaining_s: float = self.BANK_TOTAL_S
        self._turn_start: float       = 0.0
        self._last_turn: int          = 0

    def reset(self):
        """Call at the start of each new game."""
        self._bank_remaining_s = self.BANK_TOTAL_S
        self._turn_start       = 0.0
        self._last_turn        = 0

    def start_turn(self, turn: int):
        """Record when we started thinking this turn."""
        if turn != self._last_turn:
            self._last_turn  = turn
            self._turn_start = time.monotonic()

    def end_turn(self):
        """Deduct actual elapsed time from the bank."""
        if self._turn_start > 0:
            elapsed = time.monotonic() - self._turn_start
            self._bank_remaining_s = max(0.0, self._bank_remaining_s - elapsed)
            self._turn_start = 0.0

    def allocate(
        self,
        battle_turn:   int,
        our_alive:     int,
        opp_alive:     int,
        our_hp_frac:   float,
        opp_hp_frac:   float,
        is_faint_switch: bool = False,
    ) -> int:
        """
        Return the number of milliseconds to give the Rust engine this turn.
        Called after Python fast-paths have been checked — only invoked when
        we're actually going to run a search.
        """
        spendable = self._bank_remaining_s - self.BANK_FLOOR_S
        if spendable <= 0:
            return self.MIN_MS

        # ── Complexity score 0.0–1.0 ─────────────────────────────────────
        score = 0.0

        # Game phase: late game is highest stakes
        total_alive = our_alive + opp_alive
        if total_alive <= 2:
            score += 0.45   # last mons — every node matters
        elif total_alive <= 4:
            score += 0.30   # endgame
        elif total_alive <= 6:
            score += 0.15   # midgame
        else:
            score += 0.05   # early game, full teams

        # Turn 1: widest decision space, opponent team unknown
        if battle_turn == 1:
            score += 0.20

        # Faint switch: irreversible send-in decision
        if is_faint_switch:
            score += 0.25

        # Position closeness: close HP = harder to evaluate
        hp_diff = abs(our_hp_frac - opp_hp_frac)
        if hp_diff < 0.15:
            score += 0.20   # essentially even
        elif hp_diff < 0.30:
            score += 0.10

        # Clear lead: we're winning easily → spend less
        mon_diff = our_alive - opp_alive
        if mon_diff >= 2:
            score -= 0.15
        elif mon_diff <= -2:
            # We're losing — think harder about the comeback
            score += 0.15

        score = max(0.05, min(1.0, score))

        # ── Budget allocation ─────────────────────────────────────────────
        # Scale BASE_MS by complexity. Cap at the lesser of:
        #   - The per-turn hard cap (150s)
        #   - 20% of remaining bank (so we can't blow the whole bank on one turn)
        # This is more aggressive than before — we were leaving 95% unused.
        raw_ms  = int(self.BASE_MS * (0.3 + score * 1.4))  # 0.3–1.7× base
        bank_ms = int(min(spendable, self.TURN_CAP_S) * 1000 * 0.20)  # 20% of available
        alloc   = max(self.MIN_MS, min(raw_ms, bank_ms, self.MAX_MS))

        return alloc

    def status(self) -> str:
        return f"bank={self._bank_remaining_s:.1f}s"



# =============================================================================
# COMPETITIVE PLAYER
# =============================================================================

class CompetitivePlayer(Player):

    def __init__(self, *args, verbose=True, total_games=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._opponent_move_types = {}
        self._opponent_move_names = {}
        self._python_call_count   = 0
        self._rust_call_count     = 0
        self._verbose             = verbose
        self._current_battle_tag  = None
        self._total_games         = total_games
        self._games_finished      = 0
        self._wins                = 0
        self._battle_start_py     = 0
        self._battle_start_rust   = 0
        self._sleep_clause_active = False
        self._last_rust_count     = 0
        # Sleep turn tracking: species.lower() → turns_asleep (increments each turn)
        # Used to give Rust an accurate sleep duration estimate.
        self._sleep_turns: dict        = {}
        # Toxic turn tracking: species.lower() → ticks taken (increments each turn)
        # Used to give Rust an accurate toxic counter since poke-env is unreliable.
        self._toxic_turns: dict        = {}
        # Substitute HP tracking: species.lower() → sub HP fraction (0.0 when no sub)
        # poke-env doesn't expose this; we infer from Substitute use and damage taken.
        self._sub_hp_fracs: dict       = {}
        # Sleep move tracking: species.lower() → move_id attempted
        # Prevents re-firing a sleep move that missed last turn.
        self._sleep_attempted_vs: dict = {}
        # T-Wave tracking: species.lower() → True once attempted
        # Prevents re-firing T-Wave every turn when it misses or is blocked.
        self._twave_attempted_vs: dict = {}
        self._last_rust_result:   dict = {}
        self._last_healed_turn:   int  = -99
        self._last_healed_species: str = ""
        self._last_healed_hp_frac: float = 1.0   # HP at time of last heal (tox race check)
        self._last_switched_in_turn: int  = -99   # switch cooldown
        self._last_switched_in_species: str = ""  # which mon just switched in
        # Opponent HP tracking: species.lower() → hp_fraction at end of last turn.
        # Used to detect healing even when the move name was never revealed.
        self._opp_hp_last_turn: dict = {}
        self._time_manager = TimeManager()
        self._rust_engine = RustEngine(
            algorithm="auto",
            depth=6,
            iterations=100_000,  # high ceiling — real cap set dynamically per turn
            time_ms=500,         # fallback default
        )

    def _log(self, msg):
        print(msg)

    def _unsuppress(self):
        tee = sys.stdout
        if hasattr(tee, '_suppress_console'):
            tee._suppress_console = False

    def _emit_compact(self, turn, my_poke, my_hp, opp_poke, opp_hp, action, source):
        self._unsuppress()
        if not self._verbose:
            indicator = "⚙️" if source == "rust" else "⚡"
            print(f"  {indicator} T{turn:02d} {my_poke}({my_hp}%) vs {opp_poke}({opp_hp}%) → {action} [{source}]")

    # -------------------------------------------------------------------------
    # Team preview — matchup-based lead selection
    # -------------------------------------------------------------------------

    async def teampreview(self, battle):
        my_team  = list(battle.team.values())
        opp_team = list(battle.opponent_team.values())

        print(f"\n{'='*60}")
        print(f"TEAM PREVIEW")
        print(f"  My team:  {', '.join(p.species for p in my_team)}")
        print(f"  Opp team: {', '.join(p.species for p in opp_team)}")

        # Score each of our mons as a lead against the full opponent team.
        best_lead  = my_team[0]
        best_score = -9999
        for candidate in my_team:
            score = sum(evaluate_matchup(candidate, opp) for opp in opp_team)
            if score > best_score:
                best_score = score
                best_lead  = candidate

        order     = [best_lead] + [p for p in my_team if p != best_lead]
        order_str = '/team ' + ''.join(str(my_team.index(p) + 1) for p in order)
        print(f"  ✅ LEAD: {best_lead.species} (score={best_score:.0f})")
        return order_str

    # -------------------------------------------------------------------------
    # Opponent move tracking
    # -------------------------------------------------------------------------

    def _get_opponent_prefix(self, battle):
        if hasattr(battle, 'player_role') and battle.player_role:
            return 'p2' if battle.player_role == 'p1' else 'p1'
        return 'p2'

    async def _handle_battle_message(self, split_messages):
        battle_tag = split_messages[0][0] if split_messages[0] else None
        battle     = self.battles.get(battle_tag) if battle_tag else None
        opp_prefix = self._get_opponent_prefix(battle) if battle else 'p2'

        for msg in split_messages[1:]:
            if not msg or len(msg) < 2:
                continue
            if msg[1] == 'move' and len(msg) > 3:
                actor     = msg[2] if len(msg) > 2 else ''
                move_name = msg[3].lower().replace(' ', '').replace('-', '')
                if actor.startswith(opp_prefix) and move_name not in IGNORE_MOVES:
                    species = (
                        actor.split(':')[1].strip().lower()
                        if ':' in actor else actor
                    )
                    if species not in self._opponent_move_names:
                        self._opponent_move_names[species] = []
                    if move_name not in self._opponent_move_names[species]:
                        self._opponent_move_names[species].append(move_name)

                    move_type = get_move_type(move_name)
                    if not move_type and PokeEnvMove is not None:
                        try:
                            move_obj = PokeEnvMove(move_name, gen=1)
                            if move_obj.type:
                                move_type = move_obj.type.name.lower()
                                register_move_type(move_name, move_type)
                        except Exception:
                            pass
                    if move_type:
                        if species not in self._opponent_move_types:
                            self._opponent_move_types[species] = []
                        if move_type not in self._opponent_move_types[species]:
                            self._opponent_move_types[species].append(move_type)

        await super()._handle_battle_message(split_messages)

    # -------------------------------------------------------------------------
    # Core decision engine
    # -------------------------------------------------------------------------

    async def choose_move(self, battle):
        order = await self._choose_move_inner(battle)

        if not self._verbose:
            my    = battle.active_pokemon
            opp   = battle.opponent_active_pokemon
            my_hp  = int(my.current_hp_fraction * 100)
            opp_hp = int((opp.current_hp_fraction or 1.0) * 100)

            action_str = "?"
            source     = "py"
            if hasattr(order, 'order') and order.order:
                o = order.order
                if hasattr(o, 'id'):
                    action_str = o.id
                elif hasattr(o, 'species'):
                    action_str = f"→{o.species}"

            if self._rust_call_count > self._last_rust_count:
                source = "rust"
            self._last_rust_count = self._rust_call_count

            self._emit_compact(battle.turn, my.species, my_hp,
                               opp.species, opp_hp, action_str, source)
        else:
            self._unsuppress()

        return order

    async def _choose_move_inner(self, battle):
        my_poke    = battle.active_pokemon
        opp_poke   = battle.opponent_active_pokemon
        my_types   = get_pokemon_types(my_poke)
        opp_types  = get_pokemon_types(opp_poke)
        my_hp_frac  = my_poke.current_hp_fraction
        my_species  = my_poke.species.lower()
        opp_species = opp_poke.species.lower()
        opp_hp_frac = opp_poke.current_hp_fraction or 1.0

        for m in battle.available_moves:
            if m.type:
                register_move_type(m.id, m.type.name.lower())

        opp_status_now = opp_poke.status
        my_status_now  = my_poke.status

        # ── Turn header ───────────────────────────────────────────────────

        def status_str(poke):
            parts = []
            if poke.status:
                parts.append(poke.status.name)
            for eff in (poke.effects or {}):
                if hasattr(eff, 'name') and eff.name == 'CONFUSION':
                    parts.append('CONF')
                    break
            return f" [{', '.join(parts)}]" if parts else ''

        _tee = sys.stdout
        if not self._verbose and hasattr(_tee, '_suppress_console'):
            _tee._suppress_console = True

        print(f"\n{'='*60}")
        print(f"Turn {battle.turn} | My: {my_poke.species} ({int(my_hp_frac*100)}% HP{status_str(my_poke)}) "
              f"vs {opp_poke.species} ({int(opp_hp_frac*100)}% HP{status_str(opp_poke)})")
        print(f"  My types: {my_types} | Opp types: {opp_types}")

        # ── Battle-start snapshot ─────────────────────────────────────────
        if battle.turn == 1:
            self._battle_start_py   = self._python_call_count
            self._battle_start_rust = self._rust_call_count
            self._sleep_clause_active  = False
            self._sleep_turns          = {}
            self._toxic_turns          = {}
            self._sub_hp_fracs         = {}
            self._sleep_attempted_vs   = {}
            self._twave_attempted_vs   = {}
            self._last_healed_turn     = -99
            self._last_healed_species  = ""
            self._last_healed_hp_frac  = 1.0
            self._last_switched_in_turn    = -99
            self._last_switched_in_species = ""
            self._opp_hp_last_turn         = {}
            self._time_manager.reset()

        # Record turn start for time bank tracking
        self._time_manager.start_turn(battle.turn)

        # Sleep Clause tracking
        if not self._sleep_clause_active:
            for opp_mon in battle.opponent_team.values():
                if opp_mon.status and opp_mon.status.name == 'SLP':
                    self._sleep_clause_active = True
                    break

        # ── Sleep turn tracking ───────────────────────────────────────────
        # On wake, remove from dict. This gives Rust an accurate turns-asleep
        # value rather than a static estimate.
        for p in battle.team.values():
            key = p.species.lower()
            if p.status and p.status.name == 'SLP':
                self._sleep_turns[key] = self._sleep_turns.get(key, 0) + 1
            elif key in self._sleep_turns:
                del self._sleep_turns[key]  # woke up

        # ── Toxic turn tracking ───────────────────────────────────────────
        # Increment each turn the mon is badly poisoned; clear on cure/faint.
        for p in battle.team.values():
            key = p.species.lower()
            if p.status and p.status.name in ('TOX', 'PSN'):
                self._toxic_turns[key] = self._toxic_turns.get(key, 0) + 1
            elif key in self._toxic_turns:
                del self._toxic_turns[key]

        # Clear sleep attempt record when opponent is confirmed asleep — move landed.
        if opp_status_now and opp_status_now.name == 'SLP':
            self._sleep_attempted_vs.pop(opp_species, None)
        # Clear T-Wave attempt record when opponent is confirmed PAR — move landed.
        if opp_status_now and opp_status_now.name == 'PAR':
            self._twave_attempted_vs.pop(opp_species, None)

        # ── Opponent HP tracking (heal detection) ─────────────────────────
        # Record current opponent HP after each turn. If next turn's HP is
        # higher than this turn's (same species, no switch), the opponent healed.
        opp_hp_prev = self._opp_hp_last_turn.get(opp_species, None)
        opp_healed_this_turn = (
            opp_hp_prev is not None
            and opp_hp_frac > opp_hp_prev + 0.05  # >5% gain = genuine heal
        )
        self._opp_hp_last_turn[opp_species] = opp_hp_frac

        # ── Build usable move list ─────────────────────────────────────────
        # Strip moves that can never work this turn
        real_moves = [m for m in battle.available_moves
                      if m.id not in ('struggle', 'recharge')]

        NON_VOLATILE_STATUS_MOVES = {
            'sleeppowder', 'hypnosis', 'spore', 'lovelykiss', 'sing',
            'thunderwave', 'stunspore', 'glare', 'toxic', 'poisonpowder',
        }
        if opp_status_now:
            real_moves = [m for m in real_moves if m.id not in NON_VOLATILE_STATUS_MOVES]

        def is_immune(move):
            if move.id in FIXED_DAMAGE_MOVES:
                return False
            move_type = move.type.name.lower() if move.type else 'normal'
            return type_effectiveness(move_type, opp_types) == 0

        real_moves = [m for m in real_moves if not is_immune(m)]

        if 'ground' in opp_types:
            real_moves = [m for m in real_moves if m.id not in ('thunderwave', 'stunspore')]
        if 'grass' in opp_types:
            real_moves = [m for m in real_moves if m.id != 'stunspore']

        opp_is_asleep = opp_status_now and opp_status_now.name == 'SLP'
        if not opp_is_asleep:
            real_moves = [m for m in real_moves if m.id != 'dreameater']

        _opp_has_sub = False
        if opp_poke.effects:
            for eff in opp_poke.effects:
                eff_name = eff.name if hasattr(eff, 'name') else str(eff)
                if 'SUBSTITUTE' in eff_name.upper():
                    _opp_has_sub = True
                    break
        if _opp_has_sub:
            real_moves = [m for m in real_moves if m.id not in {
                'thunderwave', 'sleeppowder', 'stunspore', 'toxic',
                'hypnosis', 'lovelykiss', 'sing', 'poisonpowder',
                'confuseray', 'supersonic',
            }]

        if self._sleep_clause_active:
            real_moves = [m for m in real_moves if m.id not in SLEEP_MOVES]

        switches   = battle.available_switches
        all_moves  = battle.available_moves
        my_is_asleep = my_status_now and my_status_now.name == 'SLP'

        if real_moves:
            print(f"  Moves: {[m.id for m in real_moves]}")
        if switches:
            print(f"  Switches: {[p.species for p in switches]}")

        # Shared status flags used across multiple steps
        HEAL_MOVES   = ('softboiled', 'recover', 'rest')
        my_is_par    = my_status_now and my_status_now.name == 'PAR'
        my_is_tox    = my_status_now and my_status_now.name in ('TOX', 'PSN')
        my_is_brn    = my_status_now and my_status_now.name == 'BRN'
        my_is_asleep = my_status_now and my_status_now.name == 'SLP'
        my_is_frz    = my_status_now and my_status_now.name == 'FRZ'
        opp_is_asleep = opp_status_now and opp_status_now.name == 'SLP'

        our_alive = sum(1 for p in battle.team.values() if not p.fainted)
        opp_alive = sum(1 for p in battle.opponent_team.values() if not p.fainted)

        my_boosts  = dict(my_poke.boosts)  if my_poke.boosts  else {}
        opp_boosts = dict(opp_poke.boosts) if opp_poke.boosts else {}
        opp_has_reflect     = False
        opp_has_lightscreen = False
        try:
            opp_has_reflect     = SideCondition.REFLECT      in battle.opponent_side_conditions
            opp_has_lightscreen = SideCondition.LIGHT_SCREEN in battle.opponent_side_conditions
        except AttributeError:
            for sc in battle.opponent_side_conditions:
                sc_name = sc.name if hasattr(sc, 'name') else str(sc)
                if 'REFLECT' in sc_name.upper(): opp_has_reflect     = True
                if 'LIGHT'   in sc_name.upper(): opp_has_lightscreen = True
        calc_kwargs = {
            'atk_boosts':      my_boosts,
            'def_boosts':      opp_boosts,
            'reflect':         opp_has_reflect,
            'light_screen':    opp_has_lightscreen,
            'attacker_burned': my_is_brn,
        }

        # ══════════════════════════════════════════════════════════════════
        # STEP 1 — Recharge lock
        # ══════════════════════════════════════════════════════════════════
        if len(all_moves) == 1 and all_moves[0].id == 'recharge':
            print(f"  ⏳ PYTHON: recharge turn")
            self._python_call_count += 1
            return self.create_order(all_moves[0])

        # ══════════════════════════════════════════════════════════════════
        # STEP 2 — No legal options
        # ══════════════════════════════════════════════════════════════════
        if not real_moves and not switches:
            print("  🔒 FORCED: no options, using default")
            self._python_call_count += 1
            return self.choose_default_move()

        # ══════════════════════════════════════════════════════════════════
        # STEP 3 — Field correctness: are we on the right mon?
        #
        # 3a. No moves → faint switch (Rust decides send-in)
        # 3b. Incapacitated (asleep/frozen) → switch to best healthy mon
        # 3c. Can act → stay (Rust's switch pruning handles bad matchups)
        #
        # 3b and 3c use find_best_switch for consistency.
        # ══════════════════════════════════════════════════════════════════

        # 3a — faint switch
        if not real_moves and switches:
            if len(switches) == 1:
                print(f"  🔀 FORCED SWITCH: only {switches[0].species} left")
                self._python_call_count += 1
                return self.create_order(switches[0])
            print(f"\n  ⚙️  RUST ENGINE (faint switch)")
            faint_time_ms = self._time_manager.allocate(
                battle_turn=battle.turn, our_alive=our_alive, opp_alive=opp_alive,
                our_hp_frac=my_hp_frac, opp_hp_frac=opp_hp_frac, is_faint_switch=True,
            )
            faint_iters = max(3000, int(faint_time_ms * NODES_PER_SEC / 1000))
            rust_order = self._try_rust_faint_switch(battle, switches,
                                                     time_ms=faint_time_ms,
                                                     iterations=faint_iters)
            if rust_order:
                self._time_manager.end_turn()
                return rust_order
            best = find_best_switch(battle)
            print(f"  🔀 PYTHON FAINT SWITCH FALLBACK: {best.species}")
            self._python_call_count += 1
            return self.create_order(best)

        # 3b — incapacitated: switch to best non-incapacitated mon
        if (my_is_asleep or my_is_frz) and switches:
            best_sw = find_best_switch(battle)
            if best_sw:
                sw_incap = best_sw.status and best_sw.status.name in ('SLP', 'FRZ')
                sw_hp    = best_sw.current_hp_fraction or 0
                if not sw_incap and not (len(switches) == 1 and sw_hp < 0.30):
                    status_name = 'asleep' if my_is_asleep else 'frozen'
                    print(f"  💤 PYTHON: {status_name} — switching to {best_sw.species}")
                    self._python_call_count += 1
                    return self.create_order(best_sw)
            # No good switch — queue best move (mon likely can't act but must submit)
            if real_moves:
                best_m, _ = best_move_effectiveness(real_moves, opp_types,
                                                    attacker_types=my_types)
                fallback = best_m or real_moves[0]
                status_name = 'asleep' if my_is_asleep else 'frozen'
                print(f"  💤 PYTHON: {status_name} — queuing {fallback.id}")
                self._python_call_count += 1
                return self.create_order(fallback)

        # 3c — we can act; fall through with confidence

        # ══════════════════════════════════════════════════════════════════
        # STEP 4 — Guaranteed KO
        #
        # Min-roll kills → commit, no search needed.
        # Guards: PAR (25% miss), opp faster+healer (heals before we hit),
        #         Hyperbeam at <15% HP, explosion/selfdestruct (Rust's call).
        # ══════════════════════════════════════════════════════════════════
        opp_revealed_heals = self._opponent_move_names.get(opp_species, [])
        opp_has_heal = any(m in opp_revealed_heals
                           for m in ('recover', 'softboiled', 'rest'))
        opp_is_faster = not outspeeds(
            my_species, opp_species,
            a_par=bool(my_is_par),
            b_par=bool(opp_poke.status and opp_poke.status.name == 'PAR'
                       if opp_poke.status else False),
        )
        skip_gko = bool(my_is_par) or (opp_has_heal and opp_is_faster)

        if not skip_gko:
            for mv in real_moves:
                if mv.id in ('explosion', 'selfdestruct'):
                    continue
                if mv.id == 'hyperbeam' and my_hp_frac < 0.15:
                    continue
                try:
                    is_ko = can_ko(my_species, mv.id, opp_species,
                                   hp_pct=opp_hp_frac, use_avg=False, **calc_kwargs)
                except Exception:
                    is_ko = False
                if is_ko:
                    print(f"  🎯 PYTHON GUARANTEED KO: {mv.id} finishes "
                          f"{opp_poke.species} at {int(opp_hp_frac*100)}%")
                    self._python_call_count += 1
                    return self.create_order(mv)

        # ══════════════════════════════════════════════════════════════════
        # STEP 5 — Status control
        #
        # Opponent has no status → inflict one. Priority: sleep > paralysis.
        # Sleep eliminates turns entirely; always higher value than attacking.
        # T-Wave: only when opp has Recover and we can't 2HKO through it.
        # ══════════════════════════════════════════════════════════════════
        if not opp_status_now and not _opp_has_sub:
            # Sleep (highest priority)
            if not self._sleep_clause_active:
                sleep_move = next((m for m in real_moves if m.id in SLEEP_MOVES), None)
                already_tried = self._sleep_attempted_vs.get(opp_species)
                if sleep_move and already_tried != sleep_move.id:
                    self._sleep_attempted_vs[opp_species] = sleep_move.id
                    print(f"  😴 PYTHON: using {sleep_move.id}")
                    self._python_call_count += 1
                    return self.create_order(sleep_move)

            # Thunder Wave — paralysis is always valuable on an unstatused opponent.
            # Track attempts per species so we don't re-fire if it missed/was blocked.
            twave = next((m for m in real_moves if m.id == 'thunderwave'), None)
            if twave and not self._twave_attempted_vs.get(opp_species):
                self._twave_attempted_vs[opp_species] = True
                print(f"  ⚡ PYTHON: Thunder Wave — inflicting PAR")
                self._python_call_count += 1
                return self.create_order(twave)

        # ══════════════════════════════════════════════════════════════════
        # STEP 6 — Toxic fast-heal
        #
        # Escalating Toxic drain → heal immediately unless futile.
        # Futile: counter ≥ 9 (drain > recovery) or losing race (healed
        # last turn but HP still fell). Both fall through to Rust.
        # ══════════════════════════════════════════════════════════════════
        if my_is_tox:
            heal_move = next((m for m in real_moves if m.id in HEAL_MOVES), None)
            if heal_move:
                tox_counter = getattr(my_poke, 'toxic_turn_counter',
                                      getattr(my_poke, 'toxic_turns_left', None))
                if tox_counter is None:
                    try:
                        tox_counter = battle.active_pokemon.n_turns_statused
                    except AttributeError:
                        tox_counter = None
                tox_is_futile   = (tox_counter is not None and tox_counter >= 9)
                healed_last_turn = (self._last_healed_turn == battle.turn - 1
                                    and self._last_healed_species == my_species)
                tox_losing_race  = (healed_last_turn
                                    and my_hp_frac < self._last_healed_hp_frac - 0.05)
                if not tox_is_futile and not tox_losing_race:
                    print(f"  💊 PYTHON: Toxiced — healing with {heal_move.id}")
                    self._last_healed_turn    = battle.turn
                    self._last_healed_species = my_species
                    self._last_healed_hp_frac = my_hp_frac
                    self._python_call_count  += 1
                    return self.create_order(heal_move)
                elif tox_is_futile:
                    print(f"  🚫 PYTHON: Toxic futile (counter={tox_counter}) — deferring")
                else:
                    print(f"  🚫 PYTHON: Toxic recovery losing race — deferring")

        # ══════════════════════════════════════════════════════════════════
        # STEP 7 — Strip non-viable moves
        #
        # Strip heals that are wasteful; strip attacks that are dominated.
        # Heal moves are NEVER treated as dominated attacks — they are a
        # separate category that Rust weighs (heal vs attack trade-off).
        #
        # After stripping, count remaining moves:
        #   0 → Rust gets switches or hard fallback
        #   1 → Python uses it (no search needed — decision is obvious)
        #   2+ → Rust decides among legitimate options
        # ══════════════════════════════════════════════════════════════════
        turns_since_heal  = battle.turn - self._last_healed_turn
        same_species_heal = (self._last_healed_species == my_species)
        strip_log = {}  # move_id → reason

        # Heal strips
        for hm in [m for m in real_moves if m.id in HEAL_MOVES]:
            reason = None
            if hm.id == 'rest' and my_is_par:
                reason = "PAR+Rest = 2+ dead turns"
            elif hm.id == 'rest' and my_hp_frac >= 0.85 and not my_is_tox:
                reason = f"{int(my_hp_frac*100)}% HP ≥ 85% (Rest cost too high)"
            elif hm.id in ('softboiled', 'recover'):
                threshold = 0.95 if my_is_par else 0.90
                if my_hp_frac >= threshold and not my_is_tox:
                    reason = f"{int(my_hp_frac*100)}% HP ≥ {int(threshold*100)}%"
            if reason is None and turns_since_heal <= 3 and same_species_heal and my_hp_frac >= 0.80:
                reason = f"healed {turns_since_heal}t ago"
            if reason is None and my_hp_frac >= 0.80 and our_alive >= opp_alive + 2:
                reason = f"up {our_alive - opp_alive} mons"
            if reason:
                strip_log[hm.id] = reason

        # Attack strips — compute best expected damage first (heals and
        # explosion/selfdestruct excluded — sacrifice moves shouldn't set
        # the domination baseline or they'd strip all regular attacks).
        SACRIFICE_MOVES = ('explosion', 'selfdestruct')
        damaging = [m for m in real_moves
                    if m.id not in HEAL_MOVES and (m.base_power or 0) > 0]
        best_exp_dmg  = 0.0
        exp_dmg_cache = {}
        for mv in damaging:
            try:
                lo, hi = calc_damage_pct(my_species, mv.id, opp_species,
                                         atk_boosts=my_boosts, def_boosts=opp_boosts)
                exp = (lo + hi) / 2
            except Exception:
                exp = 0.0
            # Add secondary effect value so Body Slam (30% para) correctly
            # outweighs moves with slightly higher raw damage but no secondary.
            exp += secondary_effect_value(mv.id)
            exp_dmg_cache[mv.id] = exp
            if mv.id not in SACRIFICE_MOVES:
                best_exp_dmg = max(best_exp_dmg, exp)

        for mv in damaging:
            if mv.id in strip_log:
                continue
            reason = None
            if mv.id == 'hyperbeam' and my_is_par:
                reason = "PAR+Hyperbeam"
            elif mv.id == 'hyperbeam' and my_hp_frac < 0.15:
                reason = "Hyperbeam at <15% HP"
            elif mv.id == 'dreameater' and not opp_is_asleep:
                reason = "Dream Eater — opp not asleep"
            elif best_exp_dmg > 0:
                exp = exp_dmg_cache.get(mv.id, 0.0)
                if exp < best_exp_dmg * 0.55:
                    reason = (f"dominated ({int(exp*100)}% < 55% of "
                              f"best {int(best_exp_dmg*100)}%)")
            if reason:
                strip_log[mv.id] = reason

        # Strip status moves (base_power = 0, not heals).
        # All status decisions were made in Step 5 — any status move
        # that survives to Step 7 is irrelevant in the current situation.
        for mv in real_moves:
            if mv.id in strip_log or mv.id in HEAL_MOVES:
                continue
            if (mv.base_power or 0) == 0:
                strip_log[mv.id] = "status move — handled in Step 5"

        # Apply strips (only when alternatives survive)
        if strip_log:
            filtered = [m for m in real_moves if m.id not in strip_log]
            if filtered:
                for mv_id, reason in sorted(strip_log.items()):
                    print(f"  🚫 PYTHON: suppressing {mv_id} ({reason})")
                real_moves = filtered

        # ══════════════════════════════════════════════════════════════════
        # STEP 8 — Single viable move: no search needed
        # ══════════════════════════════════════════════════════════════════
        if len(real_moves) == 1:
            mv = real_moves[0]
            print(f"  ⚡ PYTHON: only viable move — {mv.id}")
            self._python_call_count += 1
            return self.create_order(mv)

        # ══════════════════════════════════════════════════════════════════
        # STEP 9 — Rust engine
        #
        # 2+ viable moves remain. Rust decides:
        #   heal vs attack trade-off, switch timing, Hyperbeam risk/reward,
        #   damage races, endgame trades.
        # ══════════════════════════════════════════════════════════════════
        active_move_ids = [m.id for m in real_moves]

        time_budget_ms = self._time_manager.allocate(
            battle_turn=battle.turn, our_alive=our_alive, opp_alive=opp_alive,
            our_hp_frac=my_hp_frac, opp_hp_frac=opp_hp_frac, is_faint_switch=False,
        )
        print(f"  ⏱  Time budget: {time_budget_ms}ms ({self._time_manager.status()})")
        iters = max(3000, int(time_budget_ms * NODES_PER_SEC / 1000))

        rust_result = self._try_rust_engine(
            battle,
            active_move_ids=active_move_ids,
            time_ms=time_budget_ms,
            iterations=iters,
        )
        self._time_manager.end_turn()

        if rust_result is None:
            return self._hard_fallback(battle, real_moves, switches)

        # Post-process: veto Hyperbeam in deeply losing positions
        # (unless it's a guaranteed KO — dead opp can't punish recharge)
        last = getattr(self, '_last_rust_result', {})
        if (last.get('action', {}).get('id') == 'hyperbeam'
                and last.get('score', 0) < -2000
                and not opp_is_asleep):
            hb_is_gko = can_ko(
                my_species, 'hyperbeam', opp_species,
                hp_pct=opp_hp_frac, use_avg=False,
                atk_boosts=my_boosts, def_boosts=opp_boosts,
            ) if opp_hp_frac > 0 else False
            if not hb_is_gko:
                alt = next(
                    (m for m in real_moves
                     if m.id != 'hyperbeam' and m.base_power and m.base_power > 0),
                    None,
                )
                if alt:
                    print(f"  🚫 PYTHON: vetoing Hyperbeam (score={last['score']:.0f}, "
                          f"losing) — using {alt.id} instead")
                    self._python_call_count += 1
                    return self.create_order(alt)

        # Track heal and switch state for next turn's strip logic
        last_action    = last.get('action', {})
        last_action_id = last_action.get('id', '')
        if last_action_id in ('softboiled', 'recover', 'rest'):
            self._last_healed_turn    = battle.turn
            self._last_healed_species = my_species
        if last_action.get('type') == 'switch':
            self._last_switched_in_turn    = battle.turn
            self._last_switched_in_species = last_action.get('species', '').lower()

        return rust_result

    def _hard_fallback(self, battle, real_moves, switches):
        """Hard Python fallback when Rust engine is unavailable or errored."""
        my_poke  = battle.active_pokemon
        opp_poke = battle.opponent_active_pokemon
        my_types  = get_pokemon_types(my_poke)
        opp_types = get_pokemon_types(opp_poke)

        reason_str = "rust engine unavailable — python fallback"
        print(f"\n  🔄 HARD FALLBACK: {reason_str}")

        best_move, _ = best_move_effectiveness(
            real_moves, opp_types, attacker_types=my_types
        )
        opp_known_names = self._opponent_move_names.get(opp_poke.species.lower(), [])
        print(f"     Opponent known moves: {opp_known_names or 'none'}")

        if best_move:
            print(f"  🔄 HARD FALLBACK: {best_move.id}")
            self._python_call_count += 1
            return self.create_order(best_move)
        return self.choose_default_move()

    # -------------------------------------------------------------------------
    # Rust engine helpers
    # -------------------------------------------------------------------------

    def _try_rust_engine(self, battle, active_move_ids: list = None,
                         time_ms: int = None, iterations: int = None):
        """
        Query the Rust engine for a normal (non-faint) decision.
        active_move_ids: optional filtered list of move IDs to present to Rust.
        time_ms: search budget in ms; defaults to engine default if None.
        iterations: iteration cap; defaults to engine default if None.
        """
        self._last_rust_result = {}
        try:
            state = build_state(
                battle,
                sleep_turns=self._sleep_turns,
                toxic_counters=self._toxic_turns,
                sub_hp_fracs=self._sub_hp_fracs,
            )
            if active_move_ids is not None:
                state["ours"]["active"]["moves"] = active_move_ids
            result = self._rust_engine.choose(state, time_ms=time_ms,
                                               iterations=iterations)
            if "error" in result:
                print(f"  ⚠️  Rust engine error: {result['error']}")
                return None

            self._last_rust_result = result
            action = result.get("action", {})
            atype  = action.get("type")
            algo   = result.get("algorithm", "rust")
            score  = result.get("score", 0)
            nodes  = result.get("nodes_searched", 0)
            reason = result.get("reason", "")
            label  = action.get("id") or action.get("species") or atype or "?"

            # ── Handle __sleep_frz__ internal token ───────────────────────
            if atype == "move" and action.get("id") == "__sleep_frz__":
                print(f"  ⚙️  RUST: active is frozen/asleep — finding best switch")
                switches = battle.available_switches
                if switches:
                    best = max(
                        (p for p in switches
                         if not (p.status and p.status.name in ('SLP', 'FRZ'))),
                        key=lambda p: p.current_hp_fraction or 0,
                        default=None
                    ) or switches[0]
                    print(f"  ✅ RUST [frozen/sleep switch]: {best.species}")
                    self._rust_call_count += 1
                    return self.create_order(best)
                real_moves = [m for m in battle.available_moves
                              if m.id not in ('struggle', 'recharge', '__sleep_frz__')]
                if real_moves:
                    best_m = max(real_moves, key=lambda m: m.base_power or 0)
                    self._rust_call_count += 1
                    return self.create_order(best_m)
                return None

            print(f"  ✅ RUST [{algo}]: {label} (score={score:.0f}, nodes={nodes}) | {reason}")
            self._rust_call_count += 1

            poke_obj, _ = action_to_poke_env(result, battle)
            if poke_obj is not None:
                return self.create_order(poke_obj)

            print(f"  ⚠️  Rust action '{label}' not in legal list")
            return None

        except Exception as e:
            print(f"  ⚠️  Rust engine exception: {e}")
            return None

    def _try_rust_faint_switch(self, battle, switches, time_ms: int = None,
                               iterations: int = None):
        """
        Query Rust specifically for a faint switch.
        Builds a state where our active has no moves (switch-only position)
        so Rust returns a switch action, not a move.
        """
        try:
            state = build_state(
                battle,
                sleep_turns=self._sleep_turns,
                toxic_counters=self._toxic_turns,
                sub_hp_fracs=self._sub_hp_fracs,
            )
            state["ours"]["active"]["moves"] = []
            result = self._rust_engine.choose(state, time_ms=time_ms,
                                               iterations=iterations)

            if "error" in result:
                print(f"  ⚠️  Rust faint-switch error: {result['error']}")
                return None

            action = result.get("action", {})
            algo   = result.get("algorithm", "rust")
            score  = result.get("score", 0)
            reason = result.get("reason", "")

            if action.get("type") == "switch":
                species = action.get("species", "").lower()
                chosen  = next(
                    (p for p in switches
                     if p.species.lower() == species),
                    None
                )
                if chosen:
                    print(f"  ✅ RUST [{algo}]: switch {chosen.species} "
                          f"(score={score:.0f}) | {reason}")
                    self._rust_call_count += 1
                    return self.create_order(chosen)
                print(f"  ⚠️  Rust faint-switch species '{species}' not in bench")
            else:
                move_id = action.get("id", "")
                if move_id == "struggle":
                    # All remaining mons are asleep/frozen — no valid switches.
                    # Pick the healthiest available switch regardless of status.
                    best = max(switches, key=lambda p: p.current_hp_fraction or 0)
                    print(f"  ✅ RUST [all incapacitated — sending {best.species}]")
                    self._rust_call_count += 1
                    return self.create_order(best)
                print(f"  ⚠️  Rust returned move during faint switch: {action}")
            return None

        except Exception as e:
            print(f"  ⚠️  Rust faint-switch exception: {e}")
            return None

    # -------------------------------------------------------------------------
    # End of battle summary
    # -------------------------------------------------------------------------

    def _battle_finished_callback(self, battle):
        self._unsuppress()
        result = "WON ✓" if battle.won else "LOST ✗"

        game_py   = self._python_call_count - self._battle_start_py
        game_rust = self._rust_call_count   - self._battle_start_rust
        game_total = game_py + game_rust

        print(f"\n{'='*60}")
        print(f"BATTLE OVER — {result} in {battle.turn} turns")
        print(f"  Python decisions: {game_py}")
        print(f"  Rust decisions:   {game_rust}")
        if game_total > 0:
            print(f"  Rust involvement: {int(game_rust / game_total * 100)}% of turns")
        print(f"{'='*60}")

        super()._battle_finished_callback(battle)
        self._games_finished += 1
        if battle.won:
            self._wins += 1
        losses = self._games_finished - self._wins
        if self._total_games:
            print(f"📈 Progress: {self._games_finished}/{self._total_games} "
                  f"({self._wins}W / {losses}L)")
        else:
            print(f"📈 Record: {self._wins}W / {losses}L "
                  f"({self._games_finished} games)")

        cum_total = self._python_call_count + self._rust_call_count
        if cum_total > 0:
            print(f"  Python decisions: {self._python_call_count}")
            print(f"  Rust decisions:   {self._rust_call_count}")
            print(f"  Rust involvement: {int(self._rust_call_count / cum_total * 100)}% cumulative")
        print(f"{'='*60}\n")


# =============================================================================
# LOCAL OPPONENT
# =============================================================================

class FilteredRandomPlayer(Player):
    def choose_move(self, battle):
        real_moves = [m for m in battle.available_moves if m.id != 'struggle']
        if real_moves:
            return self.create_order(random.choice(real_moves))
        if battle.available_switches:
            return self.create_order(random.choice(battle.available_switches))
        return self.choose_default_move()


# =============================================================================
# HELPERS
# =============================================================================

def random_suffix(length=6):
    return ''.join(random.choices(string.ascii_lowercase, k=length))


def load_latest_team(format_name="ou"):
    def iteration_num(path):
        m = re.search(r'_(\d+)\.txt$', path)
        return int(m.group(1)) if m else 0

    files = sorted(
        glob.glob(f"teams/team_{format_name}_iteration_*.txt"),
        key=iteration_num
    )
    if files:
        latest = files[-1]
        print(f"📂 Using team: {latest}")
        with open(latest) as f:
            return f.read()

    legacy = sorted(glob.glob(f"team_{format_name}_iteration_*.txt"), key=iteration_num)
    if legacy:
        latest = legacy[-1]
        print(f"📂 Using team (legacy path): {latest}")
        with open(latest) as f:
            return f.read()

    fallback = f"current_team_{format_name}.txt"
    print(f"📂 No iteration files found, using {fallback}")
    with open(fallback) as f:
        return f.read()


class Tee:
    def __init__(self, filepath):
        self.file = open(filepath, 'w')
        self.stdout = sys.stdout
        self._suppress_console = False
        sys.stdout = self

    def write(self, data):
        self.file.write(data)
        self.file.flush()
        if not self._suppress_console:
            self.stdout.write(data)

    def flush(self):
        self.stdout.flush()
        self.file.flush()

    def close(self):
        sys.stdout = self.stdout
        self.file.close()

    def log_only(self, msg):
        self.file.write(msg + '\n')
        self.file.flush()


# =============================================================================
# LOCAL PLAY ENTRY POINT
# =============================================================================

async def run_competitive_local(team, n_battles=1):
    print(f"\n🏆 Competitive player starting ({n_battles} battle(s), local)\n")

    player = CompetitivePlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(f"CompBot_{random_suffix()}", None),
        log_level=POKE_ENV_LOG_LEVEL,
    )
    opponent = FilteredRandomPlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(f"RandOpp_{random_suffix()}", None),
        log_level=POKE_ENV_LOG_LEVEL,
    )

    await player.battle_against(opponent, n_battles=n_battles)

    wins = sum(1 for b in player.battles.values() if b.won)
    print(f"\n📊 Final: {wins}/{n_battles} wins")
    print(f"   Python decisions: {player._python_call_count}")
    print(f"   Rust decisions:   {player._rust_call_count}")

    player._rust_engine.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run competitive Gen 1 OU player (local)")
    parser.add_argument("--format",  default="ou")
    parser.add_argument("--battles", type=int, default=1)
    args = parser.parse_args()

    import os as _os
    _os.makedirs("live_logs", exist_ok=True)
    existing = glob.glob("live_logs/competitive_log_*.txt")
    def _num(p):
        m = re.search(r'_(\d+)\.txt$', p)
        return int(m.group(1)) if m else 0
    next_num = max((_num(p) for p in existing), default=0) + 1
    log_path = f"live_logs/competitive_log_{next_num:03d}.txt"

    tee = Tee(log_path)
    print(f"📝 Logging to: {log_path}")

    team = load_latest_team(args.format)
    print(f"Running {args.battles} battle(s)...\n")

    try:
        asyncio.run(run_competitive_local(team, n_battles=args.battles))
    finally:
        tee.close()
        print(f"\nLog saved to: {log_path}")
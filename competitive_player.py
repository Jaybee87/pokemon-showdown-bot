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
import time

from poke_env.player import Player
from poke_env import LocalhostServerConfiguration, AccountConfiguration

from config import POKE_ENV_LOG_LEVEL
from gen1_engine import (
    type_effectiveness, get_pokemon_types, best_move_effectiveness,
    worst_incoming_effectiveness, find_best_switch, resolve_move_types,
    register_move_type, get_move_type,
    calc_damage_pct, can_ko, find_ko_move, outspeeds, get_speed,
    evaluate_matchup, find_best_matchup_switch,
    freeze_chance_value, get_substitute_hp, can_break_substitute,
    FIXED_DAMAGE_MOVES, OHKO_MOVES, SLEEP_MOVES, IGNORE_MOVES,
)
from rust_engine_bridge import RustEngine, build_state, action_to_poke_env


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
    Time budget ceiling: 12,000ms per turn (leaves headroom in bank)
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
        # Sleep move tracking: species.lower() → move_id attempted
        # Prevents re-firing a sleep move that missed last turn.
        self._sleep_attempted_vs: dict = {}
        self._last_rust_result:   dict = {}
        self._last_healed_turn:   int  = -99
        self._last_healed_species: str = ""
        self._last_healed_hp_frac: float = 1.0   # HP at time of last heal (tox race check)
        self._last_switched_in_turn: int  = -99   # switch cooldown
        self._last_switched_in_species: str = ""  # which mon just switched in
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
        import sys
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
                    if not move_type:
                        try:
                            from poke_env.environment.move import Move
                            move_obj = Move(move_name, gen=1)
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

        import sys as _sys
        _tee = _sys.stdout
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
            self._sleep_attempted_vs   = {}
            self._last_healed_turn     = -99
            self._last_healed_species  = ""
            self._last_healed_hp_frac  = 1.0
            self._last_switched_in_turn    = -99
            self._last_switched_in_species = ""
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

        # Clear sleep attempt record when opponent is confirmed asleep — move landed.
        if opp_status_now and opp_status_now.name == 'SLP':
            self._sleep_attempted_vs.pop(opp_species, None)

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

        # ==================================================================
        # STEP 1 — Recharge lock (no real choice)
        # ==================================================================
        if len(all_moves) == 1 and all_moves[0].id == 'recharge':
            print(f"  ⏳ PYTHON: recharge turn")
            self._python_call_count += 1
            return self.create_order(all_moves[0])

        # ==================================================================
        # STEP 2 — Forced: no moves and no switches
        # ==================================================================
        if not real_moves and not switches:
            print("  🔒 FORCED: no options, using default")
            self._python_call_count += 1
            return self.choose_default_move()

        # ==================================================================
        # STEP 3 — Faint switch: Rust picks the send-in
        # ==================================================================
        if not real_moves and switches:
            if len(switches) == 1:
                print(f"  🔀 FORCED SWITCH: only {switches[0].species} left")
                self._python_call_count += 1
                return self.create_order(switches[0])

            opp_known_types = self._opponent_move_types.get(opp_poke.species, [])
            print(f"\n  ⚙️  RUST ENGINE (faint switch)")
            our_alive  = sum(1 for p in battle.team.values() if not p.fainted)
            opp_alive  = sum(1 for p in battle.opponent_team.values() if not p.fainted)
            faint_time_ms = self._time_manager.allocate(
                battle_turn    = battle.turn,
                our_alive      = our_alive,
                opp_alive      = opp_alive,
                our_hp_frac    = my_hp_frac,
                opp_hp_frac    = opp_hp_frac,
                is_faint_switch= True,
            )
            faint_iters = max(3000, int(faint_time_ms * 4000 / 1000))
            rust_order = self._try_rust_faint_switch(battle, switches,
                                                     time_ms=faint_time_ms,
                                                     iterations=faint_iters)
            if rust_order:
                self._time_manager.end_turn()
                return rust_order

            # Python fallback
            best = find_best_switch(battle)
            print(f"  🔀 PYTHON FAINT SWITCH FALLBACK: {best.species}")
            self._python_call_count += 1
            return self.create_order(best)

        # ==================================================================
        # STEP 4 — We are asleep: switch out cleanly or queue best move
        # ==================================================================
        if my_is_asleep:
            if switches:
                best_sw = find_best_switch(battle)
                if best_sw:
                    sw_hp     = best_sw.current_hp_fraction or 0
                    sw_asleep = best_sw.status and best_sw.status.name == 'SLP'
                    # Don't switch to another sleeping mon or a nearly-dead mon
                    if not sw_asleep and not (len(switches) == 1 and sw_hp < 0.30):
                        print(f"  💤 PYTHON: asleep — switching to {best_sw.species}")
                        self._python_call_count += 1
                        return self.create_order(best_sw)
            if real_moves:
                best_asleep, _ = best_move_effectiveness(
                    real_moves, opp_types, attacker_types=my_types
                )
                fallback = best_asleep or real_moves[0]
                print(f"  💤 PYTHON: asleep — queuing {fallback.id}")
                self._python_call_count += 1
                return self.create_order(fallback)

        # ==================================================================
        # STEP 5 — Guaranteed KO: Python math is certain, no need for search
        # (Only non-Hyperbeam KOs — HB recharge risk is a search problem)
        # ==================================================================
        my_boosts  = dict(my_poke.boosts)  if my_poke.boosts  else {}
        opp_boosts = dict(opp_poke.boosts) if opp_poke.boosts else {}
        my_burned  = my_status_now and my_status_now.name == 'BRN'

        opp_has_reflect     = False
        opp_has_lightscreen = False
        try:
            try:
                from poke_env.battle.side_condition import SideCondition
            except ImportError:
                from poke_env.environment.side_condition import SideCondition
            opp_has_reflect     = SideCondition.REFLECT      in battle.opponent_side_conditions
            opp_has_lightscreen = SideCondition.LIGHT_SCREEN in battle.opponent_side_conditions
        except (ImportError, AttributeError):
            for sc in battle.opponent_side_conditions:
                sc_name = sc.name if hasattr(sc, 'name') else str(sc)
                if 'REFLECT'     in sc_name.upper(): opp_has_reflect     = True
                if 'LIGHT'       in sc_name.upper(): opp_has_lightscreen = True

        calc_kwargs = {
            'atk_boosts':      my_boosts,
            'def_boosts':      opp_boosts,
            'reflect':         opp_has_reflect,
            'light_screen':    opp_has_lightscreen,
            'attacker_burned': my_burned,
        }

        # Guaranteed KO check — use min damage roll (use_avg=False) so "guaranteed"
        # means the WORST roll still kills.
        # Exclude Hyperbeam (recharge risk handled by Rust) and explosion/selfdestruct.
        # Also exclude Hyperbeam when we are below 15% HP — recharge turn is fatal.
        #
        # Speed+heal guard: if the opponent is faster AND has a revealed heal move,
        # they will Recover/Softboiled BEFORE our attack lands on the same turn.
        # The hp_pct we have is pre-heal — the actual HP when we hit will be ~50% higher.
        # In this case the GKO math is correct but the premise is wrong, so skip the gate.
        opp_revealed_heals = self._opponent_move_names.get(opp_species, [])
        opp_has_heal = any(m in opp_revealed_heals for m in ('recover', 'softboiled', 'rest'))
        opp_is_faster = not outspeeds(
            my_species, opp_species,
            a_par=(my_status_now and my_status_now.name == 'PAR'),
            b_par=(opp_poke.status and opp_poke.status.name == 'PAR'
                   if opp_poke.status else False)
        )
        skip_gko = opp_has_heal and opp_is_faster

        my_hp_too_low_for_hb = my_hp_frac < 0.15
        ko_move_obj = None
        if not skip_gko:
            for mv in real_moves:
                if mv.id in ('explosion', 'selfdestruct'):
                    continue
                if mv.id == 'hyperbeam' and my_hp_too_low_for_hb:
                    continue
                try:
                    is_ko = can_ko(
                        my_species, mv.id, opp_species,
                        hp_pct=opp_hp_frac,
                        use_avg=False,      # min roll — truly guaranteed
                        **calc_kwargs
                    )
                except Exception:
                    is_ko = False
                if is_ko:
                    ko_move_obj = mv
                    break
        if ko_move_obj:
            print(f"  🎯 PYTHON GUARANTEED KO: {ko_move_obj.id} finishes "
                  f"{opp_poke.species} at {int(opp_hp_frac*100)}%")
            self._python_call_count += 1
            return self.create_order(ko_move_obj)

        # ==================================================================
        # STEP 6 — Immune to all revealed opponent moves: stay in and hit
        # ==================================================================
        opp_known_types = self._opponent_move_types.get(opp_poke.species, [])
        if opp_known_types:
            worst_incoming = worst_incoming_effectiveness(opp_known_types, my_types)
            if worst_incoming == 0:
                best_move, _ = best_move_effectiveness(
                    real_moves, opp_types, attacker_types=my_types
                )
                if best_move:
                    print(f"  🛡️  PYTHON: immune to all revealed moves — {best_move.id}")
                    self._python_call_count += 1
                    return self.create_order(best_move)

        # ==================================================================
        # STEP 7 — Sleep move: opponent has no status, we have a sleep move
        # (Deterministic — always correct when conditions are met)
        # Track attempts per species so we don't re-fire if the move missed.
        # ==================================================================
        if not self._sleep_clause_active and not opp_status_now and not _opp_has_sub:
            sleep_move = next(
                (m for m in real_moves if m.id in SLEEP_MOVES), None
            )
            already_tried = self._sleep_attempted_vs.get(opp_species)
            if sleep_move and already_tried != sleep_move.id:
                self._sleep_attempted_vs[opp_species] = sleep_move.id
                print(f"  😴 PYTHON: using {sleep_move.id}")
                self._python_call_count += 1
                return self.create_order(sleep_move)

        # ==================================================================
        # STEP 7b/c/e — Unified heal filter.
        #
        # All heal logic in one place with clear precedence. Applies to
        # all Gen 1 heal moves: softboiled, recover, rest.
        #
        # ALWAYS heal (fire immediately before Rust):
        #   - Toxic/Poison: escalating damage, cure now regardless of HP
        #   - HP < 40% with Softboiled/Recover available: one hit from death
        #
        # NEVER offer to Rust (strip from move list):
        #   - Rest at ≥ 85% HP without poison: 2-turn sleep for near-zero gain
        #   - Any heal within 3 turns of last heal AND HP ≥ 80%: spam prevention
        #   - Any heal when HP ≥ 80% AND we're up 2+ mons: press the win
        #
        # Everything else (40–85% HP, no active status forcing action):
        #   defer to Rust — it has context we don't.
        # ==================================================================
        HEAL_MOVES = ('softboiled', 'recover', 'rest')
        my_is_tox  = my_status_now and my_status_now.name in ('TOX', 'PSN')
        my_is_par  = my_status_now and my_status_now.name == 'PAR'

        # ALWAYS: Toxic/Poison — heal immediately, BUT only when recovery
        # can actually help. Gen 1 Toxic escalates: counter N → N/16 HP lost
        # per turn. Recover restores 50%. Once the counter hits 9+, Toxic
        # drains more than 56%/turn — recovery can never catch up and we just
        # waste turns. The correct play at that point is to switch or attack.
        #
        # Futility check: if we healed last turn AND HP is still lower than
        # it was before that heal (net negative), recovery has failed — stop.
        # Also cap at tox_counter >= 9 (>50%/turn drain beats 50% recovery).
        if my_is_tox:
            heal_move = next((m for m in real_moves if m.id in HEAL_MOVES), None)
            if heal_move:
                tox_counter = getattr(my_poke, 'toxic_turn_counter',
                                      getattr(my_poke, 'toxic_turns_left',
                                      None))
                # poke-env sometimes exposes this as n_turns_statused for TOX
                if tox_counter is None:
                    try:
                        tox_counter = battle.active_pokemon.n_turns_statused
                    except AttributeError:
                        tox_counter = None

                # Hard cap: counter >= 9 means ≥56.25% drain vs 50% recovery
                tox_is_futile = (tox_counter is not None and tox_counter >= 9)

                # Soft cap: if we healed last turn and HP is still trending down,
                # recovery is not keeping up — hand off to Rust
                healed_last_turn = (
                    self._last_healed_turn == battle.turn - 1
                    and self._last_healed_species == my_species
                )
                tox_losing_race = (
                    healed_last_turn
                    and my_hp_frac < self._last_healed_hp_frac - 0.05
                )

                if not tox_is_futile and not tox_losing_race:
                    print(f"  💊 PYTHON: Toxiced — healing with {heal_move.id}")
                    self._last_healed_turn     = battle.turn
                    self._last_healed_species  = my_species
                    self._last_healed_hp_frac  = my_hp_frac
                    self._python_call_count   += 1
                    return self.create_order(heal_move)
                elif tox_is_futile:
                    print(f"  🚫 PYTHON: Toxic futile (counter={tox_counter}, "
                          f"drain>{50:.0f}%) — deferring to Rust")
                else:
                    print(f"  🚫 PYTHON: Toxic recovery losing race "
                          f"(HP {int(self._last_healed_hp_frac*100)}%→{int(my_hp_frac*100)}% after heal) "
                          f"— deferring to Rust")

        # ALWAYS: low HP — but only if we're actually in danger this turn
        # AND healing can actually help us win.
        heal_move_nontox = next(
            (m for m in real_moves if m.id in ('softboiled', 'recover')), None
        )
        if heal_move_nontox and my_hp_frac < 0.55:
            turns_since_heal = battle.turn - self._last_healed_turn
            same_species     = (self._last_healed_species == my_species)
            consecutive_heals = turns_since_heal <= 2 and same_species

            # Never fire if we've healed 2+ consecutive turns — we're in a loop.
            # Rust should handle it; Python healing repeatedly achieves nothing.
            if consecutive_heals:
                pass  # fall through to Rust
            else:
                # Build opponent's max damage per turn from revealed moves
                opp_known_names = self._opponent_move_names.get(opp_species, [])
                opp_max_dmg = 0.0
                if opp_known_names:
                    for move_id in opp_known_names:
                        try:
                            lo, hi = calc_damage_pct(
                                opp_species, move_id, my_species,
                                atk_boosts=dict(opp_poke.boosts) if opp_poke.boosts else {},
                                def_boosts=dict(my_poke.boosts)  if my_poke.boosts  else {},
                            )
                            opp_max_dmg = max(opp_max_dmg, hi)
                        except Exception:
                            pass
                if opp_max_dmg == 0.0:
                    # No revealed moves — conservative fallback, but cap it
                    # at 0.25 (not 0.30) to reduce false positives.
                    opp_known_types = self._opponent_move_types.get(opp_species, [])
                    worst_eff = worst_incoming_effectiveness(opp_known_types, my_types)
                    opp_max_dmg = min(0.25 * worst_eff, 0.40)

                # Can-win check: if opponent also has a heal move, healing
                # into their heal is a treadmill — don't fire, let Rust decide.
                opp_has_heal = any(
                    m in self._opponent_move_names.get(opp_species, [])
                    for m in ('softboiled', 'recover', 'rest')
                )

                # Net gain check: Recover restores ~50%. If opp does more
                # than ~40% per turn, we're net negative every cycle — switch
                # is the right answer, not heal. Let Rust decide.
                heal_is_futile = opp_max_dmg > 0.42

                if not opp_has_heal and not heal_is_futile:
                    we_are_slower = not outspeeds(my_species, opp_species,
                                                  a_par=(my_is_par),
                                                  b_par=(opp_poke.status and
                                                         opp_poke.status.name=='PAR'
                                                         if opp_poke.status else False))
                    hits_before_heal = 2 if we_are_slower else 1

                    par_scale = 1.0
                    if my_is_par:
                        par_success_prob = 0.75 ** hits_before_heal
                        par_scale = 1.0 / par_success_prob

                    danger_threshold = opp_max_dmg * hits_before_heal * 1.2 * par_scale

                    if my_hp_frac <= danger_threshold:
                        opp_alive = sum(1 for p in battle.opponent_team.values() if not p.fainted)
                        our_alive  = sum(1 for p in battle.team.values() if not p.fainted)
                        if not (opp_alive == 1 and our_alive >= 2):
                            print(f"  💊 PYTHON: danger heal ({int(my_hp_frac*100)}% HP, "
                                  f"opp max ~{int(opp_max_dmg*100)}%/turn, "
                                  f"{'slower' if we_are_slower else 'faster'}"
                                  f"{f', PAR ×{par_scale:.2f}' if my_is_par else ''}) "
                                  f"— healing with {heal_move_nontox.id}")
                            self._last_healed_turn    = battle.turn
                            self._last_healed_species = my_species
                            self._python_call_count  += 1
                            return self.create_order(heal_move_nontox)

        # NEVER (strip from move list before Rust):
        heal_moves_available = [m for m in real_moves if m.id in HEAL_MOVES]
        if heal_moves_available:
            opp_alive = sum(1 for p in battle.opponent_team.values() if not p.fainted)
            our_alive  = sum(1 for p in battle.team.values() if not p.fainted)
            turns_since_heal = battle.turn - self._last_healed_turn
            same_species     = (self._last_healed_species == my_species)

            strip_heals = {}  # move_id → reason string
            for hm in heal_moves_available:
                # Rest at high HP without poison: 2-turn sleep cost is too high.
                # Instant heals (Softboiled/Recover) use a higher threshold since
                # they don't incapacitate. When paralysed, PAR chip (~10%/turn)
                # erodes HP quickly so we allow healing until 95% (not 90%).
                if hm.id == 'rest':
                    hp_threshold = 0.85
                elif my_is_par:
                    hp_threshold = 0.95  # PAR chip will eat the margin fast
                else:
                    hp_threshold = 0.90
                if my_hp_frac >= hp_threshold and not my_is_tox:
                    strip_heals[hm.id] = f"{int(my_hp_frac*100)}% HP ≥ {int(hp_threshold*100)}%"
                # Heal spam: healed same mon within last 3 turns and HP still high
                elif turns_since_heal <= 3 and same_species and my_hp_frac >= 0.80:
                    strip_heals[hm.id] = f"healed {turns_since_heal}t ago"
                # Up on mons significantly: press the advantage, don't stall
                elif my_hp_frac >= 0.80 and our_alive >= opp_alive + 2:
                    strip_heals[hm.id] = f"up {our_alive - opp_alive} mons"

            if strip_heals:
                filtered = [m for m in real_moves if m.id not in strip_heals]
                if filtered:  # only strip if alternatives exist
                    for mv_id, reason in sorted(strip_heals.items()):
                        print(f"  🚫 PYTHON: suppressing {mv_id} ({reason})")
                    real_moves = filtered

        # ==================================================================
        # STEP 7e — Recover stall prevention: if opponent has Recover and
        # we cannot 2HKO them, Thunder Wave is higher value than attacking.
        # Paralysing a Recover user cuts their speed and creates win conditions
        # through PAR immobilisation. Without T-Wave the matchup is unwinnable
        # by damage alone.
        # ==================================================================
        opp_has_recover = any(
            m in self._opponent_move_names.get(opp_species, [])
            for m in ('recover', 'softboiled', 'rest')
        )
        if opp_has_recover and not (opp_poke.status and opp_poke.status.name == 'PAR'):
            twave = next((m for m in real_moves if m.id == 'thunderwave'), None)
            if twave:
                # Check if we can 2HKO (deal >50% per hit)
                best_dmg = 0.0
                for mv in real_moves:
                    if mv.id in ('thunderwave', 'recover', 'softboiled', 'rest'):
                        continue
                    try:
                        lo, hi = calc_damage_pct(
                            my_species, mv.id, opp_species,
                            atk_boosts=dict(my_poke.boosts) if my_poke.boosts else {},
                            def_boosts=dict(opp_poke.boosts) if opp_poke.boosts else {},
                        )
                        best_dmg = max(best_dmg, (lo + hi) / 2)
                    except Exception:
                        pass
                if best_dmg < 0.50:
                    # Can't 2HKO — T-Wave is the winning move
                    print(f"  ⚡ PYTHON: opponent has Recover, can't 2HKO "
                          f"(best ~{int(best_dmg*100)}%) — Thunder Wave")
                    self._python_call_count += 1
                    return self.create_order(twave)
        # ==================================================================
        if my_is_par and any(m.id == 'hyperbeam' for m in real_moves):
            non_hb = [m for m in real_moves if m.id != 'hyperbeam']
            if any((m.base_power or 0) > 0 for m in non_hb):
                real_moves = non_hb
                print(f"  🚫 PYTHON: paralysed — suppressing Hyperbeam")

        # ==================================================================
        # STEP 8 — RUST ENGINE
        # Everything else: switch timing, Hyperbeam risk/reward, Thunder Wave,
        # matchup evaluation, stall breaks, damage races, late-game decisions.
        # ==================================================================
        # Pass the filtered move list so suppressed moves (e.g. HB when PAR)
        # are never visible to the search.
        active_move_ids = [m.id for m in real_moves]

        # Allocate compute time based on position complexity
        our_alive  = sum(1 for p in battle.team.values() if not p.fainted)
        opp_alive  = sum(1 for p in battle.opponent_team.values() if not p.fainted)
        time_budget_ms = self._time_manager.allocate(
            battle_turn    = battle.turn,
            our_alive      = our_alive,
            opp_alive      = opp_alive,
            our_hp_frac    = my_hp_frac,
            opp_hp_frac    = opp_hp_frac,
            is_faint_switch= False,
        )
        print(f"  ⏱  Time budget: {time_budget_ms}ms ({self._time_manager.status()})")

        # Dynamic iteration cap: nodes/sec × budget.
        # Measured throughput is ~4000 nodes/sec on the 9800X3D with 6 threads.
        # Setting iterations = budget_ms * 4 means the time limit and iteration
        # cap both fire at approximately the same point — no idle time wasted.
        # Floor of 3000 preserves minimum quality on very short budgets.
        NODES_PER_SEC = 57000
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

        # Post-process: veto Hyperbeam when position is deeply losing
        # BUT: never veto a guaranteed KO — if they die, there's no recharge turn.
        # BUT: never veto when opponent is asleep — a sleeping opponent can't move
        # during our recharge turn, so the recharge risk is zero.
        last = getattr(self, '_last_rust_result', {})
        opp_is_asleep = opp_poke.status and opp_poke.status.name == 'SLP'
        if (last.get('action', {}).get('id') == 'hyperbeam'
                and last.get('score', 0) < -2000
                and not opp_is_asleep):
            # Check if this is a guaranteed KO before vetoing
            hb_is_guaranteed_ko = can_ko(
                my_species, 'hyperbeam', opp_species,
                hp_pct=opp_hp_frac, use_avg=False,  # use min roll = guaranteed
                atk_boosts=dict(my_poke.boosts) if my_poke.boosts else {},
                def_boosts=dict(opp_poke.boosts) if opp_poke.boosts else {},
            ) if opp_hp_frac > 0 else False
            if not hb_is_guaranteed_ko:
                alt = next(
                    (m for m in real_moves
                     if m.id != 'hyperbeam' and m.base_power and m.base_power > 0),
                    None
                )
                if alt:
                    print(f"  🚫 PYTHON: vetoing Hyperbeam (score={last['score']:.0f}, losing) "
                          f"— using {alt.id} instead")
                    self._python_call_count += 1
                    return self.create_order(alt)

        # Post-process: switch cooldown — if the active mon switched in last
        # turn, don't immediately switch out again unless HP is below 50%.
        # Repeated pivoting wastes turns and chips our own mons on switch-in damage.
        # EXCEPTION: disabled when position is deeply losing (score < -3000) —
        # in that case the search found a switch as the recovery line and we
        # should trust it. 7 cases in logs where this blocked a score=-8976 escape.
        last_action = last.get('action', {})
        last_action_id = last_action.get('id', '')
        last_score = last.get('score', 0)
        if last_action.get('type') == 'switch' and last_score > -3000:
            just_switched_in = (
                self._last_switched_in_species == my_species
                and battle.turn - self._last_switched_in_turn == 1
            )
            if just_switched_in and my_hp_frac > 0.50 and real_moves:
                best_move, _ = best_move_effectiveness(
                    real_moves, opp_types, attacker_types=my_types
                )
                if best_move:
                    print(f"  🚫 PYTHON: switch cooldown (just switched in) "
                          f"— attacking with {best_move.id}")
                    self._python_call_count += 1
                    return self.create_order(best_move)

        # Track heals so the unified heal filter above can apply the
        # spam-prevention cooldown next turn.
        if last_action_id in ('softboiled', 'recover', 'rest'):
            self._last_healed_turn    = battle.turn
            self._last_healed_species = my_species

        # Track the switch we're about to make
        if last_action.get('type') == 'switch':
            target_species = last_action.get('species', '').lower()
            self._last_switched_in_turn    = battle.turn
            self._last_switched_in_species = target_species

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
            state = build_state(battle, sleep_turns=self._sleep_turns)
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

    def _hard_fallback_dupe(self, battle, real_moves, switches):
        """Duplicate — kept for reference only."""
        my_poke  = battle.active_pokemon
        opp_poke = battle.opponent_active_pokemon
        my_types  = get_pokemon_types(my_poke)
        opp_types = get_pokemon_types(opp_poke)

        print(f"\n  🔄 HARD FALLBACK: rust unavailable")
        best_move, _ = best_move_effectiveness(
            real_moves, opp_types, attacker_types=my_types
        )
        if best_move:
            self._python_call_count += 1
            return self.create_order(best_move)
        return self.choose_default_move()

    def _try_rust_faint_switch(self, battle, switches, time_ms: int = None,
                               iterations: int = None):
        """
        Query Rust specifically for a faint switch.
        Builds a state where our active has no moves (switch-only position)
        so Rust returns a switch action, not a move.
        """
        try:
            state = build_state(battle, sleep_turns=self._sleep_turns)
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
        import sys
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
        import sys
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
"""
competitive_player.py
=====================
Competitive Gen 1 OU player using a hybrid Python/Rust/LLM decision engine.

Decision hierarchy — Python only handles mechanical certainties.
Rust handles everything strategic.

  1. FORCED      — only one legal action (struggle / recharge / single switch)
  2. RECHARGE    — locked after Hyper Beam, no choice
  3. ASLEEP      — we can't act; queue best move or switch if clean
  4. FAINT SWITCH — Rust picks the send-in; LLM fallback only on error
  5. GUARANTEED KO — Python math says we finish them this turn → do it
  6. IMMUNE       — opponent known moves do 0x to us → stay in and hit
  7. SLEEP MOVE   — opponent has no status and we have a sleep move → use it
  8. RUST ENGINE  — all other decisions: switch timing, Hyperbeam risk,
                    status moves, matchup evaluation, stall breaks
  9. LLM          — last resort if Rust errors

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

from poke_env.player import Player
from poke_env import LocalhostServerConfiguration, AccountConfiguration

from config import LLM_MODEL, LLM_TIMEOUT_SECONDS, POKE_ENV_LOG_LEVEL
from gen1_engine import (
    type_effectiveness, get_pokemon_types, best_move_effectiveness,
    worst_incoming_effectiveness, find_best_switch, resolve_move_types,
    register_move_type, get_move_type,
    calc_damage_pct, can_ko, find_ko_move, outspeeds, get_speed,
    evaluate_matchup, find_best_matchup_switch,
    freeze_chance_value, get_substitute_hp, can_break_substitute,
    FIXED_DAMAGE_MOVES, OHKO_MOVES, SLEEP_MOVES, LLM_ONLY_MOVES, IGNORE_MOVES,
)
from llm_bridge import (
    call_llm, call_llm_async, strip_think_tags,
    parse_battle_decision, parse_lead_choice, ensure_ollama_running,
)
from rust_engine_bridge import RustEngine, build_state, action_to_poke_env


# =============================================================================
# LLM PROMPT BUILDER
# =============================================================================

def build_battle_prompt(battle, available_moves, available_switches,
                        opponent_move_types, reason_for_ambiguity):
    my_poke  = battle.active_pokemon
    opp_poke = battle.opponent_active_pokemon
    my_types  = get_pokemon_types(my_poke)
    opp_types = get_pokemon_types(opp_poke)
    my_hp  = int(my_poke.current_hp_fraction * 100)
    opp_hp = int((opp_poke.current_hp_fraction or 1.0) * 100)

    def status_label(poke):
        parts = []
        if poke.status:
            name = poke.status.name
            descriptions = {
                'SLP': 'ASLEEP (cannot move)',
                'PAR': 'PARALYZED (25% chance to not move, Speed/4)',
                'PSN': 'POISONED (loses HP each turn)',
                'TOX': 'BADLY POISONED (escalating HP loss)',
                'BRN': 'BURNED (loses HP, Attack halved)',
                'FRZ': 'FROZEN (cannot move)',
            }
            parts.append(descriptions.get(name, name))
        for eff in (poke.effects or {}):
            if hasattr(eff, 'name') and eff.name == 'CONFUSION':
                parts.append('CONFUSED (may hurt itself)')
                break
        return ' + '.join(parts) if parts else 'none'

    my_status  = status_label(my_poke)
    opp_status = status_label(opp_poke)

    move_lines = []
    for m in available_moves:
        if m.id == 'struggle':
            continue
        mtype = m.type.name.lower() if m.type else 'normal'
        bp    = m.base_power or 0
        stab  = mtype in my_types
        stab_note = ' +STAB(1.5x)' if stab else ''
        if m.id in FIXED_DAMAGE_MOVES:
            eff_label = 'deals fixed damage (~100 dmg at L100)'
        elif m.id in OHKO_MOVES:
            eff_label = 'OHKO move (if it hits)'
        else:
            eff = type_effectiveness(mtype, opp_types)
            eff_label = (
                f'SUPER EFFECTIVE ({eff}x)' if eff > 1
                else f'not very effective ({eff}x)' if 0 < eff < 1
                else 'NO EFFECT (immune)' if eff == 0
                else 'neutral (1x)'
            )
        extra = ''
        if m.id == 'hyperbeam':
            extra = ' ⚠️ USER MUST RECHARGE NEXT TURN'
        if m.id in ('explosion', 'selfdestruct'):
            extra = ' ⚠️ USER FAINTS AFTER USE'
        move_lines.append(
            f'  {m.id:18s} type:{mtype:10s} bp:{bp:3d}{stab_note:14s} vs opp: {eff_label}{extra}'
        )

    switch_lines = []
    opp_known_types = opponent_move_types.get(opp_poke.species, [])
    for p in available_switches:
        ptypes = get_pokemon_types(p)
        php    = int(p.current_hp_fraction * 100)
        pst    = status_label(p)
        if opp_known_types:
            worst = worst_incoming_effectiveness(opp_known_types, ptypes)
            threat_str = (
                f'DANGER: takes {worst}x from opp moves' if worst > 1
                else f'resists opp moves ({worst}x)' if worst < 1
                else 'neutral vs opp moves'
            )
        else:
            threat_str = 'opp moveset unknown'
        status_str_p = f' [{pst}]' if pst != 'none' else ''
        switch_lines.append(
            f'  {p.species:14s} ({"/".join(ptypes):16s}) {php:3d}% HP{status_str_p} | {threat_str}'
        )

    opp_team_lines = []
    for p in battle.opponent_team.values():
        ptypes = get_pokemon_types(p)
        if p.fainted:
            opp_team_lines.append(f'  {p.species}: FAINTED')
        elif p == opp_poke:
            opp_team_lines.append(
                f'  {p.species} ({"/".join(ptypes)}): {opp_hp}% HP [ACTIVE] status:{opp_status}'
            )
        else:
            opp_team_lines.append(
                f'  {p.species} ({"/".join(ptypes)}): {int((p.current_hp_fraction or 1)*100)}% HP'
            )

    my_team_lines = []
    for p in battle.team.values():
        ptypes = get_pokemon_types(p)
        if p.fainted:
            my_team_lines.append(f'  {p.species}: FAINTED')
        elif p == my_poke:
            my_team_lines.append(
                f'  {p.species} ({"/".join(ptypes)}): {my_hp}% HP [ACTIVE] status:{my_status}'
            )
        else:
            pst     = status_label(p)
            pst_str = f' [{pst}]' if pst != 'none' else ''
            my_team_lines.append(
                f'  {p.species} ({"/".join(ptypes)}): {int(p.current_hp_fraction*100)}% HP{pst_str}'
            )

    known_str = ', '.join(opp_known_types) if opp_known_types else 'none revealed yet'

    prompt = f"""You are a Gen 1 competitive Pokemon battle AI making a single battle decision.

════════════════════════════════════════
TURN {battle.turn}
════════════════════════════════════════

MY ACTIVE:   {my_poke.species.upper()} | Type: {" / ".join(t.upper() for t in my_types)} | HP: {my_hp}% | Status: {my_status}
OPP ACTIVE:  {opp_poke.species.upper()} | Type: {" / ".join(t.upper() for t in opp_types)} | HP: {opp_hp}% | Status: {opp_status}

OPPONENT'S REVEALED MOVE TYPES: {known_str}

────────────────────────────────────────
MY MOVE OPTIONS:
{chr(10).join(move_lines) if move_lines else "  (none — must switch)"}

MY SWITCH OPTIONS:
{chr(10).join(switch_lines) if switch_lines else "  (none — must attack)"}

────────────────────────────────────────
MY TEAM:
{chr(10).join(my_team_lines)}

OPPONENT'S KNOWN TEAM:
{chr(10).join(opp_team_lines) if opp_team_lines else "  (unknown)"}

────────────────────────────────────────
GEN 1 KEY RULES:
- Ghost immune to Normal+Fighting | Ground immune to Electric | Psychic 2x vs Fighting+Poison
- STAB: moves matching user type deal 1.5x damage
- Hyper Beam: user MUST recharge next turn. Don't use if opponent can KO on free turn.
- Paralysis: 25% chance fully immobilized each turn. Speed/4.
- Sleep: cannot move at all.
- Switching costs your entire turn.
- Thunder Wave: cannot miss. Very high value vs fast threats.

════════════════════════════════════════
SITUATION: {reason_for_ambiguity}
════════════════════════════════════════

Analyse the battle state, then output ONE decision:
DECISION: move <moveid>      e.g. DECISION: move thunderbolt
DECISION: switch <species>   e.g. DECISION: switch chansey

Use ONLY the exact move IDs and species names listed above. Output DECISION on its own line at the end."""

    valid_move_ids   = {m.id.lower() for m in available_moves if m.id != 'struggle'}
    valid_switch_ids = {p.species.lower() for p in available_switches}
    return prompt, valid_move_ids, valid_switch_ids


# =============================================================================
# COMPETITIVE PLAYER
# =============================================================================

class CompetitivePlayer(Player):

    def __init__(self, *args, verbose=True, live_timeout=None,
                 total_games=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._opponent_move_types = {}
        self._opponent_move_names = {}
        self._llm_call_count      = 0
        self._python_call_count   = 0
        self._rust_call_count     = 0
        self._verbose             = verbose
        self._current_battle_tag  = None
        self._llm_timeout         = live_timeout
        self._total_games         = total_games
        self._games_finished      = 0
        self._wins                = 0
        self._battle_start_py     = 0
        self._battle_start_llm    = 0
        self._battle_start_rust   = 0
        self._sleep_clause_active = False
        self._last_rust_count     = 0
        self._last_llm_count      = 0
        # Sleep turn tracking: species.lower() → turns_asleep (increments each turn)
        # Used to give Rust an accurate sleep duration estimate.
        self._sleep_turns: dict        = {}
        # Sleep move tracking: species.lower() → move_id attempted
        # Prevents re-firing a sleep move that missed last turn.
        self._sleep_attempted_vs: dict = {}
        self._last_rust_result:   dict = {}
        self._rust_engine = RustEngine(
            algorithm="auto",
            depth=4,
            iterations=800,
            time_ms=200,
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
            indicator = "⚙️" if source == "rust" else ("🤖" if source == "llm" else "⚡")
            print(f"  {indicator} T{turn:02d} {my_poke}({my_hp}%) vs {opp_poke}({opp_hp}%) → {action} [{source}]")

    # -------------------------------------------------------------------------
    # Team preview — Rust picks the lead, LLM fallback
    # -------------------------------------------------------------------------

    async def teampreview(self, battle):
        my_team  = list(battle.team.values())
        opp_team = list(battle.opponent_team.values())

        def team_summary(pokemon_list):
            return '\n'.join(
                f"  {p.species} ({'/'.join(get_pokemon_types(p))})"
                for p in pokemon_list
            )

        valid_leads = [p.species.lower() for p in my_team]

        print(f"\n{'='*60}")
        print(f"TEAM PREVIEW")
        print(f"  My team:  {', '.join(p.species for p in my_team)}")
        print(f"  Opp team: {', '.join(p.species for p in opp_team)}")

        prompt = f"""You are a Gen 1 competitive Pokemon player choosing your lead for team preview.

MY TEAM:
{team_summary(my_team)}

OPPONENT'S TEAM:
{team_summary(opp_team)}

GEN 1 TEAM PREVIEW STRATEGY:
- Pick a lead with a good type matchup against the opponent's likely lead
- Gengar and Alakazam are common fast leads
- Chansey leads absorb hits and set up Thunder Wave early
- A fast Pokemon with Thunder Wave can cripple the opponent's lead immediately

Your available leads (use exact species name):
{', '.join(valid_leads)}

Think through the matchups, then end with exactly:
LEAD: <species>"""

        try:
            raw, err = await call_llm_async(prompt, timeout=self._llm_timeout)
            if err:
                print(f"  LLM error: {err}")
            elif raw:
                print(f"\n  💭 LLM LEAD REASONING:")
                for line in strip_think_tags(raw).split('\n'):
                    print(f"     {line}")
                chosen_species = parse_lead_choice(raw, valid_leads)
                if chosen_species:
                    chosen = next(
                        (p for p in my_team
                         if re.sub(r'[^a-z0-9]', '', p.species.lower()) == chosen_species),
                        None
                    )
                    if chosen:
                        order     = [chosen] + [p for p in my_team if p != chosen]
                        order_str = '/team ' + ''.join(str(my_team.index(p) + 1) for p in order)
                        print(f"\n  ✅ LLM LEAD: {chosen.species}")
                        return order_str
        except Exception as e:
            print(f"  LLM error during teampreview: {e}")

        print(f"  🔄 FALLBACK: defaulting to slot 1 ({my_team[0].species})")
        return self.random_teampreview(battle)

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
            elif self._llm_call_count > self._last_llm_count:
                source = "llm"
            self._last_rust_count = self._rust_call_count
            self._last_llm_count  = self._llm_call_count

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
            self._battle_start_llm  = self._llm_call_count
            self._battle_start_rust = self._rust_call_count
            self._sleep_clause_active  = False
            self._sleep_turns          = {}
            self._sleep_attempted_vs   = {}

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
            rust_order = self._try_rust_faint_switch(battle, switches)
            if rust_order:
                return rust_order

            # LLM fallback
            reason = (f"my Pokemon fainted — picking send-in vs "
                      f"{opp_poke.species} ({'/'.join(opp_types)})")
            print(f"  🤖 LLM FALLBACK (faint switch)")
            prompt, _, _ = build_battle_prompt(
                battle, [], switches, self._opponent_move_types, reason
            )
            raw, err = await call_llm_async(prompt, timeout=self._llm_timeout)
            if not err and raw:
                print(f"\n  💭 LLM REASONING:")
                for line in strip_think_tags(raw).split('\n'):
                    print(f"     {line}")
                _, action_id = parse_battle_decision(
                    raw,
                    set(),
                    {p.species.lower() for p in switches}
                )
                if action_id:
                    norm   = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
                    chosen = next(
                        (p for p in switches if norm(p.species) == norm(action_id)), None
                    )
                    if chosen:
                        print(f"  ✅ LLM FAINT SWITCH: {chosen.species}")
                        self._llm_call_count += 1
                        return self.create_order(chosen)

            best = find_best_switch(battle)
            print(f"  🔀 PYTHON FALLBACK: {best.species}")
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

        # Guaranteed KO check — exclude Hyperbeam (recharge risk needs search)
        # and explosion/selfdestruct (need eligibility check first).
        # Also exclude Hyperbeam when we are below 15% HP — at that HP level
        # the recharge turn will almost certainly be fatal.
        my_hp_too_low_for_hb = my_hp_frac < 0.15
        safe_ko_ids = [
            m.id for m in real_moves
            if m.id not in ('explosion', 'selfdestruct')
            and not (m.id == 'hyperbeam' and my_hp_too_low_for_hb)
        ]
        ko_result, ko_guaranteed = find_ko_move(
            my_species, safe_ko_ids, opp_species, opp_hp_frac, **calc_kwargs
        )
        if ko_result and ko_guaranteed:
            ko_move_obj = next((m for m in real_moves if m.id == ko_result), None)
            if ko_move_obj:
                print(f"  🎯 PYTHON GUARANTEED KO: {ko_result} finishes "
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
        # STEP 7b — Toxic heal: if we are Badly Poisoned, heal immediately.
        # ==================================================================
        if my_status_now and my_status_now.name == 'TOX':
            heal_move = next(
                (m for m in real_moves if m.id in ('softboiled', 'recover')), None
            )
            if heal_move:
                print(f"  💊 PYTHON: Toxiced — healing with {heal_move.id}")
                self._python_call_count += 1
                return self.create_order(heal_move)

        # ==================================================================
        # STEP 7c — Low-HP heal: any status + below 40% HP + heal available.
        # At this HP level, healing is almost always correct: we recover 50%
        # and force the opponent to deal 90%+ total damage to kill us, buying
        # at least one extra turn. The Rust eval's heal bonus isn't weighted
        # enough to beat raw damage scores — enforce this as a hard rule.
        # Exception: skip if the opponent is on their last mon and we can win
        # the damage race (don't stall when we're already winning).
        # ==================================================================
        if my_hp_frac < 0.40:
            heal_move = next(
                (m for m in real_moves if m.id in ('softboiled', 'recover')), None
            )
            if heal_move:
                opp_alive = sum(1 for p in battle.opponent_team.values() if not p.fainted)
                our_alive  = sum(1 for p in battle.team.values() if not p.fainted)
                # Skip healing only if we clearly win without it (last opp mon, we're ahead)
                skip_heal = (opp_alive == 1 and our_alive >= 2)
                if not skip_heal:
                    print(f"  💊 PYTHON: low HP ({int(my_hp_frac*100)}%) — healing with {heal_move.id}")
                    self._python_call_count += 1
                    return self.create_order(heal_move)

        # ==================================================================
        # STEP 7d — Paralysed + Hyperbeam suppression.
        # When we are paralysed, strip Hyperbeam from the move list before
        # passing to Rust. PAR means 25% fully-wasted turns; HB adds a
        # guaranteed recharge turn on top — two consecutive dead turns while
        # the opponent acts freely. Rust's eval penalties don't overcome the
        # raw damage scores. Only allow HB if no other damaging move exists.
        # ==================================================================
        my_is_par = my_status_now and my_status_now.name == 'PAR'
        if my_is_par and any(m.id == 'hyperbeam' for m in real_moves):
            non_hb = [m for m in real_moves if m.id != 'hyperbeam']
            has_damage_alt = any((m.base_power or 0) > 0 for m in non_hb)
            if has_damage_alt:
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
        rust_result = self._try_rust_engine(battle, active_move_ids=active_move_ids)

        if rust_result is None:
            return self._llm_fallback(battle, real_moves, switches)

        # Post-process: veto Hyperbeam when position is deeply losing
        last = getattr(self, '_last_rust_result', {})
        if (last.get('action', {}).get('id') == 'hyperbeam'
                and last.get('score', 0) < -2000):
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

        return rust_result

    def _llm_fallback(self, battle, real_moves, switches):
        """LLM fallback when Rust engine is unavailable or errored."""
        my_poke  = battle.active_pokemon
        opp_poke = battle.opponent_active_pokemon
        my_types  = get_pokemon_types(my_poke)
        opp_types = get_pokemon_types(opp_poke)

        reason_str = "rust engine unavailable — LLM fallback"
        print(f"\n  🤖 LLM FALLBACK: {reason_str}")

        best_move, _ = best_move_effectiveness(
            real_moves, opp_types, attacker_types=my_types
        )
        opp_known_names = self._opponent_move_names.get(opp_poke.species.lower(), [])
        print(f"     Opponent known moves: {opp_known_names or 'none'}")

        # Hard fallback — LLM is async but we're in a sync context here.
        # Return best move immediately to avoid blocking.
        if best_move:
            print(f"  🔄 HARD FALLBACK: {best_move.id}")
            self._python_call_count += 1
            return self.create_order(best_move)
        return self.choose_default_move()

    # -------------------------------------------------------------------------
    # Rust engine helpers
    # -------------------------------------------------------------------------

    def _try_rust_engine(self, battle, active_move_ids: list = None):
        """
        Query the Rust engine for a normal (non-faint) decision.
        active_move_ids: optional filtered list of move IDs to present to Rust.
                         If None, uses the full moveset from poke-env.
        """
        self._last_rust_result = {}
        try:
            state = build_state(battle, sleep_turns=self._sleep_turns)
            # Apply move suppression — replace active's moves with the filtered list
            if active_move_ids is not None:
                state["ours"]["active"]["moves"] = active_move_ids
            result = self._rust_engine.choose(state)
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

    def _llm_fallback(self, battle, real_moves, switches):
        """Hard fallback when Rust engine is unavailable or errored."""
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

    def _try_rust_faint_switch(self, battle, switches):
        """
        Query Rust specifically for a faint switch.
        Builds a state where our active has no moves (switch-only position)
        so Rust returns a switch action, not a move.
        """
        try:
            state = build_state(battle, sleep_turns=self._sleep_turns)
            # Zero out our active's moves — only switches are legal
            state["ours"]["active"]["moves"] = []
            result = self._rust_engine.choose(state)

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
                # Engine returned a move despite empty move list — shouldn't happen
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
        game_llm  = self._llm_call_count    - self._battle_start_llm
        game_total = game_py + game_rust + game_llm

        print(f"\n{'='*60}")
        print(f"BATTLE OVER — {result} in {battle.turn} turns")
        print(f"  Python decisions: {game_py}")
        print(f"  Rust decisions:   {game_rust}")
        print(f"  LLM decisions:    {game_llm}")
        if game_total > 0:
            print(f"  Rust involvement: {int(game_rust / game_total * 100)}% of turns")
            print(f"  LLM involvement:  {int(game_llm  / game_total * 100)}% of turns")
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

        cum_total = self._python_call_count + self._rust_call_count + self._llm_call_count
        if cum_total > 0:
            print(f"  Python decisions: {self._python_call_count}")
            print(f"  Rust decisions:   {self._rust_call_count}")
            print(f"  LLM decisions:    {self._llm_call_count}")
            print(f"  Rust involvement: {int(self._rust_call_count / cum_total * 100)}% cumulative")
            print(f"  LLM involvement:  {int(self._llm_call_count  / cum_total * 100)}% cumulative")
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
    print(f"   LLM decisions:    {player._llm_call_count}")

    player._rust_engine.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run competitive Gen 1 OU player (local)")
    parser.add_argument("--format",  default="ou")
    parser.add_argument("--battles", type=int, default=1)
    args = parser.parse_args()

    if not ensure_ollama_running():
        print("Please start Ollama: ollama serve")
        exit(1)

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
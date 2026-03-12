"""
competitive_player.py
=====================
Competitive Gen 1 OU player using a hybrid Python/LLM decision engine.

Decision hierarchy (fast lane first, LLM only when genuinely ambiguous):

  1. FORCED  — only one legal action, no decision needed
  2. IMMUNE  — opponent move does 0x damage, stay in and attack
  3. DANGER  — taking 2x+ and at risk, switch to best resist if available
  4. DOMINANT — we hit opponent for 2x+, they don't threaten us → attack
  5. RECHARGE — opponent must recharge after Hyper Beam → free turn, attack
  6. SLEEP FOLLOW-UP — opponent is asleep and we have Dream Eater → use it
  7. AMBIGUOUS — LLM called with full battle context and reasoning printed

Opponent model:
  Tracks moves seen per opponent Pokemon across the battle.
  Stores move TYPES (not names) for accurate effectiveness calculations.
  Passed to LLM so it reasons about what the opponent likely carries.

Usage:
    python3 competitive_player.py --battles 1
    python3 competitive_player.py --battles 5 --format ou
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
    FIXED_DAMAGE_MOVES, OHKO_MOVES, SLEEP_MOVES, LLM_ONLY_MOVES, IGNORE_MOVES,
)
from llm_bridge import (
    call_llm, call_llm_async, strip_think_tags,
    parse_battle_decision, parse_lead_choice, ensure_ollama_running,
)


# =============================================================================
# LLM PROMPT BUILDER
# =============================================================================

def build_battle_prompt(battle, available_moves, available_switches,
                        opponent_move_types, reason_for_ambiguity):
    """
    Build a tight battle-state prompt for the LLM.
    Returns (prompt_string, valid_move_ids_set, valid_switch_ids_set)
    """
    my_poke = battle.active_pokemon
    opp_poke = battle.opponent_active_pokemon
    my_types = get_pokemon_types(my_poke)
    opp_types = get_pokemon_types(opp_poke)
    my_hp = int(my_poke.current_hp_fraction * 100)
    opp_hp = int((opp_poke.current_hp_fraction or 1.0) * 100)

    # ── Status ────────────────────────────────────────────────────────────
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

    my_status = status_label(my_poke)
    opp_status = status_label(opp_poke)

    # ── Move options with full context ────────────────────────────────────
    move_lines = []
    for m in available_moves:
        if m.id == 'struggle':
            continue
        mtype = m.type.name.lower() if m.type else 'normal'
        bp = m.base_power or 0

        stab = mtype in my_types
        stab_note = ' +STAB(1.5x)' if stab else ''

        if m.id in FIXED_DAMAGE_MOVES:
            eff_label = 'deals fixed damage (ignores type chart, ~100 dmg at L100)'
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
            extra = ' ⚠️ USER MUST RECHARGE NEXT TURN (opponent gets free move)'
        if m.id in ('explosion', 'selfdestruct'):
            extra = ' ⚠️ USER FAINTS AFTER USE'

        move_lines.append(
            f'  {m.id:18s} type:{mtype:10s} bp:{bp:3d}{stab_note:14s} vs opp: {eff_label}{extra}'
        )

    # ── Switch options with threat analysis ───────────────────────────────
    switch_lines = []
    opp_known_types = opponent_move_types.get(opp_poke.species, [])
    for p in available_switches:
        ptypes = get_pokemon_types(p)
        php = int(p.current_hp_fraction * 100)
        pst = status_label(p)

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

    # ── Opponent team overview ────────────────────────────────────────────
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

    # ── My team overview ──────────────────────────────────────────────────
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
            pst = status_label(p)
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
- Type chart: Ghost immune to Normal+Fighting | Ground immune to Electric | Psychic 2x vs Fighting+Poison
- STAB: moves matching user's type deal 1.5x damage (already factored into bp above)
- Seismic Toss / Night Shade: deal damage = user level (100 in OU). Ignore type chart entirely.
- Hyper Beam: powerful but USER SKIPS NEXT TURN to recharge. Don't use if opponent can KO on free turn.
- Paralysis (PAR): 25% chance to be fully immobilized each turn. Speed reduced to 1/4.
- Sleep (SLP): cannot move at all. Dream Eater only works on sleeping targets.
- Switching: costs your entire turn. Only switch if current matchup is clearly losing.
- Thunder Wave: cannot miss, permanently paralyzes. Very high value vs fast threats.

════════════════════════════════════════
SITUATION: {reason_for_ambiguity}
════════════════════════════════════════

Analyse the battle state above, then output ONE decision:
DECISION: move <moveid>      e.g. DECISION: move thunderbolt
DECISION: switch <species>   e.g. DECISION: switch chansey

Use ONLY the exact move IDs and species names listed above. Output DECISION on its own line at the end."""

    valid_move_ids = {m.id.lower() for m in available_moves if m.id != 'struggle'}
    valid_switch_ids = {p.species.lower() for p in available_switches}

    return prompt, valid_move_ids, valid_switch_ids


# =============================================================================
# COMPETITIVE PLAYER
# =============================================================================

class CompetitivePlayer(Player):
    """
    Hybrid Python/LLM competitive player.

    Python handles clear-cut decisions instantly.
    LLM is called only when multiple reasonable plays exist.
    Full reasoning is printed to console for observation.
    """

    def __init__(self, *args, verbose=True, live_timeout=None, **kwargs):
        super().__init__(*args, **kwargs)
        # {species: [move_type_strings]} — stores TYPES not names
        self._opponent_move_types = {}
        # {species: [move_id_strings]} — stores names for display/LLM context
        self._opponent_move_names = {}
        self._llm_call_count = 0
        self._python_call_count = 0
        self._verbose = verbose
        self._current_battle_tag = None
        # Live play uses a shorter timeout so the event loop stays responsive
        self._llm_timeout = live_timeout

    def _log(self, msg):
        """Always print — captured by Tee into the log file."""
        print(msg)

    def _unsuppress(self):
        """Re-enable console output after a compact-mode turn."""
        import sys
        tee = sys.stdout
        if hasattr(tee, '_suppress_console'):
            tee._suppress_console = False

    def _emit_compact(self, turn, my_poke, my_hp, opp_poke, opp_hp, action, source):
        """Print a compact one-liner to console, re-enable console output."""
        self._unsuppress()
        if not self._verbose:
            indicator = "🤖" if source == "llm" else "⚡"
            print(f"  {indicator} T{turn:02d} {my_poke}({my_hp}%) vs {opp_poke}({opp_hp}%) → {action} [{source}]")

    # -------------------------------------------------------------------------
    # Team preview — LLM picks the lead
    # -------------------------------------------------------------------------

    async def teampreview(self, battle):
        """Ask the LLM to pick our lead based on both teams being visible."""
        my_team = list(battle.team.values())
        opp_team = list(battle.opponent_team.values())

        def team_summary(pokemon_list):
            lines = []
            for p in pokemon_list:
                types = get_pokemon_types(p)
                lines.append(f"  {p.species} ({'/'.join(types)})")
            return '\n'.join(lines)

        valid_leads = [p.species.lower() for p in my_team]

        prompt = f"""You are a Gen 1 competitive Pokemon player choosing your lead for team preview.
Both teams are now visible. Pick the best lead for your team.

MY TEAM:
{team_summary(my_team)}

OPPONENT'S TEAM:
{team_summary(opp_team)}

GEN 1 TEAM PREVIEW STRATEGY:
- Pick a lead that has a good type matchup against the opponent's likely lead
- Gengar and Alakazam are common fast leads — consider what beats them
- Chansey leads absorb hits and set up Thunder Wave early
- Avoid leading with a Pokemon weak to the opponent's most threatening types
- A fast Pokemon with Thunder Wave can cripple the opponent's lead immediately

Your available leads (use exact species name):
{', '.join(valid_leads)}

Think through the matchups, then end with exactly:
LEAD: <species>"""

        print(f"\n{'='*60}")
        print(f"TEAM PREVIEW")
        print(f"  My team:  {', '.join(p.species for p in my_team)}")
        print(f"  Opp team: {', '.join(p.species for p in opp_team)}")

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
                        order = [chosen] + [p for p in my_team if p != chosen]
                        order_str = '/team ' + ''.join(
                            str(my_team.index(p) + 1) for p in order
                        )
                        print(f"\n  ✅ LLM LEAD: {chosen.species}")
                        return order_str

        except Exception as e:
            print(f"  LLM error during teampreview: {e}")

        print(f"  🔄 FALLBACK: defaulting to slot 1 ({my_team[0].species})")
        return self.random_teampreview(battle)

    # -------------------------------------------------------------------------
    # Opponent move tracking — intercept raw messages
    # -------------------------------------------------------------------------

    def _get_opponent_prefix(self, battle):
        """
        Determine which side prefix the opponent uses.
        On live Showdown we could be p1 or p2 — detect dynamically.
        """
        if hasattr(battle, 'player_role') and battle.player_role:
            return 'p2' if battle.player_role == 'p1' else 'p1'
        # Fallback: assume we are p1 (local play default)
        return 'p2'

    async def _handle_battle_message(self, split_messages):
        """
        Track opponent moves as they are used.
        Stores both move TYPES (for effectiveness calc) and move NAMES (for display).
        """
        # Determine opponent prefix from the first battle we see
        battle_tag = split_messages[0][0] if split_messages[0] else None
        battle = self.battles.get(battle_tag) if battle_tag else None
        opp_prefix = self._get_opponent_prefix(battle) if battle else 'p2'

        for msg in split_messages[1:]:
            if not msg or len(msg) < 2:
                continue

            if msg[1] == 'move' and len(msg) > 3:
                actor = msg[2] if len(msg) > 2 else ''
                move_name = msg[3].lower().replace(' ', '').replace('-', '')

                # Only track opponent moves, skip protocol artifacts
                if actor.startswith(opp_prefix) and move_name not in IGNORE_MOVES:
                    species = (
                        actor.split(':')[1].strip().lower()
                        if ':' in actor else actor
                    )

                    # Store move name (for display and LLM prompts)
                    if species not in self._opponent_move_names:
                        self._opponent_move_names[species] = []
                    if move_name not in self._opponent_move_names[species]:
                        self._opponent_move_names[species].append(move_name)

                    # Resolve move type and store it (for effectiveness calc)
                    # Try poke-env's move database first
                    move_type = get_move_type(move_name)
                    if not move_type:
                        # Try to get type from the battle's move objects
                        try:
                            from poke_env.environment.move import Move
                            from poke_env import gen_data
                            # poke-env Gen1 move lookup
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
        """
        Async dispatch to the decision engine.
        poke-env supports choose_move returning an Awaitable[BattleOrder].
        This keeps the event loop alive during LLM calls so websocket
        pings and Showdown timer messages can be processed.
        """
        order = await self._choose_move_inner(battle)

        # Emit compact one-liner and restore console
        if not self._verbose:
            my = battle.active_pokemon
            opp = battle.opponent_active_pokemon
            my_hp = int(my.current_hp_fraction * 100)
            opp_hp = int((opp.current_hp_fraction or 1.0) * 100)

            # Determine what action was chosen from the order
            action_str = "?"
            source = "py"
            if hasattr(order, 'order') and order.order:
                o = order.order
                if hasattr(o, 'id'):
                    action_str = o.id
                elif hasattr(o, 'species'):
                    action_str = f"→{o.species}"

            if self._llm_call_count > getattr(self, '_last_llm_count', 0):
                source = "llm"
            self._last_llm_count = self._llm_call_count

            self._emit_compact(
                battle.turn, my.species, my_hp,
                opp.species, opp_hp, action_str, source
            )
        else:
            self._unsuppress()

        return order

    async def _choose_move_inner(self, battle):
        my_poke = battle.active_pokemon
        opp_poke = battle.opponent_active_pokemon
        my_types = get_pokemon_types(my_poke)
        opp_types = get_pokemon_types(opp_poke)
        my_hp_frac = my_poke.current_hp_fraction

        # Register types of our own moves into the cache (for opponent tracking)
        for m in battle.available_moves:
            if m.type:
                register_move_type(m.id, m.type.name.lower())

        # Filter out struggle (forced) and recharge (forced after Hyper Beam)
        real_moves = [m for m in battle.available_moves
                      if m.id not in ('struggle', 'recharge')]

        # Filter Thunder Wave if opponent already has a status condition
        opp_status_now = battle.opponent_active_pokemon.status
        my_status_now = battle.active_pokemon.status
        if opp_status_now:
            real_moves = [m for m in real_moves if m.id != 'thunderwave']

        # Filter ALL moves that are immune (0x) against the opponent's types.
        # This catches every type immunity in one pass:
        #   Electric → Ground (Thunder Wave, Thunderbolt vs Rhydon etc)
        #   Normal → Ghost, Ghost → Normal
        #   Fighting → Ghost
        #   Ground → Flying
        #   Psychic → Ghost (Gen 1 bug: 0x not 2x)
        # Fixed-damage moves (Seismic Toss, Night Shade) are excluded from
        # this filter because they ignore the type chart entirely.
        def is_immune(move):
            if move.id in FIXED_DAMAGE_MOVES:
                return False  # fixed damage ignores type chart
            move_type = move.type.name.lower() if move.type else 'normal'
            return type_effectiveness(move_type, opp_types) == 0

        real_moves = [m for m in real_moves if not is_immune(m)]

        # Also filter Thunder Wave specifically against Ground types
        # (even though the general filter above handles Thunderbolt etc,
        #  Thunder Wave has 0 base power so type_effectiveness alone
        #  might not catch it depending on how poke-env reports its type)
        if 'ground' in opp_types:
            real_moves = [m for m in real_moves if m.id not in ('thunderwave', 'stunspore')]
        if 'grass' in opp_types:
            real_moves = [m for m in real_moves if m.id != 'stunspore']

        # Filter Dream Eater unless opponent is actually asleep
        opp_is_asleep = opp_status_now and opp_status_now.name == 'SLP'
        if not opp_is_asleep:
            real_moves = [m for m in real_moves if m.id != 'dreameater']

        # On a recharge turn poke-env only offers [recharge]
        all_moves = battle.available_moves
        if len(all_moves) == 1 and all_moves[0].id == 'recharge':
            print(f"  ⏳ PYTHON: recharge turn (locked after Hyper Beam)")
            self._python_call_count += 1
            return self.create_order(all_moves[0])

        switches = battle.available_switches
        my_is_asleep = my_status_now and my_status_now.name == 'SLP'

        # ── Turn header ──────────────────────────────────────────────────
        opp_hp_frac = opp_poke.current_hp_fraction or 1.0

        def status_str(poke):
            parts = []
            if poke.status:
                parts.append(poke.status.name)
            for eff in (poke.effects or {}):
                if hasattr(eff, 'name') and eff.name == 'CONFUSION':
                    parts.append('CONF')
                    break
            return f" [{', '.join(parts)}]" if parts else ''

        my_status_str = status_str(my_poke)
        opp_status_str = status_str(opp_poke)

        opp_last = getattr(opp_poke, 'last_move', None)
        opp_last_str = f" | Opp used: {opp_last.id}" if opp_last else ''

        # In compact mode, suppress verbose console output but still write to log
        import sys as _sys
        _tee = _sys.stdout
        if not self._verbose and hasattr(_tee, '_suppress_console'):
            _tee._suppress_console = True

        print(f"\n{'='*60}")
        print(f"Turn {battle.turn} | My: {my_poke.species} ({int(my_hp_frac*100)}% HP{my_status_str}) "
              f"vs {opp_poke.species} ({int(opp_hp_frac*100)}% HP{opp_status_str}){opp_last_str}")
        print(f"  My types: {my_types} | Opp types: {opp_types}")
        if real_moves:
            print(f"  Moves: {[m.id for m in real_moves]}")
        if switches:
            print(f"  Switches: {[p.species for p in switches]}")

        # If WE are asleep — server ignores input, queue best move
        if my_is_asleep and real_moves:
            best_asleep, _ = best_move_effectiveness(
                real_moves, opp_types, attacker_types=my_types
            )
            fallback = best_asleep or real_moves[0]
            print(f"  💤 PYTHON: WE ARE ASLEEP — queuing {fallback.id}")
            self._python_call_count += 1
            return self.create_order(fallback)

        # ------------------------------------------------------------------
        # STEP 1 — Forced: no real moves and no switches
        # ------------------------------------------------------------------
        if not real_moves and not switches:
            print("  🔒 FORCED: no options, using default")
            self._python_call_count += 1
            return self.choose_default_move()

        # ------------------------------------------------------------------
        # STEP 2 — Only switches available (faint or all moves are struggle)
        # ------------------------------------------------------------------
        if not real_moves and switches:
            if len(switches) == 1:
                print(f"  🔀 FORCED SWITCH: only {switches[0].species} left")
                self._python_call_count += 1
                return self.create_order(switches[0])

            opp_known_types = self._opponent_move_types.get(opp_poke.species, [])
            opp_known_names = self._opponent_move_names.get(opp_poke.species, [])
            reason_faint = (
                f"my Pokemon fainted — opponent has {opp_poke.species} "
                f"({'/'.join(opp_types)}) on field, known move types: "
                f"{opp_known_types or 'none'}"
            )
            print(f"\n  🤖 LLM CALLED (faint switch): {reason_faint}")

            prompt, valid_moves, valid_switches = build_battle_prompt(
                battle, [], switches, self._opponent_move_types, reason_faint
            )
            raw, err = await call_llm_async(prompt, timeout=self._llm_timeout)
            if err:
                print(f"  LLM error: {err}")
            elif raw:
                print(f"\n  💭 LLM REASONING:\n")
                for line in strip_think_tags(raw).split('\n'):
                    print(f"     {line}")

                action_type, action_id = parse_battle_decision(
                    raw, valid_moves, valid_switches
                )
                if action_type == 'switch' and action_id:
                    norm = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
                    chosen = next(
                        (p for p in switches if norm(p.species) == norm(action_id)),
                        None
                    )
                    if chosen:
                        print(f"\n  ✅ LLM FAINT SWITCH: sending in {chosen.species}")
                        self._llm_call_count += 1
                        return self.create_order(chosen)

            # Fallback: Python picks best resist
            best_switch = find_best_switch(battle)
            print(f"  🔀 PYTHON FALLBACK: sending in {best_switch.species}")
            self._python_call_count += 1
            return self.create_order(best_switch)

        # ------------------------------------------------------------------
        # STEP 3 — Compute best move
        # ------------------------------------------------------------------
        opp_known_types = self._opponent_move_types.get(opp_poke.species, [])
        opp_known_names = self._opponent_move_names.get(opp_poke.species, [])
        best_move, best_score = best_move_effectiveness(
            real_moves, opp_types, attacker_types=my_types
        )

        # ------------------------------------------------------------------
        # STEP 4 — Are we immune to everything the opponent has shown?
        # ------------------------------------------------------------------
        if opp_known_types:
            worst_incoming = worst_incoming_effectiveness(opp_known_types, my_types)
            if worst_incoming == 0:
                print(f"  🛡️  PYTHON: immune to all opponent known moves — staying in")
                print(f"     Using: {best_move.id}")
                self._python_call_count += 1
                return self.create_order(best_move)

        # ------------------------------------------------------------------
        # STEP 5 — Danger switch logic
        # ------------------------------------------------------------------
        in_danger = False
        threat_type = None

        # Tier A: confirmed move type threat
        if opp_known_types:
            worst_incoming = worst_incoming_effectiveness(opp_known_types, my_types)
            if worst_incoming >= 2 and my_hp_frac < 0.40:
                in_danger = True
                for mt in opp_known_types:
                    if type_effectiveness(mt, my_types) >= 2:
                        threat_type = mt
                        break

        # Tier B: STAB-based threat assessment
        if not in_danger and sorted(my_types) != sorted(opp_types):
            if opp_known_types:
                worst = worst_incoming_effectiveness(opp_known_types, my_types)
                if worst >= 2:
                    threat_type = None
                    if my_hp_frac < 0.50:
                        in_danger = True
                    elif switches:
                        for sw in switches:
                            sw_types = get_pokemon_types(sw)
                            if worst_incoming_effectiveness(opp_known_types, sw_types) < 1:
                                in_danger = True
                                break
            else:
                for opp_type in opp_types:
                    eff = type_effectiveness(opp_type, my_types)
                    if eff >= 2:
                        threat_type = opp_type
                        if my_hp_frac < 0.50:
                            in_danger = True
                        break

        if in_danger and switches:
            best_switch = find_best_switch(battle, threat_type=threat_type)
            if best_switch:
                switch_types = get_pokemon_types(best_switch)
                incoming_eff = type_effectiveness(
                    threat_type or 'normal', switch_types
                )
                if incoming_eff < 1:
                    print(f"  🔀 PYTHON DANGER SWITCH: {my_poke.species} at "
                          f"{int(my_hp_frac*100)}% threatened by {threat_type} "
                          f"— switching to {best_switch.species}")
                    self._python_call_count += 1
                    return self.create_order(best_switch)

        # ------------------------------------------------------------------
        # STEP 6 — Dominant type advantage (2x+ on opponent)
        # ------------------------------------------------------------------
        if best_move:
            move_type = best_move.type.name.lower() if best_move.type else 'normal'
            best_eff = type_effectiveness(move_type, opp_types)

            if best_eff >= 2 and best_move.base_power >= 60:
                if not in_danger or my_hp_frac > 0.5:
                    print(f"  ⚔️  PYTHON DOMINANT: {best_move.id} hits "
                          f"{opp_poke.species} for {best_eff}x — staying aggressive")
                    self._python_call_count += 1
                    return self.create_order(best_move)

        # ------------------------------------------------------------------
        # STEP 7a — Recover / Soft-Boiled at ~50% HP
        # These heal 50% HP instantly with no downside (no sleep).
        # Use them proactively — Chansey and Starmie should heal often.
        # ------------------------------------------------------------------
        if my_hp_frac < 0.55:
            heal_move = next(
                (m for m in real_moves if m.id in ('recover', 'softboiled')),
                None
            )
            if heal_move:
                print(f"  💚 PYTHON: healing at {int(my_hp_frac*100)}% — using {heal_move.id}")
                self._python_call_count += 1
                return self.create_order(heal_move)

        # ------------------------------------------------------------------
        # STEP 7b — Rest at low HP (puts us to sleep — last resort)
        # ------------------------------------------------------------------
        if my_hp_frac < 0.40:
            rest_move = next((m for m in real_moves if m.id == 'rest'), None)
            if rest_move:
                print(f"  💤 PYTHON: low HP ({int(my_hp_frac*100)}%) — using Rest")
                self._python_call_count += 1
                return self.create_order(rest_move)

        # ------------------------------------------------------------------
        # STEP 7c — Sleep follow-up: Dream Eater on sleeping opponent
        # ------------------------------------------------------------------
        opp_status = opp_poke.status
        if opp_status and opp_status.name.lower() == 'slp':
            dreameater = next(
                (m for m in real_moves if m.id == 'dreameater'), None
            )
            if dreameater:
                print(f"  💤 PYTHON: opponent asleep — using Dream Eater")
                self._python_call_count += 1
                return self.create_order(dreameater)

        # ------------------------------------------------------------------
        # STEP 8 — AMBIGUOUS: call LLM
        # ------------------------------------------------------------------
        current_best_eff = 1.0
        if best_move:
            mv_type = best_move.type.name.lower() if best_move.type else 'normal'
            current_best_eff = type_effectiveness(mv_type, opp_types)

        reasons = []
        if in_danger:
            reasons.append(f"in danger at {int(my_hp_frac*100)}% HP")
        if current_best_eff < 1:
            reasons.append("best move is resisted")
        if current_best_eff == 1 and not opp_known_types:
            reasons.append("neutral matchup, opponent moveset unknown")
        if current_best_eff == 1 and switches and opp_known_types:
            for sw in switches:
                sw_types = get_pokemon_types(sw)
                if worst_incoming_effectiveness(opp_known_types, sw_types) < 1:
                    reasons.append("a switch-in resists opponent's known moves")
                    break

        # Flag status moves as LLM decisions
        opp_status_now = opp_poke.status
        has_twave = any(m.id == 'thunderwave' for m in real_moves)
        has_sleep = any(m.id in SLEEP_MOVES for m in real_moves)
        has_dreameater = any(
            m.id == 'dreameater' for m in battle.available_moves
            if m.id not in ('struggle', 'recharge')
        )

        if not opp_status_now:
            if has_sleep:
                sleep_move = next(m.id for m in real_moves if m.id in SLEEP_MOVES)
                combo_note = ' (sets up Dream Eater)' if has_dreameater else ''
                reasons.append(
                    f"{sleep_move} available — opponent has no status{combo_note}"
                )
            if has_twave:
                reasons.append("Thunder Wave available — opponent has no status")

        if not reasons:
            if best_move and best_move.id == 'hyperbeam':
                reasons.append("considering Hyper Beam — need to weigh recharge risk vs KO")
            else:
                print(f"  ⚔️  PYTHON: neutral matchup, attacking with {best_move.id}")
                self._python_call_count += 1
                return self.create_order(best_move)

        reason_str = '; '.join(reasons)
        print(f"\n  🤖 LLM CALLED (call #{self._llm_call_count + 1}): {reason_str}")

        # Status context before LLM reasoning
        opp_conf = any(hasattr(e, 'name') and e.name == 'CONFUSION'
                       for e in (opp_poke.effects or {}))
        my_conf = any(hasattr(e, 'name') and e.name == 'CONFUSION'
                      for e in (my_poke.effects or {}))
        opp_st = opp_poke.status.name if opp_poke.status else "none"
        my_st = my_poke.status.name if my_poke.status else "none"
        if opp_conf: opp_st += "+CONF"
        if my_conf:  my_st += "+CONF"
        print(f"     Status — Me: {my_st} | Opp: {opp_st}")
        print(f"     Opponent known move types: {opp_known_types or 'none'}")
        print(f"     Opponent known move names: {opp_known_names or 'none'}")

        prompt, valid_moves, valid_switches = build_battle_prompt(
            battle, real_moves, switches,
            self._opponent_move_types, reason_str
        )
        raw, err = await call_llm_async(prompt, timeout=self._llm_timeout)

        if err:
            print(f"  LLM error: {err}")
        elif raw:
            print(f"\n  💭 LLM REASONING:\n")
            for line in strip_think_tags(raw).split('\n'):
                print(f"     {line}")

            action_type, action_id = parse_battle_decision(
                raw, valid_moves, valid_switches
            )
            norm = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())

            if action_type == 'move' and action_id:
                chosen = next(
                    (m for m in real_moves if norm(m.id) == norm(action_id)),
                    None
                )
                if chosen:
                    print(f"\n  ✅ LLM DECISION: use {chosen.id}")
                    self._llm_call_count += 1
                    return self.create_order(chosen)
                else:
                    print(f"  ⚠️  LLM chose move '{action_id}' not in legal list")

            elif action_type == 'switch' and action_id:
                chosen = next(
                    (p for p in switches if norm(p.species) == norm(action_id)),
                    None
                )
                if chosen:
                    print(f"\n  ✅ LLM DECISION: switch to {chosen.species}")
                    self._llm_call_count += 1
                    return self.create_order(chosen)
                else:
                    print(f"  ⚠️  LLM chose switch '{action_id}' not available")

        # Fallback
        print(f"  🔄 FALLBACK: using {best_move.id if best_move else 'default'}")
        if best_move:
            return self.create_order(best_move)
        return self.choose_default_move()

    # -------------------------------------------------------------------------
    # End of battle summary
    # -------------------------------------------------------------------------

    def _battle_finished_callback(self, battle):
        self._unsuppress()  # always show battle result on console
        result = "WON ✓" if battle.won else "LOST ✗"
        print(f"\n{'='*60}")
        print(f"BATTLE OVER — {result} in {battle.turn} turns")
        print(f"  Python decisions: {self._python_call_count}")
        print(f"  LLM decisions:    {self._llm_call_count}")
        total = self._python_call_count + self._llm_call_count
        if total > 0:
            pct = int(self._llm_call_count / total * 100)
            print(f"  LLM involvement:  {pct}% of turns")
        print(f"{'='*60}\n")


# =============================================================================
# LOCAL OPPONENT (for stress testing)
# =============================================================================

class FilteredRandomPlayer(Player):
    """Random player that filters Struggle to prevent PP exhaustion loops."""
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
    """Auto-detect and load the highest numbered team file from teams/ directory."""
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

    # Fallback: check root directory for legacy files
    legacy = sorted(
        glob.glob(f"team_{format_name}_iteration_*.txt"),
        key=iteration_num
    )
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
    """Write to both stdout and a log file simultaneously."""
    def __init__(self, filepath):
        import sys
        self.file = open(filepath, 'w')
        self.stdout = sys.stdout
        self._suppress_console = False
        sys.stdout = self

    def write(self, data):
        # Always write to file
        self.file.write(data)
        self.file.flush()
        # Only write to console if not suppressed
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
        """Write to log file only, not console."""
        self.file.write(msg + '\n')
        self.file.flush()


# =============================================================================
# LOCAL PLAY ENTRY POINT
# =============================================================================

async def run_competitive_local(team, n_battles=1):
    """Run competitive player against a local RandomPlayer."""
    print(f"\n🏆 Competitive player starting ({n_battles} battle(s), local)\n")

    player = CompetitivePlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(
            f"CompBot_{random_suffix()}", None
        ),
        log_level=POKE_ENV_LOG_LEVEL,
    )
    opponent = FilteredRandomPlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(
            f"RandOpp_{random_suffix()}", None
        ),
        log_level=POKE_ENV_LOG_LEVEL,
    )

    await player.battle_against(opponent, n_battles=n_battles)

    wins = sum(1 for b in player.battles.values() if b.won)
    print(f"\n📊 Final: {wins}/{n_battles} wins")
    print(f"   Total Python decisions: {player._python_call_count}")
    print(f"   Total LLM decisions:    {player._llm_call_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run competitive Gen 1 OU player (local)")
    parser.add_argument("--format", default="ou", help="Format name (ou, uu etc)")
    parser.add_argument("--battles", type=int, default=1)
    args = parser.parse_args()

    if not ensure_ollama_running():
        print("Please start Ollama: ollama serve")
        exit(1)

    # Auto-number log file
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
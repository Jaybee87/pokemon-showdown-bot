"""
competitive_player.py
=====================
Competitive Gen 1 OU player using a hybrid Python/LLM decision engine.

Decision hierarchy (fast lane first, LLM only when genuinely ambiguous):

  1. FORCED - only one legal action, no decision needed
  2. IMMUNE  - opponent move does 0x damage, stay in and attack
  3. DANGER  - taking 2x+ and at risk, switch to best resist if available
  4. DOMINANT - we hit opponent for 2x+, they don't threaten us → attack
  5. RECHARGE - opponent must recharge after Hyper Beam → free turn, attack
  6. SLEEP FOLLOW-UP - opponent is asleep and we have Dream Eater → use it
  7. AMBIGUOUS - LLM called with full battle context and reasoning printed

Opponent model:
  Tracks moves seen per opponent Pokemon across the battle.
  Passed to LLM so it reasons about what the opponent likely carries,
  not just what it showed last turn.

Usage:
    python3 competitive_player.py --team current_team_ou.txt --battles 1
"""

import asyncio
import argparse
import random
import string
import re
# threading removed - LLM call is blocking
import ollama

from poke_env.player import Player
from poke_env import LocalhostServerConfiguration, AccountConfiguration
# Gen 1 type chart - 15 types, no Dark/Steel/Fairy
# effectiveness[attacker_type][defender_type] = multiplier
GEN1_TYPE_CHART = {
    'normal':   {'normal':1,'fire':1,'water':1,'electric':1,'grass':1,'ice':1,'fighting':1,'poison':1,'ground':1,'flying':1,'psychic':1,'bug':1,'rock':0.5,'ghost':0,'dragon':1},
    'fire':     {'normal':1,'fire':0.5,'water':0.5,'electric':1,'grass':2,'ice':2,'fighting':1,'poison':1,'ground':1,'flying':1,'psychic':1,'bug':2,'rock':0.5,'ghost':1,'dragon':0.5},
    'water':    {'normal':1,'fire':2,'water':0.5,'electric':1,'grass':0.5,'ice':1,'fighting':1,'poison':1,'ground':2,'flying':1,'psychic':1,'bug':1,'rock':2,'ghost':1,'dragon':0.5},
    'electric': {'normal':1,'fire':1,'water':2,'electric':0.5,'grass':0.5,'ice':1,'fighting':1,'poison':1,'ground':0,'flying':2,'psychic':1,'bug':1,'rock':1,'ghost':1,'dragon':0.5},
    'grass':    {'normal':1,'fire':0.5,'water':2,'electric':1,'grass':0.5,'ice':1,'fighting':1,'poison':0.5,'ground':2,'flying':0.5,'psychic':1,'bug':0.5,'rock':2,'ghost':1,'dragon':0.5},
    'ice':      {'normal':1,'fire':0.5,'water':0.5,'electric':1,'grass':2,'ice':0.5,'fighting':1,'poison':1,'ground':2,'flying':2,'psychic':1,'bug':1,'rock':1,'ghost':1,'dragon':2},
    'fighting': {'normal':2,'fire':1,'water':1,'electric':1,'grass':1,'ice':2,'fighting':1,'poison':0.5,'ground':1,'flying':0.5,'psychic':0.5,'bug':0.5,'rock':2,'ghost':0,'dragon':1},
    'poison':   {'normal':1,'fire':1,'water':1,'electric':1,'grass':2,'ice':1,'fighting':1,'poison':0.5,'ground':0.5,'flying':1,'psychic':1,'bug':2,'rock':0.5,'ghost':0.5,'dragon':1},
    'ground':   {'normal':1,'fire':2,'water':1,'electric':2,'grass':0.5,'ice':1,'fighting':1,'poison':2,'ground':1,'flying':0,'psychic':1,'bug':0.5,'rock':2,'ghost':1,'dragon':1},
    'flying':   {'normal':1,'fire':1,'water':1,'electric':0.5,'grass':2,'ice':1,'fighting':2,'poison':1,'ground':1,'flying':1,'psychic':1,'bug':2,'rock':0.5,'ghost':1,'dragon':1},
    'psychic':  {'normal':1,'fire':1,'water':1,'electric':1,'grass':1,'ice':1,'fighting':2,'poison':2,'ground':1,'flying':1,'psychic':0.5,'bug':1,'rock':1,'ghost':1,'dragon':1},  # Gen 1 bug: Psychic hits Ghost for 1x not 0x
    'bug':      {'normal':1,'fire':0.5,'water':1,'electric':1,'grass':2,'ice':1,'fighting':0.5,'poison':2,'ground':1,'flying':0.5,'psychic':2,'bug':1,'rock':1,'ghost':0.5,'dragon':1},
    'rock':     {'normal':1,'fire':2,'water':1,'electric':1,'grass':1,'ice':2,'fighting':0.5,'poison':1,'ground':0.5,'flying':2,'psychic':1,'bug':2,'rock':1,'ghost':1,'dragon':1},
    'ghost':    {'normal':0,'fire':1,'water':1,'electric':1,'grass':1,'ice':1,'fighting':0,'poison':1,'ground':1,'flying':1,'psychic':2,'bug':1,'rock':1,'ghost':2,'dragon':1},
    'dragon':   {'normal':1,'fire':1,'water':1,'electric':1,'grass':1,'ice':1,'fighting':1,'poison':1,'ground':1,'flying':1,'psychic':1,'bug':1,'rock':1,'ghost':1,'dragon':2},
}


def type_effectiveness(move_type, defender_types):
    """
    Calculate combined type effectiveness multiplier.
    move_type: string e.g. 'fire'
    defender_types: list of 1-2 type strings e.g. ['water', 'ice']
    Returns float multiplier.
    """
    chart = GEN1_TYPE_CHART.get(move_type.lower(), {})
    mult  = 1.0
    for t in defender_types:
        if t:
            mult *= chart.get(t.lower(), 1.0)
    return mult


def get_pokemon_types(pokemon):
    """Extract type strings from a poke-env Pokemon object."""
    types = []
    if pokemon.type_1:
        types.append(pokemon.type_1.name.lower())
    if pokemon.type_2:
        types.append(pokemon.type_2.name.lower())
    return types


def best_move_effectiveness(moves, defender_types):
    """
    From a list of poke-env Move objects, return (best_move, best_eff_multiplier)
    for the move with highest type effectiveness * adjusted base_power.
    Hyper Beam is penalised to 75 effective BP (150/2) because the recharge
    turn gives the opponent a free hit — so it only beats 75bp alternatives.
    Excludes struggle and recharge.
    """
    best_move  = None
    best_score = -1
    best_eff   = 1.0

    for move in moves:
        if move.id in ('struggle', 'recharge'):
            continue
        move_type  = move.type.name.lower() if move.type else 'normal'
        eff        = type_effectiveness(move_type, defender_types)
        raw_bp     = move.base_power or 0
        # Penalise Hyper Beam: treat as 75 effective BP unless it's SE
        # (if it's super effective it may still KO, so penalty is smaller)
        if move.id == 'hyperbeam':
            adj_bp = raw_bp * 0.5 if eff <= 1 else raw_bp * 0.75
        else:
            adj_bp = raw_bp
        score = adj_bp * eff
        if score > best_score:
            best_score = score
            best_move  = move
            best_eff   = eff

    return best_move, best_eff


def worst_incoming_effectiveness(attacker_moves_seen, my_types):
    """
    Given the moves the opponent has revealed, what's the worst
    type effectiveness they can hit me with?
    Returns max multiplier across known moves.
    """
    worst = 1.0
    for move_type in attacker_moves_seen:
        eff   = type_effectiveness(move_type, my_types)
        worst = max(worst, eff)
    return worst


def find_best_switch(battle, threat_type=None):
    """
    Find the best available switch target.
    Prioritises: resists/immune to threat > most HP > not active.
    Returns a Pokemon object or None.
    """
    candidates = [
        p for p in battle.available_switches
        if not p.fainted
    ]
    if not candidates:
        return None

    def switch_score(p):
        types     = get_pokemon_types(p)
        hp_factor = p.current_hp_fraction

        if threat_type:
            eff = type_effectiveness(threat_type, types)
            if eff == 0:
                return 1000 + hp_factor   # immune - top priority
            if eff < 1:
                return 100 + hp_factor    # resist
            if eff > 1:
                return hp_factor - 10     # weak - heavily penalise

        return hp_factor

    return max(candidates, key=switch_score)


# =============================================================================
# LLM CALL - called only for ambiguous decisions
# =============================================================================

# LLM_TIMEOUT removed - call is blocking, no timeout needed

def call_llm_for_decision(battle, available_moves, available_switches,
                           opponent_moves_seen, reason_for_ambiguity):
    """
    Build a tight battle-state prompt and ask the LLM what to do.
    Returns (action_type, action_id, reasoning)
      action_type: 'move' or 'switch'
      action_id:   move.id or pokemon.species
      reasoning:   LLM's explanation string
    """
    # Build current Pokemon summary
    my_poke     = battle.active_pokemon
    opp_poke    = battle.opponent_active_pokemon
    my_types    = get_pokemon_types(my_poke)
    opp_types   = get_pokemon_types(opp_poke)
    my_hp       = int(my_poke.current_hp_fraction * 100)
    opp_hp      = int((opp_poke.current_hp_fraction or 1.0) * 100)

    # Move options with effectiveness
    move_lines = []
    for m in available_moves:
        if m.id == 'struggle':
            continue
        mtype = m.type.name.lower() if m.type else 'normal'
        eff   = type_effectiveness(mtype, opp_types)
        eff_label = (
            f"super effective ({eff}x)" if eff > 1
            else f"not very effective ({eff}x)" if eff < 1 and eff > 0
            else "no effect (immune)" if eff == 0
            else "neutral"
        )
        move_lines.append(
            f"  {m.id} (type:{mtype}, bp:{m.base_power or 0}, {eff_label})"
        )

    # Switch options
    switch_lines = []
    for p in available_switches:
        ptypes   = get_pokemon_types(p)
        php      = int(p.current_hp_fraction * 100)
        # How does the opponent's known moveset threaten this switch-in?
        threat   = worst_incoming_effectiveness(
            opponent_moves_seen.get(opp_poke.species, []), ptypes
        )
        threat_label = (
            "would be hit super effectively" if threat > 1
            else "resists opponent's known moves" if threat < 1
            else "neutral matchup"
        )
        switch_lines.append(
            f"  {p.species} ({'/'.join(ptypes)}, {php}% HP, {threat_label})"
        )

    # Opponent known moves
    known = opponent_moves_seen.get(opp_poke.species, [])
    known_str = ', '.join(known) if known else 'none revealed yet'

    # Team HP overview
    team_lines = []
    for p in battle.team.values():
        if p.fainted:
            team_lines.append(f"  {p.species}: fainted")
        elif p == my_poke:
            team_lines.append(f"  {p.species}: {my_hp}% HP (active)")
        else:
            team_lines.append(f"  {p.species}: {int(p.current_hp_fraction*100)}% HP")

    prompt = f"""You are a Gen 1 competitive Pokemon battle AI.

=== BATTLE STATE (turn {battle.turn}) ===
MY ACTIVE:  {my_poke.species.upper()} | types: {', '.join(my_types)} | HP: {my_hp}%
OPP ACTIVE: {opp_poke.species.upper()} | types: {', '.join(opp_types)} | HP: ~{opp_hp}%

NOTE: The types above are EXACT in-game types. Do NOT guess types from move names.

OPPONENT'S REVEALED MOVES: {known_str}

=== MY OPTIONS ===
MOVES (use the exact id shown):
{chr(10).join(move_lines) if move_lines else '  none available'}

SWITCHES (use the exact species name shown):
{chr(10).join(switch_lines) if switch_lines else '  none (must attack)'}

=== MY TEAM HP ===
{chr(10).join(team_lines)}

=== GEN 1 RULES ===
- Ghost is immune to Normal and Fighting
- Ground is immune to Electric
- Psychic hits Fighting and Poison for 2x
- After Hyper Beam the user must recharge - opponent gets a free turn
- Sleep is the strongest status - if opponent is sleeping, deal maximum damage
- Only switch if the type matchup is clearly losing - switching wastes a turn
- Thunder Wave permanently halves speed - very strong utility move

=== SITUATION ===
{reason_for_ambiguity}

Think through the matchup, then end your response with exactly one DECISION line:
DECISION: move <moveid>      (e.g. DECISION: move thunderbolt)
DECISION: switch <species>   (e.g. DECISION: switch chansey)
Use only the exact IDs/names listed above."""


    # Valid IDs for parsing
    valid_move_ids   = {m.id.lower() for m in available_moves if m.id != 'struggle'}
    valid_switch_ids = {p.species.lower() for p in available_switches}

    try:
        response = ollama.chat(
            model="deepseek-r1:7b",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response['message']['content'].strip()

        # Search anywhere in response for a DECISION line
        # Handle variants: "DECISION: move X", "DECISION: X", "DECISION: use X",
        #                   "DECISION: switch <X>", "DECISION: switch to X"
        action_type = None
        action_id   = None

        # Find ALL DECISION lines, take the FIRST valid one
        # (LLM sometimes outputs multiple, we honour the first commitment)
        action_type = None
        action_id   = None
        for line in raw.split('\n'):
            line = line.strip()
            if not line.upper().startswith('DECISION:'):
                continue
            # Try "DECISION: move/switch X"
            m = re.search(r'DECISION:\s*(move|switch)\s+<?([\w]+)>?', line, re.IGNORECASE)
            if m:
                at = m.group(1).lower()
                ai = m.group(2).lower()
                if at == 'move' and ai in valid_move_ids:
                    action_type, action_id = at, ai
                    break
                if at == 'switch' and ai in valid_switch_ids:
                    action_type, action_id = at, ai
                    break
            # Try "DECISION: use X" or "DECISION: X"
            m = re.search(r'DECISION:\s+(?:use\s+)?<?([\w]+)>?', line, re.IGNORECASE)
            if m:
                candidate = m.group(1).lower()
                if candidate in valid_move_ids:
                    action_type, action_id = 'move', candidate
                    break
                if candidate in valid_switch_ids:
                    action_type, action_id = 'switch', candidate
                    break

        # Validate the parsed id against actual legal options
        if action_type == 'move' and action_id not in valid_move_ids:
            action_type = None
            action_id   = None
        if action_type == 'switch' and action_id not in valid_switch_ids:
            action_type = None
            action_id   = None

        return action_type, action_id, raw

    except Exception as e:
        return None, None, f"LLM error: {e}"


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._opponent_moves_seen = {}   # {species: [move_types_seen]}
        self._recharge_next_turn  = {}   # {battle_tag: bool}
        self._llm_call_count      = 0
        self._python_call_count   = 0
        self._turn_log            = []

    # -------------------------------------------------------------------------
    # Opponent move tracking - intercept raw messages
    # -------------------------------------------------------------------------

    # PS protocol artifacts - not real moveset choices
    IGNORE_MOVES = {'recharge', 'struggle', 'splash'}

    async def _handle_battle_message(self, split_messages):
        """Track opponent moves as they are used."""
        for msg in split_messages[1:]:
            if not msg or len(msg) < 2:
                continue
            if msg[1] == 'move':
                # ['', 'move', 'p2a: Gengar', 'Thunderbolt', 'p1a: Starmie']
                if len(msg) > 3:
                    actor     = msg[2] if len(msg) > 2 else ''
                    move_name = msg[3].lower().replace(' ', '').replace('-', '')
                    # Only track opponent moves (p2), skip protocol artifacts
                    if actor.startswith('p2') and move_name not in self.IGNORE_MOVES:
                        species = actor.split(':')[1].strip().lower() if ':' in actor else actor
                        if species not in self._opponent_moves_seen:
                            self._opponent_moves_seen[species] = []
                        if move_name not in self._opponent_moves_seen[species]:
                            self._opponent_moves_seen[species].append(move_name)

        await super()._handle_battle_message(split_messages)

    # -------------------------------------------------------------------------
    # Core decision engine
    # -------------------------------------------------------------------------

    def choose_move(self, battle):
        my_poke     = battle.active_pokemon
        opp_poke    = battle.opponent_active_pokemon
        my_types    = get_pokemon_types(my_poke)
        opp_types   = get_pokemon_types(opp_poke)
        my_hp_frac  = my_poke.current_hp_fraction

        # Filter out struggle (forced) and recharge (forced after Hyper Beam)
        real_moves  = [m for m in battle.available_moves
                       if m.id not in ('struggle', 'recharge')]

        # Filter Thunder Wave if opponent already has a status condition
        # (can't stack status in Gen 1 - it would be wasted)
        opp_status_now = battle.opponent_active_pokemon.status
        if opp_status_now:
            real_moves = [m for m in real_moves if m.id != 'thunderwave']

        # On a recharge turn poke-env only offers [recharge] — handle it explicitly
        all_moves = battle.available_moves
        if len(all_moves) == 1 and all_moves[0].id == 'recharge':
            # Forced recharge turn — nothing to decide
            print(f"  ⏳ PYTHON: recharge turn (locked after Hyper Beam)")
            self._python_call_count += 1
            return self.create_order(all_moves[0])
        switches    = battle.available_switches

        print(f"\n{'='*60}")
        print(f"Turn {battle.turn} | My: {my_poke.species} ({int(my_hp_frac*100)}% HP) "
              f"vs {opp_poke.species}")
        print(f"  My types: {my_types} | Opp types: {opp_types}")
        if real_moves:
            print(f"  Moves: {[m.id for m in real_moves]}")
        if switches:
            print(f"  Switches: {[p.species for p in switches]}")

        # ------------------------------------------------------------------
        # STEP 1 - Forced: no real moves and no switches
        # ------------------------------------------------------------------
        if not real_moves and not switches:
            print("  🔒 FORCED: no options, using default")
            self._python_call_count += 1
            return self.choose_default_move()

        # ------------------------------------------------------------------
        # STEP 2 - Only switches available (faint or all moves are struggle)
        # Defer to LLM: it knows the full team state and what opponent has left.
        # ------------------------------------------------------------------
        if not real_moves and switches:
            opp_known_faint = self._opponent_moves_seen.get(
                battle.opponent_active_pokemon.species, []
            )
            opp_team_status = ', '.join(
                f"{p.species}({'fainted' if p.fainted else f'{int(p.current_hp_fraction*100)}%'})"
                for p in battle.opponent_team.values()
            ) if battle.opponent_team else 'unknown'
            reason_faint = (
                f"my Pokemon fainted - opponent has {opp_poke.species} "
                f"({'/'.join(opp_types)}) on field, known moves: "
                f"{opp_known_faint or 'none'}"
            )
            print(f"\n  🤖 LLM CALLED (faint switch): {reason_faint}")
            action_type, action_id, reasoning = call_llm_for_decision(
                battle, [], switches, self._opponent_moves_seen, reason_faint
            )
            if reasoning:
                print(f"\n  💭 LLM REASONING:\n")
                for line in reasoning.split('\n'):
                    print(f"     {line}")

            def norm(s): return __import__('re').sub(r'[^a-z0-9]', '', s.lower())
            if action_type == 'switch' and action_id:
                chosen = next(
                    (p for p in switches if norm(p.species) == norm(action_id)), None
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
        # STEP 3 - Check if opponent is in recharge turn (used Hyper Beam)
        # ------------------------------------------------------------------
        opp_known = self._opponent_moves_seen.get(opp_poke.species, [])
        # poke-env tracks this via battle.opponent_active_pokemon's status
        # We use the best damage move as a free punch
        best_move, best_score = best_move_effectiveness(real_moves, opp_types, attacker_types=my_types)

        # ------------------------------------------------------------------
        # STEP 4 - Are we immune to everything the opponent has shown?
        # ------------------------------------------------------------------
        if opp_known:
            worst_incoming = worst_incoming_effectiveness(opp_known, my_types)
            if worst_incoming == 0:
                print(f"  🛡️  PYTHON: immune to all opponent known moves - staying in")
                print(f"     Using: {best_move.id}")
                self._python_call_count += 1
                return self.create_order(best_move)

        # ------------------------------------------------------------------
        # STEP 5 - Are we in danger or in a losing type matchup?
        #
        # Tier A: confirmed 2x move from opponent, HP < 40%
        # Tier B: opponent STAB 2x us, HP < 50% OR clean resist on bench
        #         (leave proactively before taking damage, not after)
        # ------------------------------------------------------------------
        in_danger   = False
        threat_type = None

        # Tier A: confirmed move threat
        if opp_known:
            worst_incoming = worst_incoming_effectiveness(opp_known, my_types)
            if worst_incoming >= 2 and my_hp_frac < 0.40:
                in_danger = True
                for move_type in opp_known:
                    if type_effectiveness(move_type, my_types) >= 2:
                        threat_type = move_type
                        break

        # Tier B: type matchup threat - leave before taking damage
        if not in_danger:
            for opp_type in opp_types:
                eff = type_effectiveness(opp_type, my_types)
                if eff >= 2:
                    threat_type = opp_type
                    if my_hp_frac < 0.50:
                        in_danger = True
                    elif switches:
                        # Leave even at full HP if a resist is on the bench
                        for sw in switches:
                            sw_types = get_pokemon_types(sw)
                            if type_effectiveness(opp_type, sw_types) < 1:
                                in_danger = True
                                break
                    break

        if in_danger and switches:
            best_switch = find_best_switch(battle, threat_type=threat_type)
            if best_switch:
                switch_types = get_pokemon_types(best_switch)
                incoming_eff = type_effectiveness(threat_type or 'normal', switch_types)
                if incoming_eff < 1:
                    print(f"  🔀 PYTHON DANGER SWITCH: {my_poke.species} at {int(my_hp_frac*100)}% "
                          f"threatened by {threat_type} - switching to {best_switch.species}")
                    self._python_call_count += 1
                    return self.create_order(best_switch)

        # ------------------------------------------------------------------
        # STEP 6 - Do we have a dominant type advantage? (2x+ on opponent)
        # ------------------------------------------------------------------
        if best_move:
            move_type = best_move.type.name.lower() if best_move.type else 'normal'
            best_eff  = type_effectiveness(move_type, opp_types)

            if best_eff >= 2 and best_move.base_power >= 60:
                # Check we're not simultaneously losing badly
                if not in_danger or my_hp_frac > 0.5:
                    print(f"  ⚔️  PYTHON DOMINANT: {best_move.id} hits {opp_poke.species} "
                          f"for {best_eff}x - staying aggressive")
                    self._python_call_count += 1
                    return self.create_order(best_move)

        # ------------------------------------------------------------------
        # STEP 7a - Rest: use it if HP < 40% and we have Rest
        # Always correct - no need for LLM on this one
        # ------------------------------------------------------------------
        if my_hp_frac < 0.40:
            rest_move = next((m for m in real_moves if m.id == 'rest'), None)
            if rest_move:
                print(f"  💤 PYTHON: low HP ({int(my_hp_frac*100)}%) - using Rest")
                self._python_call_count += 1
                return self.create_order(rest_move)

        # ------------------------------------------------------------------
        # STEP 7b - Sleep follow-up: opponent is asleep + we have Dream Eater
        # ------------------------------------------------------------------
        opp_status = opp_poke.status
        if opp_status and opp_status.name.lower() in ('slp',):
            dreameater = next(
                (m for m in real_moves if m.id == 'dreameater'), None
            )
            if dreameater:
                print(f"  💤 PYTHON: opponent asleep - using Dream Eater")
                self._python_call_count += 1
                return self.create_order(dreameater)

        # ------------------------------------------------------------------
        # STEP 8 - AMBIGUOUS: call LLM
        # Only call when there's genuine uncertainty. Triggers:
        #   - best move is resisted or we're in danger
        #   - neutral matchup but a switch-in resists opponent's known moves
        #   - neutral matchup with unknown opponent moveset (new Pokemon)
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
        if current_best_eff == 1 and not opp_known:
            reasons.append("neutral matchup, opponent moveset unknown")
        if current_best_eff == 1 and switches and opp_known:
            # Only flag switching if a switch-in actually resists known moves
            for sw in switches:
                sw_types = get_pokemon_types(sw)
                if worst_incoming_effectiveness(opp_known, sw_types) < 1:
                    reasons.append("a switch-in resists opponent's known moves")
                    break

        # Flag Thunder Wave as an LLM decision if opponent has no status
        # and we have it available (filtering already removed it if statused)
        has_twave = any(m.id == 'thunderwave' for m in real_moves)
        if has_twave and not opp_status_now and 'thunderwave' not in reasons:
            reasons.append("Thunder Wave available - opponent has no status")

        if not reasons:
            # If the best move is Hyper Beam, defer to LLM - it's a commitment
            if best_move and best_move.id == 'hyperbeam':
                reasons.append("considering Hyper Beam - need to weigh recharge risk vs KO")
            else:
                # Genuinely neutral with no better option - just attack
                print(f"  ⚔️  PYTHON: neutral matchup, attacking with {best_move.id}")
                self._python_call_count += 1
                return self.create_order(best_move)

        reason_str = '; '.join(reasons)

        print(f"\n  🤖 LLM CALLED (call #{self._llm_call_count + 1}): {reason_str}")
        print(f"     Opponent known moves: {opp_known or 'none'}")

        action_type, action_id, reasoning = call_llm_for_decision(
            battle, real_moves, switches,
            self._opponent_moves_seen, reason_str
        )

        if reasoning:
            print(f"\n  💭 LLM REASONING:\n")
            for line in reasoning.split('\n'):
                print(f"     {line}")

        def norm(s):
            return re.sub(r'[^a-z0-9]', '', s.lower())

        # Try to execute LLM decision
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
                print(f"  ⚠️  LLM chose move '{action_id}' not in legal list - falling back")

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
                print(f"  ⚠️  LLM chose switch '{action_id}' not in available switches - falling back")

        # Fallback: Python best move (LLM didn't give a valid decision)
        print(f"  🔄 FALLBACK: using {best_move.id if best_move else 'default'}")
        if best_move:
            return self.create_order(best_move)
        return self.choose_default_move()

    # -------------------------------------------------------------------------
    # End of battle summary
    # -------------------------------------------------------------------------

    def _battle_finished_callback(self, battle):
        result = "WON ✓" if battle.won else "LOST ✗"
        print(f"\n{'='*60}")
        print(f"BATTLE OVER - {result} in {battle.turn} turns")
        print(f"  Python decisions: {self._python_call_count}")
        print(f"  LLM decisions:    {self._llm_call_count}")
        total = self._python_call_count + self._llm_call_count
        if total > 0:
            pct = int(self._llm_call_count / total * 100)
            print(f"  LLM involvement:  {pct}% of turns")
        print(f"{'='*60}\n")


# =============================================================================
# RANDOM OPPONENT (same as battle_runner's dumb player)
# =============================================================================

class RandomPlayer(Player):
    def choose_move(self, battle):
        real_moves = [m for m in battle.available_moves if m.id != 'struggle']
        if real_moves:
            return self.create_order(random.choice(real_moves))
        if battle.available_switches:
            return self.create_order(random.choice(battle.available_switches))
        return self.choose_default_move()


# =============================================================================
# ENTRY POINT
# =============================================================================

def random_suffix(length=6):
    return ''.join(random.choices(string.ascii_lowercase, k=length))


def load_latest_team(format_name="ou"):
    """
    Auto-detect and load the highest numbered team_ou_iteration_N.txt.
    Falls back to current_team_ou.txt if no iteration files exist.
    """
    import glob

    def iteration_num(path):
        m = re.search(r'_(\d+)\.txt$', path)
        return int(m.group(1)) if m else 0

    files = sorted(glob.glob(f"team_{format_name}_iteration_*.txt"), key=iteration_num)
    if files:
        latest = files[-1]
        print(f"📂 Using team: {latest}")
        with open(latest) as f:
            return f.read()

    fallback = f"current_team_{format_name}.txt"
    print(f"📂 No iteration files found, using {fallback}")
    with open(fallback) as f:
        return f.read()


async def run_competitive(team, n_battles=1):
    print(f"\n🏆 Competitive player starting ({n_battles} battle(s))\n")

    player = CompetitivePlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(f"CompBot_{random_suffix()}", None),
        log_level=40,
    )
    opponent = RandomPlayer(
        battle_format="gen1ou",
        team=team,
        server_configuration=LocalhostServerConfiguration,
        account_configuration=AccountConfiguration(f"RandOpp_{random_suffix()}", None),
        log_level=40,
    )

    await player.battle_against(opponent, n_battles=n_battles)

    wins = sum(1 for b in player.battles.values() if b.won)
    print(f"\n📊 Final: {wins}/{n_battles} wins")
    print(f"   Total Python decisions: {player._python_call_count}")
    print(f"   Total LLM decisions:    {player._llm_call_count}")


class Tee:
    """Write to both stdout and a log file simultaneously."""
    def __init__(self, filepath):
        import sys
        self.file   = open(filepath, 'w')
        self.stdout = sys.stdout
        import sys as _sys
        _sys.stdout = self

    def write(self, data):
        self.stdout.write(data)
        self.file.write(data)
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()

    def close(self):
        import sys
        sys.stdout = self.stdout
        self.file.close()


if __name__ == "__main__":
    import glob as _glob

    parser = argparse.ArgumentParser(description="Run competitive Gen 1 OU player")
    parser.add_argument("--format",  default="ou", help="Format name (ou, uu etc)")
    parser.add_argument("--battles", type=int, default=1)
    args = parser.parse_args()

    # Auto-number the log file
    existing = _glob.glob(f"competitive_log_*.txt")
    def _num(p):
        m = re.search(r'_(\d+)\.txt$', p)
        return int(m.group(1)) if m else 0
    next_num = max((_num(p) for p in existing), default=0) + 1
    log_path = f"competitive_log_{next_num:03d}.txt"

    tee = Tee(log_path)
    print(f"📝 Logging to: {log_path}")

    team = load_latest_team(args.format)
    print(f"Running {args.battles} battle(s)...\n")

    try:
        asyncio.run(run_competitive(team, n_battles=args.battles))
    finally:
        tee.close()
        print(f"\nLog saved to: {log_path}")
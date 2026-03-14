"""
gen1_engine.py
==============
Battle-layer functions that require poke-env objects.

Layer contract
--------------
This file deals with live battle state — poke-env Battle objects, Pokemon
objects, and Move objects. Pure data and calculations that operate only on
species strings / move IDs live in gen1_data.py and are imported from there.

What belongs here
-----------------
- get_pokemon_types()        — reads poke-env Pokemon.type_1 / type_2
- best_move_effectiveness()  — iterates poke-env Move objects
- worst_incoming_effectiveness() — battle-context helper
- find_best_switch()         — takes a poke-env Battle object
- resolve_move_types()       — converts move-ID lists using the shared cache
- find_best_matchup_switch() — iterates poke-env switch objects

Everything else (type chart, damage calc, KO checks, speed, matchup scoring)
lives in gen1_data and is re-exported below for backwards compatibility.

Public API
----------
    From gen1_data (re-exported):
        type_effectiveness, TYPES, TYPE_CHART
        get_weaknesses, get_strengths, get_weaknesses_summary
        get_immunities, get_resistances
        get_stats, get_types
        get_move, get_move_category, get_move_type, register_move_type
        is_damaging, average_hits
        apply_stage
        calc_damage, calc_damage_pct
        can_ko, find_ko_move, can_2hko
        get_speed, outspeeds
        freeze_chance_value
        get_substitute_hp, can_break_substitute
        evaluate_matchup
        FIXED_DAMAGE_MOVES, OHKO_MOVES, SLEEP_MOVES, LLM_ONLY_MOVES,
        IGNORE_MOVES, FREEZE_MOVES, TRAPPING_MOVES

    Defined here (require poke-env):
        get_pokemon_types(poke_env_pokemon) → [str]
        best_move_effectiveness(moves, defender_types, attacker_types) → (move, eff)
        worst_incoming_effectiveness(move_types, my_types) → float
        find_best_switch(battle, threat_type) → Pokemon | None
        resolve_move_types(move_ids) → [str]
        find_best_matchup_switch(...) → (Pokemon | None, float)
"""

from gen1_data import (
    # Constants
    POKEMON, MOVES, SPECIAL_TYPES,
    FIXED_DAMAGE_MOVES, OHKO_MOVES, SLEEP_MOVES, LLM_ONLY_MOVES,
    IGNORE_MOVES, FREEZE_MOVES, TRAPPING_MOVES,
    # Type system
    TYPES, TYPE_CHART, type_effectiveness,
    get_weaknesses, get_strengths, get_weaknesses_summary,
    get_immunities, get_resistances,
    # Pokémon accessors
    get_stats, get_types,
    # Move accessors and cache
    get_move, get_move_category, get_move_type, register_move_type,
    is_damaging, average_hits,
    # Calculations
    apply_stage,
    calc_damage, calc_damage_pct,
    can_ko, find_ko_move, can_2hko,
    get_speed, outspeeds,
    freeze_chance_value,
    get_substitute_hp, can_break_substitute,
    evaluate_matchup,
)

# =============================================================================
# POKE-ENV HELPERS
# Functions in this section receive live poke-env objects.
# =============================================================================

def get_pokemon_types(pokemon) -> list:
    """
    Extract type strings from a **live poke-env Pokemon object**.

    Use this during battles when you have a poke-env object (battle.active_pokemon,
    battle.opponent_active_pokemon, etc.).

    For species-name lookups (e.g. inside the damage calculator or matchup
    evaluator), use gen1_data.get_types(species_str) instead — it reads from
    the static POKEMON table without touching poke-env at all.
    """
    types = []
    if pokemon.type_1:
        types.append(pokemon.type_1.name.lower())
    if pokemon.type_2:
        types.append(pokemon.type_2.name.lower())
    return types


def best_move_effectiveness(moves, defender_types, attacker_types=None):
    """
    From a list of poke-env Move objects, return (best_move, best_eff_multiplier)
    for the move with highest type effectiveness × adjusted base_power.

    Scoring:
    - STAB: 1.5x if move type matches attacker type
    - Hyper Beam penalised 50% BP neutral / 75% SE (recharge cost)
    - Explosion/Self-Destruct and status/forced moves excluded (LLM or forced)
    """
    best_move = None
    best_score = -1
    best_eff = 1.0

    _skip_auto = {'struggle', 'recharge'} | LLM_ONLY_MOVES

    for move in moves:
        if move.id in _skip_auto:
            continue
        move_type = move.type.name.lower() if move.type else 'normal'
        eff = type_effectiveness(move_type, defender_types)
        raw_bp = move.base_power or 0

        if move.id in FIXED_DAMAGE_MOVES:
            score = 0 if eff == 0 else 100
            if score > best_score:
                best_score = score
                best_move = move
                best_eff = 1.0
            continue

        stab = 1.5 if attacker_types and move_type in attacker_types else 1.0

        if move.id == 'hyperbeam':
            adj_bp = raw_bp * 0.5 if eff <= 1 else raw_bp * 0.75
        else:
            adj_bp = raw_bp

        score = adj_bp * eff * stab
        if score > best_score:
            best_score = score
            best_move = move
            best_eff = eff

    return best_move, best_eff


def worst_incoming_effectiveness(opponent_move_types: list, my_types: list) -> float:
    """
    Given the TYPES (not names) of revealed opponent moves, return the
    highest effectiveness they can achieve against my_types.
    """
    worst = 1.0
    for move_type in opponent_move_types:
        eff = type_effectiveness(move_type, my_types)
        worst = max(worst, eff)
    return worst


def find_best_switch(battle, threat_type=None):
    """
    Find the best available switch target.
    Priority: immune to threat > resists threat > most HP > not active.

    Args:
        battle:      poke-env Battle object
        threat_type: type string we're escaping (e.g. 'electric'), or None

    Returns Pokemon object or None.
    """
    candidates = [p for p in battle.available_switches if not p.fainted]
    if not candidates:
        return None

    def switch_score(p):
        types = get_pokemon_types(p)
        hp_factor = p.current_hp_fraction
        if threat_type:
            eff = type_effectiveness(threat_type, types)
            if eff == 0:
                return 1000 + hp_factor
            if eff < 1:
                return 100 + hp_factor
            if eff > 1:
                return hp_factor - 10
        return hp_factor

    return max(candidates, key=switch_score)


# =============================================================================
# MOVE TYPE RESOLUTION
# register_move_type / get_move_type are imported from gen1_data — the runtime
# cache lives there so there is exactly one cache for the whole process.
# resolve_move_types is a convenience wrapper kept here as part of the engine API.
# =============================================================================

def resolve_move_types(move_ids: list) -> list:
    """Convert a list of move IDs to their types. Skips unknowns."""
    return [t for mid in move_ids for t in [get_move_type(mid)] if t]


# =============================================================================
# MATCHUP SWITCH FINDER
# evaluate_matchup() lives in gen1_data (pure species-string math).
# find_best_matchup_switch() lives here because it iterates poke-env switch
# objects to read .species / .current_hp_fraction / .status.
# =============================================================================

def find_best_matchup_switch(our_active_species: str, our_active_moves: list,
                              opp_species: str, switches: list,
                              our_active_hp: float = 1.0,
                              our_active_status: str = None,
                              opp_hp: float = 1.0,
                              opp_status: str = None) -> tuple:
    """
    Find whether any available switch-in has a significantly better matchup
    than staying in. Iterates poke-env switch objects.

    Returns (switch_pokemon, score_diff) if a switch is recommended,
    (None, 0) if staying in is better.
    """
    current_score = evaluate_matchup(
        our_active_species, opp_species,
        our_moves=our_active_moves,
        our_hp_pct=our_active_hp,
        opp_hp_pct=opp_hp,
        our_status=our_active_status,
        opp_status=opp_status,
    )

    best_switch = None
    best_score = current_score
    SWITCH_THRESHOLD = 120  # switching costs a full turn; must be clearly better

    for sw in switches:
        sw_species = sw.species.lower()
        sw_hp      = sw.current_hp_fraction or 0
        sw_status  = sw.status.name if sw.status else None

        # Skip sleeping/frozen — they can't act and trigger immediate re-switch
        if sw_status in ('SLP', 'FRZ'):
            continue
        # Skip critically low HP — feeding the opponent a free KO
        if sw_hp < 0.15:
            continue

        sw_moves = [m.id for m in sw.moves.values()] if sw.moves else []
        sw_score = evaluate_matchup(
            sw_species, opp_species,
            our_moves=sw_moves or None,
            our_hp_pct=sw_hp,
            opp_hp_pct=opp_hp,
            our_status=sw_status,
            opp_status=opp_status,
        )

        if sw_score > best_score + SWITCH_THRESHOLD:
            best_score = sw_score
            best_switch = sw

    if best_switch:
        return (best_switch, best_score - current_score)
    return (None, 0)


# =============================================================================
# SELF-TEST  (no poke-env objects needed — just exercises the re-exports)
# =============================================================================

if __name__ == '__main__':
    print("gen1_engine self-test — exercises re-exports from gen1_data\n")

    print("Ghost → Psychic (Gen 1 bug, should be 0x):",
          type_effectiveness('ghost', ['psychic']),
          '✓' if type_effectiveness('ghost', ['psychic']) == 0 else '✗')
    print("Psychic → Gengar Ghost/Poison (should be 2x):",
          type_effectiveness('psychic', ['ghost', 'poison']),
          '✓' if type_effectiveness('psychic', ['ghost', 'poison']) == 2 else '✗')
    print()

    lo, hi = calc_damage('tauros', 'bodyslam', 'alakazam')
    plo, phi = calc_damage_pct('tauros', 'bodyslam', 'alakazam')
    print(f"Tauros Body Slam vs Alakazam: {lo}-{hi}  ({plo*100:.0f}%-{phi*100:.0f}%)")
    print()

    for mon in ['jolteon', 'alakazam', 'tauros', 'starmie', 'snorlax', 'chansey']:
        print(f"  {mon:12s}: speed={get_speed(mon):3d}  paralyzed={get_speed(mon, True)}")
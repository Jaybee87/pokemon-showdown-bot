"""
gen1_engine.py
==============
Battle-layer functions that require poke-env objects.

Layer contract
--------------
This file deals with live battle state — poke-env Battle objects, Pokemon
objects, and Move objects.

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
    # Calculations
    apply_stage,
    get_move
)

# =============================================================================
# DAMAGE CALCULATOR
# All inputs are species-name strings and move IDs.
# Standard assumptions: Level 100, 15 DVs, max Stat EXP.
# =============================================================================

def calc_damage(attacker: str, move_id: str, defender: str,
                crit: bool = False,
                atk_boosts: dict = None, def_boosts: dict = None,
                reflect: bool = False, light_screen: bool = False,
                attacker_burned: bool = False) -> tuple:
    """
    Damage range for a specific attack at L100, 15 DVs, max Stat EXP.

    Args:
        attacker, defender: species name strings (e.g. 'tauros')
        move_id:            move ID (e.g. 'bodyslam')
        crit:               critical hit — ignores stat stages in Gen 1
        atk_boosts:         attacker's stat boost dict
        def_boosts:         defender's stat boost dict
        reflect:            Reflect active on defender's side
        light_screen:       Light Screen active on defender's side
        attacker_burned:    attacker burned (halves physical Attack)

    Returns:
        (min_damage, max_damage) — (0, 0) for immune/status, (100, 100) for fixed.
    """
    move = get_move(move_id)
    if move is None:
        return (0, 0)

    bp, move_type, category, _hits = move
    atk_boosts = atk_boosts or {}
    def_boosts = def_boosts or {}

    if category == 'status':
        return (0, 0)

    # Fixed-damage moves (Seismic Toss, Night Shade, etc.)
    if category in ('fixed', 'ohko') or move_id in FIXED_DAMAGE_MOVES:
        def_types = get_types(defender)
        return (0, 0) if type_effectiveness(move_type, def_types) == 0 else (100, 100)

    atk_stats = get_stats(attacker)
    def_stats = get_stats(defender)
    if not atk_stats or not def_stats:
        return (0, 0)

    atk_types = get_types(attacker)
    def_types = get_types(defender)

    # Physical/Special split is TYPE-based in Gen 1
    is_special = move_type in SPECIAL_TYPES
    if is_special:
        attack  = atk_stats[3]   # Special (offense)
        defense = def_stats[3]   # Special (defense)
        atk_stage = atk_boosts.get('spc', atk_boosts.get('spa', 0))
        def_stage = def_boosts.get('spc', def_boosts.get('spd', 0))
    else:
        attack  = atk_stats[1]   # Attack
        defense = def_stats[2]   # Defense
        atk_stage = atk_boosts.get('atk', 0)
        def_stage = def_boosts.get('def', 0)

    # Crits ignore stat stages in Gen 1
    if not crit:
        attack  = apply_stage(attack,  atk_stage)
        defense = apply_stage(defense, def_stage)

    # Burn halves physical Attack (applied after stages)
    if attacker_burned and not is_special:
        attack = max(1, attack // 2)

    # Explosion/Self-Destruct halve target's Defense in Gen 1
    if move_id in ('explosion', 'selfdestruct'):
        defense = max(1, defense // 2)

    # Screens double the relevant defense (crits ignore screens)
    if not crit:
        if not is_special and reflect:
            defense = min(999, defense * 2)
        elif is_special and light_screen:
            defense = min(999, defense * 2)

    eff = type_effectiveness(move_type, def_types)
    if eff == 0:
        return (0, 0)

    stab = 1.5 if move_type in atk_types else 1.0

    # Gen 1 damage formula: ((2*Level/5 + 2) * Power * Atk / Def) / 50 + 2
    base = ((2 * 100 / 5 + 2) * bp * attack / defense) / 50 + 2
    base = int(base * stab)
    base = int(base * eff)

    # Random factor: 217/255 – 255/255
    min_dmg = max(1, int(base * 217 / 255))
    max_dmg = base

    return (min_dmg, max_dmg)


def calc_damage_pct(attacker: str, move_id: str, defender: str, **kwargs) -> tuple:
    """Damage as a fraction of defender's max HP. Returns (min_pct, max_pct)."""
    lo, hi = calc_damage(attacker, move_id, defender, **kwargs)
    def_stats = get_stats(defender)
    if not def_stats:
        return (0.0, 0.0)
    return (lo / def_stats[0], hi / def_stats[0])


# =============================================================================
# KO CHECKS
# =============================================================================

def can_ko(attacker: str, move_id: str, defender: str,
           hp_pct: float = 1.0, use_avg: bool = True, **kwargs) -> bool:
    """
    Can this move KO the defender at the given HP fraction?

    Args:
        hp_pct:  defender's current HP as 0.0–1.0
        use_avg: True → use average roll; False → minimum (guaranteed KO only)
    """
    lo, hi = calc_damage(attacker, move_id, defender, **kwargs)
    def_stats = get_stats(defender)
    if not def_stats:
        return False
    current_hp = int(def_stats[0] * hp_pct)
    return ((lo + hi) // 2 >= current_hp) if use_avg else (lo >= current_hp)


def find_ko_move(attacker: str, moves: list, defender: str,
                 hp_pct: float = 1.0, **kwargs) -> tuple:
    """
    From a list of move IDs, find the best one that KOs the defender.
    Prefers guaranteed KOs (min roll) over average KOs.

    Returns (move_id, guaranteed) or (None, False).
    """
    best_guaranteed = None
    best_avg = None

    for move_id in moves:
        lo, hi = calc_damage(attacker, move_id, defender, **kwargs)
        def_stats = get_stats(defender)
        if not def_stats:
            continue
        current_hp = int(def_stats[0] * hp_pct)

        if lo >= current_hp:
            if best_guaranteed is None or lo > best_guaranteed[1]:
                best_guaranteed = (move_id, lo)
        elif (lo + hi) // 2 >= current_hp:
            if best_avg is None or (lo + hi) > best_avg[1]:
                best_avg = (move_id, lo + hi)

    if best_guaranteed:
        return (best_guaranteed[0], True)
    if best_avg:
        return (best_avg[0], False)
    return (None, False)


def can_2hko(attacker: str, move_id: str, defender: str, hp_pct: float = 1.0) -> bool:
    """Can two hits KO the defender at current HP? Uses average roll."""
    lo, hi = calc_damage(attacker, move_id, defender)
    def_stats = get_stats(defender)
    if not def_stats:
        return False
    return ((lo + hi) // 2) * 2 >= int(def_stats[0] * hp_pct)


# =============================================================================
# SPEED
# In Gen 1 there is no Speed variance at max DVs/StatEXP — every species has
# exactly one speed value (quartered by paralysis). get_speed() derives it
# from the stats formula; outspeeds() is the only comparison needed.
# =============================================================================

def get_speed(species: str, paralyzed: bool = False) -> int:
    """Calculated speed at L100/15DVs/maxStatEXP. Paralysis quarters it."""
    stats = get_stats(species)
    if not stats:
        return 0
    return stats[4] // 4 if paralyzed else stats[4]


def outspeeds(species_a: str, species_b: str,
              a_par: bool = False, b_par: bool = False) -> bool:
    """
    Does species_a outspeed species_b?
    Speed ties are broken randomly in Gen 1 — returns False (conservative).
    """
    return get_speed(species_a, a_par) > get_speed(species_b, b_par)


# =============================================================================
# FREEZE CHANCE VALUE
# All damaging Ice-type moves have a 10% freeze chance in Gen 1.
# Freeze is essentially permanent — significant strategic value.
# =============================================================================

def freeze_chance_value(move_id: str, defender_species: str) -> int:
    """
    Bonus score (0–15) for freeze potential.
    Returns 0 if the move can't freeze or the defender is already Ice-type.
    """
    if move_id not in FREEZE_MOVES:
        return 0
    if 'ice' in get_types(defender_species):
        return 0
    return 15  # 10% of a KO ≈ significant but not dominant


# =============================================================================
# SUBSTITUTE HELPERS
# Sub HP = floor(max_hp / 4). Status moves fail against Subs.
# =============================================================================

def get_substitute_hp(species: str) -> int:
    """HP of a Substitute for this species (floor(max_hp / 4))."""
    stats = get_stats(species)
    return stats[0] // 4 if stats else 0


def can_break_substitute(attacker: str, move_id: str, defender: str, **kwargs) -> bool:
    """Can this move break the defender's Substitute in one hit? Uses average roll."""
    lo, hi = calc_damage(attacker, move_id, defender, **kwargs)
    sub_hp = get_substitute_hp(defender)
    return sub_hp > 0 and (lo + hi) // 2 >= sub_hp


# =============================================================================
# MATCHUP EVALUATOR
# Scores how well our_species matches up against opp_species.
# All inputs are species strings and move-ID lists — no poke-env objects.
# find_best_matchup_switch() lives in gen1_engine because it iterates
# poke-env switch objects to read .species / .current_hp_fraction / .status.
# =============================================================================

def evaluate_matchup(our_species: str, opp_species: str,
                     our_moves: list = None,
                     our_hp_pct: float = 1.0, opp_hp_pct: float = 1.0,
                     our_status: str = None, opp_status: str = None) -> float:
    """
    Score how well our_species matches up against opp_species.
    Higher = better matchup. Negative = bad matchup (roughly –100 to +100).

    Considers: offensive damage output, defensive typing, speed, HP, status.
    """
    if not get_stats(our_species) or not get_stats(opp_species):
        return 0.0

    our_types = get_types(our_species)
    opp_types = get_types(opp_species)
    score = 0.0

    # 1. Offensive pressure — best average damage we can deal
    if our_moves:
        best_dmg_pct = 0.0
        for move_id in our_moves:
            m = get_move(move_id)
            if m and m[2] != 'status':
                lo, hi = calc_damage_pct(our_species, move_id, opp_species)
                avg = (lo + hi) / 2
                if avg > best_dmg_pct:
                    best_dmg_pct = avg
        score += best_dmg_pct * 200  # 50% damage/turn ≈ +100 points

    # 2. Defensive typing vs opponent's STAB
    worst_incoming = max(
        (type_effectiveness(t, our_types) for t in opp_types),
        default=1.0
    )
    if worst_incoming >= 2:
        score -= 40
    elif worst_incoming == 0:
        score += 50
    elif worst_incoming <= 0.5:
        score += 30

    # 3. Speed advantage
    our_par = our_status == 'PAR'
    opp_par = opp_status == 'PAR'
    score += 10 if outspeeds(our_species, opp_species, a_par=our_par, b_par=opp_par) else -5

    # 4. HP penalty
    if our_hp_pct < 0.30:
        score -= 30
    elif our_hp_pct < 0.50:
        score -= 10

    # 5. Status penalties
    if our_status == 'SLP':   score -= 40
    elif our_status == 'FRZ': score -= 50
    elif our_status == 'PAR': score -= 10

    return score

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
# resolve_move_types is a convenience wrapper kept here as part of the engine API.
# =============================================================================

def resolve_move_types(move_ids: list) -> list:
    """Convert a list of move IDs to their types. Skips unknowns."""
    return [t for mid in move_ids for t in [get_move_type(mid)] if t]

# =============================================================================
# RUNTIME MOVE-TYPE CACHE
# Populated during battles from live poke-env Move objects so that moves not
# in the static MOVES table (e.g. obscure event moves) can still be resolved.
# gen1_engine.register_move_type / get_move_type delegate here — there is
# exactly ONE cache and ONE get_move_type in the whole codebase.
# =============================================================================

_move_type_cache: dict[str, str] = {}


def register_move_type(move_id: str, move_type: str) -> None:
    """Cache a move's type seen from a live poke-env Move object."""
    _move_type_cache[move_id.lower()] = move_type.lower()


def get_move_type(move_id: str) -> str | None:
    """
    Return the type string for a move, or None if unknown.

    Resolution order:
      1. Runtime cache (populated from live battle data via register_move_type)
      2. Static MOVES table in this file
    """
    key = move_id.lower().replace(' ', '').replace('-', '')
    cached = _move_type_cache.get(key)
    if cached:
        return cached
    m = MOVES.get(key)
    return m[1] if m else None


def is_damaging(move_id: str) -> bool:
    """True if the move deals damage (physical, special, fixed, or ohko)."""
    return get_move_category(move_id) != 'status'


def average_hits(move_id: str) -> float:
    """
    Return the expected number of hits for a move.
    Multi-hit (2-5): average is ~3.17 in Gen 1 (uniform distribution).
    """
    key = move_id.lower().replace(' ', '').replace('-', '')
    m = MOVES.get(key)
    if not m:
        return 1.0
    lo, hi = m[2]
    if lo == hi:
        return float(lo)
    return sum(range(lo, hi + 1)) / (hi - lo + 1)



# =============================================================================
# MATCHUP SWITCH FINDER
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
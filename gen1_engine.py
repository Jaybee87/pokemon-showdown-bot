"""
gen1_engine.py
==============
Gen 1 battle mechanics — single source of truth for type interactions,
damage calculations, and all battle-math helpers.

Merged from: gen1_engine.py (type system) + gen1_calc.py (damage calc).
All data (Pokémon stats, move table, type-category sets) now live in
gen1_data.py and are imported from there — no duplication.

Key Gen 1 rules encoded here:
- No Dark, Steel, or Fairy types (15 types total)
- Ghost → Psychic = 0x  (RBY bug — Ghost has NO effect on Psychic)
- Poison super-effective against Bug (and vice versa)
- Special stat is single value (both Sp.Atk and Sp.Def)
- No held items, no abilities

Public API (unchanged — competitive_player.py imports stay valid):
    Type system:
        type_effectiveness(move_type, defender_types) → float
        get_pokemon_types(poke_env_pokemon) → [str]
        best_move_effectiveness(moves, defender_types, attacker_types) → (move, eff)
        worst_incoming_effectiveness(move_types, my_types) → float
        find_best_switch(battle, threat_type) → Pokemon | None
        register_move_type(move_id, move_type)
        get_move_type(move_id) → str | None
        resolve_move_types(move_ids) → [str]
        get_weaknesses / get_strengths / get_weaknesses_summary
        get_immunities / get_resistances

    Damage calc (previously in gen1_calc.py):
        calc_damage(attacker, move_id, defender, ...) → (min, max)
        calc_damage_pct(attacker, move_id, defender, ...) → (min_pct, max_pct)
        can_ko(attacker, move_id, defender, hp_pct, ...) → bool
        find_ko_move(attacker, moves, defender, hp_pct, ...) → (move_id, guaranteed)
        can_2hko(attacker, move_id, defender, hp_pct) → bool
        get_speed(species, paralyzed) → int
        outspeeds(a, b, a_par, b_par) → bool
        get_speed_tier(species, paralyzed) → str
        evaluate_matchup(...) → float
        find_best_matchup_switch(...) → (Pokemon | None, float)
        freeze_chance_value(move_id, defender_species) → int
        get_substitute_hp(species) → int
        can_break_substitute(attacker, move_id, defender, ...) → bool
        apply_stage(stat, stage) → int

    Constants (re-exported from gen1_data for backwards compat):
        TYPES, TYPE_CHART
        FIXED_DAMAGE_MOVES, OHKO_MOVES, SLEEP_MOVES, LLM_ONLY_MOVES,
        IGNORE_MOVES, FREEZE_MOVES, TRAPPING_MOVES
"""

from gen1_data import (
    POKEMON, MOVES, SPECIAL_TYPES,
    FIXED_DAMAGE_MOVES, OHKO_MOVES, SLEEP_MOVES, LLM_ONLY_MOVES,
    IGNORE_MOVES, FREEZE_MOVES, TRAPPING_MOVES,
    get_stats, get_types, get_move,
)

# =============================================================================
# TYPE SYSTEM
# =============================================================================

TYPES = [
    'normal', 'fire', 'water', 'electric', 'grass', 'ice', 'fighting',
    'poison', 'ground', 'flying', 'psychic', 'bug', 'rock', 'ghost', 'dragon'
]

# type_chart[attacking_type][defending_type] = multiplier
# Authoritative Gen 1 chart — no other file should define one.
TYPE_CHART = {
    #              nor  fir  wat  ele  gra  ice  fig  poi  gro  fly  psy  bug  roc  gho  dra
    'normal':   {'normal':1,'fire':1,'water':1,'electric':1,'grass':1,'ice':1,'fighting':1,'poison':1,'ground':1,'flying':1,'psychic':1,'bug':1,'rock':0.5,'ghost':0,'dragon':1},
    'fire':     {'normal':1,'fire':0.5,'water':0.5,'electric':1,'grass':2,'ice':2,'fighting':1,'poison':1,'ground':1,'flying':1,'psychic':1,'bug':2,'rock':0.5,'ghost':1,'dragon':0.5},
    'water':    {'normal':1,'fire':2,'water':0.5,'electric':1,'grass':0.5,'ice':1,'fighting':1,'poison':1,'ground':2,'flying':1,'psychic':1,'bug':1,'rock':2,'ghost':1,'dragon':0.5},
    'electric': {'normal':1,'fire':1,'water':2,'electric':0.5,'grass':0.5,'ice':1,'fighting':1,'poison':1,'ground':0,'flying':2,'psychic':1,'bug':1,'rock':1,'ghost':1,'dragon':0.5},
    'grass':    {'normal':1,'fire':0.5,'water':2,'electric':1,'grass':0.5,'ice':1,'fighting':1,'poison':0.5,'ground':2,'flying':0.5,'psychic':1,'bug':0.5,'rock':2,'ghost':1,'dragon':0.5},
    'ice':      {'normal':1,'fire':1,'water':0.5,'electric':1,'grass':2,'ice':0.5,'fighting':1,'poison':1,'ground':2,'flying':2,'psychic':1,'bug':1,'rock':1,'ghost':1,'dragon':2},
    'fighting': {'normal':2,'fire':1,'water':1,'electric':1,'grass':1,'ice':2,'fighting':1,'poison':0.5,'ground':1,'flying':0.5,'psychic':0.5,'bug':0.5,'rock':2,'ghost':0,'dragon':1},
    'poison':   {'normal':1,'fire':1,'water':1,'electric':1,'grass':2,'ice':1,'fighting':1,'poison':0.5,'ground':0.5,'flying':1,'psychic':1,'bug':2,'rock':0.5,'ghost':0.5,'dragon':1},
    'ground':   {'normal':1,'fire':2,'water':1,'electric':2,'grass':0.5,'ice':1,'fighting':1,'poison':2,'ground':1,'flying':0,'psychic':1,'bug':0.5,'rock':2,'ghost':1,'dragon':1},
    'flying':   {'normal':1,'fire':1,'water':1,'electric':0.5,'grass':2,'ice':1,'fighting':2,'poison':1,'ground':1,'flying':1,'psychic':1,'bug':2,'rock':0.5,'ghost':1,'dragon':1},
    'psychic':  {'normal':1,'fire':1,'water':1,'electric':1,'grass':1,'ice':1,'fighting':2,'poison':2,'ground':1,'flying':1,'psychic':0.5,'bug':1,'rock':1,'ghost':1,'dragon':1},
    'bug':      {'normal':1,'fire':0.5,'water':1,'electric':1,'grass':2,'ice':1,'fighting':0.5,'poison':2,'ground':1,'flying':0.5,'psychic':2,'bug':1,'rock':1,'ghost':0.5,'dragon':1},
    'rock':     {'normal':1,'fire':2,'water':1,'electric':1,'grass':1,'ice':2,'fighting':0.5,'poison':1,'ground':0.5,'flying':2,'psychic':1,'bug':2,'rock':1,'ghost':1,'dragon':1},
    'ghost':    {'normal':0,'fire':1,'water':1,'electric':1,'grass':1,'ice':1,'fighting':0,'poison':1,'ground':1,'flying':1,'psychic':0,'bug':1,'rock':1,'ghost':2,'dragon':1},
    'dragon':   {'normal':1,'fire':1,'water':1,'electric':1,'grass':1,'ice':1,'fighting':1,'poison':1,'ground':1,'flying':1,'psychic':1,'bug':1,'rock':1,'ghost':1,'dragon':2},
}


# =============================================================================
# EFFECTIVENESS CALCULATIONS
# =============================================================================

def type_effectiveness(move_type: str, defender_types: list) -> float:
    """
    Combined type effectiveness multiplier.

    Args:
        move_type:      e.g. 'fire'
        defender_types: list of 1-2 type strings, e.g. ['water', 'ice']

    Returns float: 0, 0.25, 0.5, 1, 2, or 4
    """
    chart = TYPE_CHART.get(move_type.lower(), {})
    mult = 1.0
    for t in defender_types:
        if t:
            mult *= chart.get(t.lower(), 1.0)
    return mult


def get_pokemon_types(pokemon) -> list:
    """Extract type strings from a poke-env Pokemon object."""
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
# MOVE TYPE RESOLUTION  (runtime cache for opponent move tracking)
# =============================================================================

_move_type_cache: dict[str, str] = {}


def register_move_type(move_id: str, move_type: str):
    """Cache a move's type from a poke-env Move object we've seen."""
    _move_type_cache[move_id.lower()] = move_type.lower()


def get_move_type(move_id: str) -> str | None:
    """
    Look up a move's type.
    Checks the runtime cache first (populated from live battle data),
    then falls back to the static gen1_data move table.
    Returns None if unknown.
    """
    cached = _move_type_cache.get(move_id.lower())
    if cached:
        return cached
    m = get_move(move_id)
    return m[1] if m else None


def resolve_move_types(move_ids: list) -> list:
    """Convert a list of move IDs to their types. Skips unknowns."""
    return [t for mid in move_ids for t in [get_move_type(mid)] if t]


# =============================================================================
# DEFENSIVE ANALYSIS  (used by team_generator)
# =============================================================================

def get_weaknesses(type1: str, type2: str = None) -> list:
    """15-element defensive multiplier array for a type combination."""
    w1 = [TYPE_CHART[atk][type1] for atk in TYPES]
    if type2:
        w2 = [TYPE_CHART[atk][type2] for atk in TYPES]
        return [a * b for a, b in zip(w1, w2)]
    return w1


def get_strengths(type1: str, type2: str = None) -> list:
    """15-element offensive matchup array — best multiplier either type gets."""
    e1 = [TYPE_CHART[type1][d] for d in TYPES]
    if type2:
        e2 = [TYPE_CHART[type2][d] for d in TYPES]
        return [max(m1, m2) for m1, m2 in zip(e1, e2)]
    return e1


def get_weaknesses_summary(type1: str, type2: str = None) -> dict:
    """Dict of types that deal super effective damage to this Pokémon."""
    w = get_weaknesses(type1, type2)
    return {TYPES[i]: w[i] for i in range(len(TYPES)) if w[i] > 1}


def get_immunities(type1: str, type2: str = None) -> list:
    """List of types this Pokémon is immune to."""
    w = get_weaknesses(type1, type2)
    return [TYPES[i] for i in range(len(TYPES)) if w[i] == 0]


def get_resistances(type1: str, type2: str = None) -> list:
    """List of types this Pokémon resists."""
    w = get_weaknesses(type1, type2)
    return [TYPES[i] for i in range(len(TYPES)) if 0 < w[i] < 1]


# =============================================================================
# STAT STAGE MULTIPLIERS
# Gen 1: multiplier = max(2, 2+s) / max(2, 2-s)
# =============================================================================

_STAGE_MULT = {
    -6: (2, 8), -5: (2, 7), -4: (2, 6), -3: (2, 5),
    -2: (2, 4), -1: (2, 3),  0: (2, 2),  1: (3, 2),
     2: (4, 2),  3: (5, 2),  4: (6, 2),  5: (7, 2),
     6: (8, 2),
}


def apply_stage(stat: int, stage: int) -> int:
    """Apply a stat-stage modifier; capped at 999 in Gen 1."""
    stage = max(-6, min(6, stage))
    num, den = _STAGE_MULT[stage]
    return min(999, max(1, stat * num // den))


# =============================================================================
# DAMAGE CALCULATOR
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
        (min_damage, max_damage)  — (0, 0) for immune/status, (100, 100) for fixed.
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

    # Gen 1 damage formula:
    # ((2*Level/5 + 2) * Power * Atk / Def) / 50 + 2
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
    max_hp = def_stats[0]
    return (lo / max_hp, hi / max_hp)


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
# SPEED TABLE
# =============================================================================

def get_speed(species: str, paralyzed: bool = False) -> int:
    """Effective speed. Paralysis quarters it in Gen 1."""
    stats = get_stats(species)
    if not stats:
        return 0
    speed = stats[4]
    return speed // 4 if paralyzed else speed


def outspeeds(species_a: str, species_b: str,
              a_par: bool = False, b_par: bool = False) -> bool:
    """
    Does species_a outspeed species_b?
    Ties broken randomly in Gen 1 — we return False (conservative).
    """
    return get_speed(species_a, a_par) > get_speed(species_b, b_par)


def get_speed_tier(species: str, paralyzed: bool = False) -> str:
    """Human-readable speed tier label for LLM prompts / debugging."""
    speed = get_speed(species, paralyzed)
    if speed >= 350:  return "blazing"
    if speed >= 300:  return "fast"
    if speed >= 250:  return "moderate"
    if speed >= 200:  return "slow"
    return "very slow"


# =============================================================================
# FREEZE CHANCE VALUE
# 10% chance per hit from any Ice-type damaging move in Gen 1.
# Freeze is essentially permanent — very high strategic value.
# =============================================================================

def freeze_chance_value(move_id: str, defender_species: str) -> int:
    """
    Bonus score (0–15) for freeze potential.
    Returns 0 if the move can't freeze or the defender is Ice-type.
    """
    if move_id not in FREEZE_MOVES:
        return 0
    if 'ice' in get_types(defender_species):
        return 0
    return 15  # 10% of a KO = significant but not dominant


# =============================================================================
# SUBSTITUTE TRACKING
# Sub HP = floor(max_hp / 4).  Status moves fail against Subs.
# =============================================================================

def get_substitute_hp(species: str) -> int:
    """HP of a Substitute for this species (floor(max_hp / 4))."""
    stats = get_stats(species)
    return stats[0] // 4 if stats else 0


def can_break_substitute(attacker: str, move_id: str, defender: str, **kwargs) -> bool:
    """Can this move break the defender's Sub in one hit? Uses average roll."""
    lo, hi = calc_damage(attacker, move_id, defender, **kwargs)
    sub_hp = get_substitute_hp(defender)
    return sub_hp > 0 and (lo + hi) // 2 >= sub_hp


# =============================================================================
# MATCHUP EVALUATOR — smart switching
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

    # 1. Offensive pressure
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


def find_best_matchup_switch(our_active_species: str, our_active_moves: list,
                              opp_species: str, switches: list,
                              our_active_hp: float = 1.0,
                              our_active_status: str = None,
                              opp_hp: float = 1.0,
                              opp_status: str = None) -> tuple:
    """
    Find whether any switch-in has a significantly better matchup than staying.

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
# SELF-TEST
# =============================================================================

if __name__ == '__main__':
    print("Gen 1 Engine — combined self-test\n")

    # Type chart sanity
    print("Ghost → Psychic (Gen 1 bug, should be 0x):",
          type_effectiveness('ghost', ['psychic']),
          '✓' if type_effectiveness('ghost', ['psychic']) == 0 else '✗')

    print("Psychic → Gengar Ghost/Poison (should be 2x):",
          type_effectiveness('psychic', ['ghost', 'poison']),
          '✓' if type_effectiveness('psychic', ['ghost', 'poison']) == 2 else '✗')

    print("Bug → Psychic (should be 2x):",
          type_effectiveness('bug', ['psychic']),
          '✓' if type_effectiveness('bug', ['psychic']) == 2 else '✗')

    print()

    # Damage calc
    lo, hi = calc_damage('tauros', 'bodyslam', 'alakazam')
    plo, phi = calc_damage_pct('tauros', 'bodyslam', 'alakazam')
    print(f"Tauros Body Slam vs Alakazam: {lo}-{hi}  ({plo*100:.0f}%-{phi*100:.0f}%)")

    lo, hi = calc_damage('alakazam', 'psychic', 'gengar')
    print(f"Alakazam Psychic vs Gengar:   {lo}-{hi}")

    lo, hi = calc_damage('chansey', 'seismictoss', 'snorlax')
    print(f"Chansey Seismic Toss vs Snorlax: {lo}-{hi}  (should be 100-100)")

    print()

    # Speed tiers
    for mon in ['jolteon', 'alakazam', 'tauros', 'starmie', 'gengar', 'snorlax', 'chansey']:
        print(f"  {mon:12s}: {get_speed(mon):3d}  ({get_speed_tier(mon)})"
              f"  paralyzed: {get_speed(mon, True)}")

    print()
    print(f"KO check — Tauros Body Slam vs Alakazam at 45%: {can_ko('tauros','bodyslam','alakazam',0.45)}")
    ko_m, ko_g = find_ko_move('starmie', ['surf','thunderbolt','psychic'], 'snorlax', 0.20)
    print(f"Best KO move — Starmie vs Snorlax 20%: {ko_m} (guaranteed={ko_g})")

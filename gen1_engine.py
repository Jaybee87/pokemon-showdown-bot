"""
gen1_engine.py
==============
Gen 1 battle mechanics engine — the single source of truth for type interactions,
effectiveness calculations, and move scoring.

Consolidates gen1_type_chart.py + competitive_player.py's inline type chart.
All modules import type logic from here.

Key Gen 1 differences from Gen 2+:
- No Dark, Steel, or Fairy types (15 types total)
- Ghost has NO effect on Psychic (RBY bug — intended 2x becomes 0x)
- Psychic is NOT immune to Ghost (Ghost→Psychic = 0x, but Psychic→Ghost = 1x)
- Poison is super effective against Bug (and vice versa)
- Bug is super effective against Poison
- No held items, no abilities, no EVs/IVs in the modern sense
"""

# =============================================================================
# TYPE SYSTEM
# =============================================================================

TYPES = [
    'normal', 'fire', 'water', 'electric', 'grass', 'ice', 'fighting',
    'poison', 'ground', 'flying', 'psychic', 'bug', 'rock', 'ghost', 'dragon'
]

# type_chart[attacking_type][defending_type] = multiplier
# This is THE authoritative chart. No other file should define one.
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

def type_effectiveness(move_type, defender_types):
    """
    Calculate combined type effectiveness multiplier.

    Args:
        move_type: string e.g. 'fire'
        defender_types: list of 1-2 type strings e.g. ['water', 'ice']

    Returns:
        float multiplier (0, 0.25, 0.5, 1, 2, or 4)
    """
    chart = TYPE_CHART.get(move_type.lower(), {})
    mult = 1.0
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


def best_move_effectiveness(moves, defender_types, attacker_types=None):
    """
    From a list of poke-env Move objects, return (best_move, best_eff_multiplier)
    for the move with highest type effectiveness * adjusted base_power.

    Scoring:
    - STAB (Same Type Attack Bonus): 1.5x if move type matches attacker type
    - Hyper Beam penalised to 50% BP neutral, 75% BP if SE (recharge cost)
    - Explosion/Self-Destruct excluded (always LLM decision)
    - Struggle/Recharge excluded (forced turns)
    """
    best_move = None
    best_score = -1
    best_eff = 1.0

    for move in moves:
        if move.id in ('struggle', 'recharge', 'explosion', 'selfdestruct'):
            continue
        move_type = move.type.name.lower() if move.type else 'normal'
        eff = type_effectiveness(move_type, defender_types)
        raw_bp = move.base_power or 0

        # Fixed-damage moves: score them at their actual damage (100 at L100)
        # unless they're immune (Night Shade vs Normal, Seismic Toss vs Ghost)
        if move.id in FIXED_DAMAGE_MOVES:
            if eff == 0:
                score = 0
            else:
                score = 100  # consistent 100 damage, ignores type chart
            if score > best_score:
                best_score = score
                best_move = move
                best_eff = 1.0  # neutral — they ignore type chart
            continue

        # STAB bonus
        stab = 1.5 if attacker_types and move_type in attacker_types else 1.0

        # Penalise Hyper Beam for recharge cost
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


def worst_incoming_effectiveness(opponent_move_types, my_types):
    """
    Given the TYPES of moves the opponent has revealed, what's the worst
    type effectiveness they can hit us with?

    IMPORTANT: This expects move TYPE strings ('electric', 'ice'), not move
    names ('thunderbolt', 'icebeam'). The caller must map move names to types
    before calling this.

    Returns max multiplier across known move types.
    """
    worst = 1.0
    for move_type in opponent_move_types:
        eff = type_effectiveness(move_type, my_types)
        worst = max(worst, eff)
    return worst


def find_best_switch(battle, threat_type=None):
    """
    Find the best available switch target.
    Prioritises: immune to threat > resists threat > most HP > not active.

    Args:
        battle: poke-env Battle object
        threat_type: the type string we're trying to escape (e.g. 'electric')

    Returns a Pokemon object or None.
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
                return 1000 + hp_factor    # immune — top priority
            if eff < 1:
                return 100 + hp_factor     # resist
            if eff > 1:
                return hp_factor - 10      # weak — heavily penalise

        return hp_factor

    return max(candidates, key=switch_score)


# =============================================================================
# MOVE TYPE RESOLUTION
# =============================================================================

# Maps poke-env move IDs to their Gen 1 type.
# Used to convert opponent move tracking (which stores move IDs) to types
# for effectiveness calculations.
#
# poke-env provides this via Move.type, but we need it for opponent moves
# that we only see by name in the protocol. This cache is populated at
# runtime from poke-env Move objects as we encounter them.

_move_type_cache = {}


def register_move_type(move_id, move_type):
    """Cache a move's type from a poke-env Move object we've seen."""
    _move_type_cache[move_id] = move_type.lower()


def get_move_type(move_id):
    """
    Look up a move's type from the cache.
    Returns the type string or None if unknown.
    """
    return _move_type_cache.get(move_id.lower())


def resolve_move_types(move_ids):
    """
    Convert a list of move IDs to their types for effectiveness calculation.
    Skips any moves whose type is unknown.
    Returns list of type strings.
    """
    types = []
    for mid in move_ids:
        t = get_move_type(mid)
        if t:
            types.append(t)
    return types


# =============================================================================
# DEFENSIVE ANALYSIS (used by team_generator)
# =============================================================================

def get_weaknesses(type1, type2=None):
    """
    Compute 15-element defensive weakness array for a Gen 1 type combination.
    Returns multipliers for each attacking type against this Pokemon.
    """
    idx1 = TYPES.index(type1)
    w1 = [TYPE_CHART[atk][type1] for atk in TYPES]
    if type2:
        w2 = [TYPE_CHART[atk][type2] for atk in TYPES]
        return [a * b for a, b in zip(w1, w2)]
    return w1


def get_strengths(type1, type2=None):
    """
    Compute 15-element offensive matchup array for a Gen 1 type combination.
    Returns best multiplier either type gets against each defending type.
    """
    e1 = [TYPE_CHART[type1][d] for d in TYPES]
    if type2:
        e2 = [TYPE_CHART[type2][d] for d in TYPES]
        return [max(m1, m2) for m1, m2 in zip(e1, e2)]
    return e1


def get_weaknesses_summary(type1, type2=None):
    """Return dict of types that deal super effective damage to this Pokemon."""
    weaknesses = get_weaknesses(type1, type2)
    return {TYPES[i]: weaknesses[i] for i in range(len(TYPES)) if weaknesses[i] > 1}


def get_immunities(type1, type2=None):
    """Return list of types this Pokemon is immune to."""
    weaknesses = get_weaknesses(type1, type2)
    return [TYPES[i] for i in range(len(TYPES)) if weaknesses[i] == 0]


def get_resistances(type1, type2=None):
    """Return list of types this Pokemon resists."""
    weaknesses = get_weaknesses(type1, type2)
    return [TYPES[i] for i in range(len(TYPES)) if 0 < weaknesses[i] < 1]


# =============================================================================
# SPECIAL MOVE CATEGORIES
# =============================================================================

# Moves that deal fixed damage (ignore type chart)
FIXED_DAMAGE_MOVES = {'seismictoss', 'nightshade', 'sonicboom', 'dragonrage', 'psywave'}

# OHKO moves
OHKO_MOVES = {'guillotine', 'horndrill', 'fissure'}

# Sleep-inducing moves
SLEEP_MOVES = {'hypnosis', 'sleeppowder', 'spore', 'lovelykiss', 'sing'}

# Moves that should never be auto-picked by Python (always defer to LLM)
LLM_ONLY_MOVES = {'explosion', 'selfdestruct', 'counter'}

# Protocol artifacts — not real moveset choices
IGNORE_MOVES = {'recharge', 'struggle', 'splash'}


if __name__ == "__main__":
    # Sanity checks
    print("Gen 1 Engine — type chart verification\n")

    print("Gengar (Ghost/Poison):")
    print(f"  Weak to: {get_weaknesses_summary('ghost', 'poison')}")
    print(f"  Immune to: {get_immunities('ghost', 'poison')}")
    print(f"  Resists: {get_resistances('ghost', 'poison')}")

    print("\nGhost → Psychic (should be 0x — Gen 1 bug):")
    print(f"  {type_effectiveness('ghost', ['psychic'])} — {'CORRECT' if type_effectiveness('ghost', ['psychic']) == 0 else 'WRONG'}")

    print("\nPsychic → Ghost (should be 1x — not immune in Gen 1):")
    print(f"  {type_effectiveness('psychic', ['ghost'])} — {'CORRECT' if type_effectiveness('psychic', ['ghost']) == 1 else 'WRONG'}")

    # Note: Psychic → Ghost/Poison (Gengar):
    # TYPE_CHART['psychic']['ghost'] = 0 (Gen 1 bug: Ghost is immune to Psychic? No!)
    # Actually checking: Psychic→Ghost should be 1x in Gen 1 (not 0x, not 2x)
    # But our chart has psychic→ghost = 1 (correct) so Gengar takes 1x * 2x = 2x
    print("\nPsychic → Gengar (Ghost/Poison, should be 1x * 2x = 2x):")
    eff = type_effectiveness('psychic', ['ghost', 'poison'])
    print(f"  {eff} — {'CORRECT' if eff == 2 else 'WRONG'}")

    print("\nIce → Fire (should be 1x — Ice is neutral against Fire in Gen 1):")
    print(f"  {type_effectiveness('ice', ['fire'])} — {'CORRECT' if type_effectiveness('ice', ['fire']) == 1 else 'WRONG'}")

    print("\nFire → Ice (should be 2x — Fire is still super effective against Ice):")
    print(f"  {type_effectiveness('fire', ['ice'])} — {'CORRECT' if type_effectiveness('fire', ['ice']) == 2 else 'WRONG'}")
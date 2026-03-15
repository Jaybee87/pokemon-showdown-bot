"""
gen1_data.py
============
Single source of truth for all Gen 1 data and calculations that operate
purely on species names, move IDs, and type strings — no poke-env objects.

Layer contract
--------------
This file MUST NOT import from poke-env or gen1_engine.
gen1_engine.py imports from here; the dependency arrow is one-way.

Sections
--------
1. Pokémon table  — base stats + types for all 151 species
2. Move table     — BP, type, hit counts for all Gen 1 moves
3. Move category sets — FIXED_DAMAGE_MOVES, OHKO_MOVES, SLEEP_MOVES, etc.
4. Type system    — TYPES list, TYPE_CHART, type_effectiveness()
5. Defensive analysis — get_weaknesses/strengths/immunities/resistances
6. Pokémon accessors — get_stats(), get_types()
7. Move accessors  — get_move(), get_move_category(), get_move_type(),
                     is_damaging(), average_hits()
8. Runtime move-type cache — register_move_type() for live battle data
9. Stat-stage math — apply_stage()
10. Damage calculator — calc_damage(), calc_damage_pct()
11. KO checks     — can_ko(), find_ko_move(), can_2hko()
12. Speed         — get_speed(), outspeeds()
13. Freeze / Substitute helpers — freeze_chance_value(),
                                  get_substitute_hp(), can_break_substitute()
14. Matchup evaluator — evaluate_matchup()

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
POKEMON TABLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Format:
    'species': [HP, Atk, Def, Spc, Spe, type1, type2_or_None]

Stats are BASE stats (the values printed in the Pokédex / data tables).
Use get_stats() to derive in-battle values at any level/DV/StatExp.

Formula (non-HP):  ((Base + DV)*2 + ceil(sqrt(StatExp))/4) * Lvl/100 + 5
Formula (HP):       same but  + Lvl + 10  instead of + 5

In Gen 1 'Special' is a single stat used for both Sp.Atk and Sp.Def.
Index key: [0]=HP  [1]=Atk  [2]=Def  [3]=Spc  [4]=Spe

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MOVE TABLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Format:
    'move_id': (BP, type, hits)

hits = (min_hits, max_hits).  Single-hit moves use (1, 1).
Multi-hit moves like Pin Missile use (2, 5).

Category is DERIVED from type at runtime via get_move_category(type):
    'physical' — Normal/Fighting/Rock/Ground/Flying/Bug/Ghost/Poison
    'special'  — Fire/Water/Electric/Grass/Ice/Psychic/Dragon

Exceptions stored in dedicated sets (BP=0 identifies these at a glance):
    'status'   — no damage (BP=0, not in FIXED_DAMAGE_MOVES or OHKO_MOVES)
    'fixed'    — fixed damage regardless of stats (FIXED_DAMAGE_MOVES)
    'ohko'     — one-hit KO (OHKO_MOVES)

BP values are raw game data from Moves.asm.  The engine is responsible
for any in-battle modifications (e.g. Explosion/Selfdestruct halve the
target's Defence before the damage roll — that is NOT baked into BP here).
"""

# =============================================================================
# POKEMON — all 151 Gen 1 species
# [HP, Atk, Def, Spc, Spe, type1, type2_or_None]
# =============================================================================

POKEMON = {
    'bulbasaur':    [45,  49,  49,  65,  45,  'grass',    'poison'],
    'ivysaur':      [60,  62,  63,  80,  60,  'grass',    'poison'],
    'venusaur':     [80,  82,  83,  100, 80,  'grass',    'poison'],
    'charmander':   [39,  52,  43,  60,  65,  'fire',     None],
    'charmeleon':   [58,  64,  58,  80,  80,  'fire',     None],
    'charizard':    [78,  84,  78,  85,  100, 'fire',     'flying'],
    'squirtle':     [44,  48,  65,  50,  43,  'water',    None],
    'wartortle':    [59,  63,  80,  65,  58,  'water',    None],
    'blastoise':    [79,  83,  100, 85,  78,  'water',    None],
    'caterpie':     [45,  30,  35,  20,  45,  'bug',      None],
    'metapod':      [50,  20,  55,  25,  30,  'bug',      None],
    'butterfree':   [60,  45,  50,  90,  70,  'bug',      'flying'],
    'weedle':       [40,  35,  30,  20,  50,  'bug',      'poison'],
    'kakuna':       [45,  25,  50,  25,  35,  'bug',      'poison'],
    'beedrill':     [65,  90,  40,  45,  75,  'bug',      'poison'],
    'pidgey':       [40,  45,  40,  35,  56,  'normal',   'flying'],
    'pidgeotto':    [63,  60,  55,  50,  71,  'normal',   'flying'],
    'pidgeot':      [83,  80,  75,  70,  91,  'normal',   'flying'],
    'rattata':      [30,  56,  35,  25,  72,  'normal',   None],
    'raticate':     [55,  81,  60,  50,  97,  'normal',   None],
    'spearow':      [40,  60,  30,  31,  70,  'normal',   'flying'],
    'fearow':       [65,  90,  65,  61,  100, 'normal',   'flying'],
    'ekans':        [35,  60,  44,  40,  55,  'poison',   None],
    'arbok':        [60,  95,  69,  65,  80,  'poison',   None],
    'pikachu':      [35,  55,  30,  50,  90,  'electric', None],
    'raichu':       [60,  90,  55,  90,  110, 'electric', None],
    'sandshrew':    [50,  75,  85,  30,  40,  'ground',   None],
    'sandslash':    [75,  100, 110, 45,  65,  'ground',   None],
    'nidoranf':     [55,  47,  52,  40,  41,  'poison',   None],
    'nidorina':     [70,  62,  67,  55,  56,  'poison',   None],
    'nidoqueen':    [90,  92,  87,  75,  76,  'poison',   'ground'],
    'nidoranm':     [46,  57,  40,  40,  50,  'poison',   None],
    'nidorino':     [61,  72,  57,  55,  65,  'poison',   None],
    'nidoking':     [81,  102, 77,  85,  85,  'poison',   'ground'],
    'clefairy':     [70,  45,  48,  60,  35,  'normal',   None],
    'clefable':     [95,  70,  73,  85,  60,  'normal',   None],
    'vulpix':       [38,  41,  40,  65,  65,  'fire',     None],
    'ninetales':    [73,  76,  75,  100, 100, 'fire',     None],
    'jigglypuff':   [115, 45,  20,  25,  20,  'normal',   None],
    'wigglytuff':   [140, 70,  45,  50,  45,  'normal',   None],
    'zubat':        [40,  45,  35,  40,  55,  'poison',   'flying'],
    'golbat':       [75,  80,  70,  75,  90,  'poison',   'flying'],
    'oddish':       [45,  50,  55,  75,  30,  'grass',    'poison'],
    'gloom':        [60,  65,  70,  85,  40,  'grass',    'poison'],
    'vileplume':    [75,  80,  85,  100, 50,  'grass',    'poison'],
    'paras':        [35,  70,  55,  55,  25,  'bug',      'grass'],
    'parasect':     [60,  95,  80,  80,  30,  'bug',      'grass'],
    'venonat':      [60,  55,  50,  40,  45,  'bug',      'poison'],
    'venomoth':     [70,  65,  60,  90,  90,  'bug',      'poison'],
    'diglett':      [10,  55,  25,  45,  95,  'ground',   None],
    'dugtrio':      [35,  100, 50,  70,  120, 'ground',   None],
    'meowth':       [40,  45,  35,  40,  90,  'normal',   None],
    'persian':      [65,  70,  60,  65,  115, 'normal',   None],
    'psyduck':      [50,  52,  48,  50,  55,  'water',    None],
    'golduck':      [80,  82,  78,  80,  85,  'water',    None],
    'mankey':       [40,  80,  35,  35,  70,  'fighting', None],
    'primeape':     [65,  105, 60,  60,  95,  'fighting', None],
    'growlithe':    [55,  70,  45,  70,  60,  'fire',     None],
    'arcanine':     [90,  110, 80,  100, 95,  'fire',     None],
    'poliwag':      [40,  50,  40,  40,  90,  'water',    None],
    'poliwhirl':    [65,  65,  65,  50,  90,  'water',    None],
    'poliwrath':    [90,  95,  95,  70,  70,  'water',    'fighting'],
    'abra':         [25,  20,  15,  105, 90,  'psychic',  None],
    'kadabra':      [40,  35,  30,  120, 105, 'psychic',  None],
    'alakazam':     [55,  50,  45,  135, 120, 'psychic',  None],
    'machop':       [70,  80,  50,  35,  35,  'fighting', None],
    'machoke':      [80,  100, 70,  50,  45,  'fighting', None],
    'machamp':      [90,  130, 80,  65,  55,  'fighting', None],
    'bellsprout':   [50,  75,  35,  70,  40,  'grass',    'poison'],
    'weepinbell':   [65,  90,  50,  85,  55,  'grass',    'poison'],
    'victreebel':   [80,  105, 65,  100, 70,  'grass',    'poison'],
    'tentacool':    [40,  40,  35,  100, 70,  'water',    'poison'],
    'tentacruel':   [80,  70,  65,  120, 100, 'water',    'poison'],
    'geodude':      [40,  80,  100, 30,  20,  'rock',     'ground'],
    'graveler':     [55,  95,  115, 45,  35,  'rock',     'ground'],
    'golem':        [80,  110, 130, 55,  45,  'rock',     'ground'],
    'ponyta':       [50,  85,  55,  65,  90,  'fire',     None],
    'rapidash':     [65,  100, 70,  80,  105, 'fire',     None],
    'slowpoke':     [90,  65,  65,  40,  15,  'water',    'psychic'],
    'slowbro':      [95,  75,  110, 80,  30,  'water',    'psychic'],
    'magnemite':    [25,  35,  70,  95,  45,  'electric', None],
    'magneton':     [50,  60,  95,  120, 70,  'electric', None],
    "farfetchd":    [52,  65,  55,  58,  60,  'normal',   'flying'],
    'doduo':        [35,  85,  45,  35,  75,  'normal',   'flying'],
    'dodrio':       [60,  110, 70,  60,  110, 'normal',   'flying'],
    'seel':         [65,  45,  55,  45,  45,  'water',    None],
    'dewgong':      [90,  70,  80,  95,  70,  'water',    'ice'],
    'grimer':       [80,  80,  50,  40,  25,  'poison',   None],
    'muk':          [105, 105, 75,  65,  50,  'poison',   None],
    'shellder':     [30,  65,  100, 45,  40,  'water',    None],
    'cloyster':     [50,  95,  180, 85,  70,  'water',    'ice'],
    'gastly':       [30,  35,  30,  100, 80,  'ghost',    'poison'],
    'haunter':      [45,  50,  45,  115, 95,  'ghost',    'poison'],
    'gengar':       [60,  65,  60,  130, 110, 'ghost',    'poison'],
    'onix':         [35,  45,  160, 30,  70,  'rock',     'ground'],
    'drowzee':      [60,  48,  45,  90,  42,  'psychic',  None],
    'hypno':        [85,  73,  70,  115, 67,  'psychic',  None],
    'krabby':       [30,  105, 90,  25,  50,  'water',    None],
    'kingler':      [55,  130, 115, 50,  75,  'water',    None],
    'voltorb':      [40,  30,  50,  55,  100, 'electric', None],
    'electrode':    [60,  50,  70,  80,  140, 'electric', None],
    'exeggcute':    [60,  40,  80,  60,  40,  'grass',    'psychic'],
    'exeggutor':    [95,  95,  85,  125, 55,  'grass',    'psychic'],
    'cubone':       [50,  50,  95,  40,  35,  'ground',   None],
    'marowak':      [60,  80,  110, 50,  45,  'ground',   None],
    'hitmonlee':    [50,  120, 53,  35,  87,  'fighting', None],
    'hitmonchan':   [50,  105, 79,  35,  76,  'fighting', None],
    'lickitung':    [90,  55,  75,  60,  30,  'normal',   None],
    'koffing':      [40,  65,  95,  60,  35,  'poison',   None],
    'weezing':      [65,  90,  120, 85,  60,  'poison',   None],
    'rhyhorn':      [80,  85,  95,  30,  25,  'ground',   'rock'],
    'rhydon':       [105, 130, 120, 45,  40,  'ground',   'rock'],
    'chansey':      [250, 5,   5,   105, 50,  'normal',   None],
    'tangela':      [65,  55,  115, 100, 60,  'grass',    None],
    'kangaskhan':   [105, 95,  80,  40,  90,  'normal',   None],
    'horsea':       [30,  40,  70,  70,  60,  'water',    None],
    'seadra':       [55,  65,  95,  95,  85,  'water',    None],
    'goldeen':      [45,  67,  60,  50,  63,  'water',    None],
    'seaking':      [80,  92,  65,  80,  68,  'water',    None],
    'staryu':       [30,  45,  55,  70,  85,  'water',    None],
    'starmie':      [60,  75,  85,  100, 115, 'water',    'psychic'],
    'mrmime':       [40,  45,  65,  100, 90,  'psychic',  None],
    'scyther':      [70,  110, 80,  55,  105, 'bug',      'flying'],
    'jynx':         [65,  50,  35,  95,  95,  'ice',      'psychic'],
    'electabuzz':   [65,  83,  57,  95,  105, 'electric', None],
    'magmar':       [65,  95,  57,  100, 93,  'fire',     None],
    'pinsir':       [65,  125, 100, 55,  85,  'bug',      None],
    'tauros':       [75,  100, 95,  70,  110, 'normal',   None],
    'magikarp':     [20,  10,  55,  20,  80,  'water',    None],
    'gyarados':     [95,  125, 79,  100, 81,  'water',    'flying'],
    'lapras':       [130, 85,  80,  95,  60,  'water',    'ice'],
    'ditto':        [48,  48,  48,  48,  48,  'normal',   None],
    'eevee':        [55,  55,  50,  45,  55,  'normal',   None],
    'vaporeon':     [130, 65,  60,  110, 65,  'water',    None],
    'jolteon':      [65,  65,  60,  110, 130, 'electric', None],
    'flareon':      [65,  130, 60,  110, 65,  'fire',     None],
    'porygon':      [65,  60,  70,  75,  40,  'normal',   None],
    'omanyte':      [35,  40,  100, 90,  35,  'rock',     'water'],
    'omastar':      [70,  60,  125, 115, 55,  'rock',     'water'],
    'kabuto':       [30,  80,  90,  45,  55,  'rock',     'water'],
    'kabutops':     [60,  115, 105, 70,  80,  'rock',     'water'],
    'aerodactyl':   [80,  105, 65,  60,  130, 'rock',     'flying'],
    'snorlax':      [160, 110, 65,  65,  30,  'normal',   None],
    'articuno':     [90,  85,  100, 125, 85,  'ice',      'flying'],
    'zapdos':       [90,  90,  85,  125, 100, 'electric', 'flying'],
    'moltres':      [90,  100, 90,  125, 90,  'fire',     'flying'],
    'dratini':      [41,  64,  45,  50,  50,  'dragon',   None],
    'dragonair':    [61,  84,  65,  70,  70,  'dragon',   None],
    'dragonite':    [91,  134, 95,  100, 80,  'dragon',   'flying'],
    'mewtwo':       [106, 110, 90,  154, 130, 'psychic',  None],
    'mew':          [100, 100, 100, 100, 100, 'psychic',  None],
}


# =============================================================================
# CONVENIENCE ACCESSORS
# =============================================================================

def get_stats(species: str, level: int = 100, dv: int = 15, stat_exp: int = 65535):
    """
    Return (HP, Atk, Def, Spc, Spe) calculated at the given level/DV/StatExp.

    Defaults to Level 100, max DVs (15), max Stat EXP (65535) — the Gen 1 OU
    standard used for damage calculations.

    Gen 1 formulas
    --------------
    stat_exp_bonus = floor(min(255, ceil(sqrt(stat_exp))) / 4)

    Non-HP:  floor(((Base + DV) * 2 + stat_exp_bonus) * level / 100) + 5
    HP:      floor(((Base + DV) * 2 + stat_exp_bonus) * level / 100) + level + 10
    """
    import math
    row = POKEMON.get(species.lower())
    if row is None:
        return None

    stat_exp_bonus = math.floor(min(255, math.ceil(math.sqrt(stat_exp))) / 4)

    def calc(base, is_hp=False):
        val = math.floor(((base + dv) * 2 + stat_exp_bonus) * level / 100)
        return val + (level + 10 if is_hp else 5)

    hp,  atk, dfn, spc, spe = row[0], row[1], row[2], row[3], row[4]
    return (
        calc(hp,  is_hp=True),
        calc(atk),
        calc(dfn),
        calc(spc),
        calc(spe),
    )


def get_types(species: str):
    """
    Return a list of type strings for a species, e.g. ['water', 'ice'].
    Always returns at least one element; dual-type Pokémon return two.
    """
    row = POKEMON.get(species.lower())
    if row is None:
        return ['normal']
    t1, t2 = row[5], row[6]
    return [t1, t2] if t2 else [t1]


# =============================================================================
# TYPE SYSTEM
# No Dark, Steel, or Fairy in Gen 1 — 15 types total.
# Ghost → Psychic = 0x (RBY bug: Ghost has NO effect on Psychic).
# Poison is super-effective against Bug (and vice versa).
# =============================================================================

TYPES = [
    'normal', 'fire', 'water', 'electric', 'grass', 'ice', 'fighting',
    'poison', 'ground', 'flying', 'psychic', 'bug', 'rock', 'ghost', 'dragon'
]

# type_chart[attacking_type][defending_type] = multiplier
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


def type_effectiveness(move_type: str, defender_types: list) -> float:
    """
    Combined type effectiveness multiplier for a move against a defender.

    Args:
        move_type:      e.g. 'fire'
        defender_types: 1–2 type strings, e.g. ['water', 'ice']

    Returns float: 0, 0.25, 0.5, 1, 2, or 4
    """
    chart = TYPE_CHART.get(move_type.lower(), {})
    mult = 1.0
    for t in defender_types:
        if t:
            mult *= chart.get(t.lower(), 1.0)
    return mult


# =============================================================================
# DEFENSIVE ANALYSIS
# All functions operate on type strings — no poke-env objects.
# Used by team_generator and matchup evaluator.
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
    """Dict of attacking types that deal super-effective damage to this combination."""
    w = get_weaknesses(type1, type2)
    return {TYPES[i]: w[i] for i in range(len(TYPES)) if w[i] > 1}


def get_immunities(type1: str, type2: str = None) -> list:
    """List of attacking types this combination is immune to."""
    w = get_weaknesses(type1, type2)
    return [TYPES[i] for i in range(len(TYPES)) if w[i] == 0]


def get_resistances(type1: str, type2: str = None) -> list:
    """List of attacking types this combination resists."""
    w = get_weaknesses(type1, type2)
    return [TYPES[i] for i in range(len(TYPES)) if 0 < w[i] < 1]


# =============================================================================
# MOVE TABLE
#
# Format: 'move_id': (BP, type, (min_hits, max_hits))
#
# Category is derived from type — see get_move_category().
# Exceptions: FIXED_DAMAGE_MOVES and OHKO_MOVES (keyed by move_id).
#
# Multi-hit moves: Pin Missile (2-5 hits), Barrage (2-5), Fury Attack (2-5),
#   Fury Swipes (2-5), Spike Cannon (2-5), Double Slap (2-5), Comet Punch (2-5),
#   Double Kick (2 hits fixed), Twineedle (2 hits fixed).
#
# Trapping moves (Wrap, Bind, Clamp, Fire Spin) hit once per turn for 2-5 turns;
#   stored as (1,1) — the engine handles the multi-turn loop.
# Thrash/Petal Dance lock the user for 2-3 turns; also stored as (1,1).
# =============================================================================

MOVES = { # https://pokemondb.net/move/generation/1
    # ── Normal (physical) ────────────────────────────────────────────────────
    'barrage':       (15,  'normal',   (2, 5)),   # Hits 2-5 times in one turn.
    'bide':          (0,   'normal',   (1, 1)),   # User takes damage for two turns then strikes back double.
    'bind':          (15,  'normal',   (1, 1)),   # Traps opponent, damaging them for 4-5 turns.
    'bite':          (60,  'normal',   (1, 1)),   # May cause flinching.
    'bodyslam':      (85,  'normal',   (1, 1)),   # May paralyze opponent.
    'cometpunch':    (18,  'normal',   (2, 5)),   # Hits 2-5 times in one turn.
    'constrict':     (10,  'normal',   (1, 1)),   # May lower opponent's Speed by one stage.
    'conversion':    (0,   'normal',   (1, 1)),   # Changes user's type to that of its first move.
    'cut':           (50,  'normal',   (1, 1)),
    'defensecurl':   (0,   'normal',   (1, 1)),   # Raises user's Defense.
    'disable':       (0,   'normal',   (1, 1)),   # Opponent can't use its last attack for a few turns.
    'dizzypunch':    (70,  'normal',   (1, 1)),   # May confuse opponent.
    'doubleslap':    (15,  'normal',   (2, 5)),   # Hits 2-5 times in one turn.
    'doubleteam':    (0,   'normal',   (1, 1)),   # Raises user's Evasiveness.
    'doubleedge':    (100, 'normal',   (1, 1)),   # User receives recoil damage.
    'eggbomb':       (100, 'normal',   (1, 1)),
    'explosion':     (170, 'normal',   (1, 1)),   # User faints.
    'flash':         (0,   'normal',   (1, 1)),   # Lowers opponent's Accuracy.
    'focusenergy':   (0,   'normal',   (1, 1)),   # Increases critical hit ratio.
    'furyswipes':    (18,  'normal',   (2, 5)),   # Hits 2-5 times in one turn.
    'furyattack':    (15,  'normal',   (2, 5)),   # Hits 2-5 times in one turn.
    'glare':         (0,   'normal',   (1, 1)),   # Paralyzes opponent.
    'growl':         (0,   'normal',   (1, 1)),   # Lowers opponent's Attack.
    'growth':        (0,   'normal',   (1, 1)),   # Raises user's Attack and Special Attack.
    'guillotine':    (1,   'normal',   (1, 1)),   # One-Hit-KO, if it hits.
    'harden':        (0,   'normal',   (1, 1)),   # Raises user's Defense.
    'headbutt':      (70,  'normal',   (1, 1)),   # May cause flinching.
    'hornattack':    (65,  'normal',   (1, 1)),
    'horndrill':     (1,   'normal',   (1, 1)),   # One-Hit-KO, if it hits.
    'hyperbeam':     (150, 'normal',   (1, 1)),   # User must recharge next turn.
    'hyperfang':     (80,  'normal',   (1, 1)),   # May cause flinching.
    'leer':          (0,   'normal',   (1, 1)),   # Lowers opponent's Defense.
    'lovelykiss':    (0,   'normal',   (1, 1)),   # Puts opponent to sleep.
    'megakick':      (120, 'normal',   (1, 1)),
    'megapunch':     (80,  'normal',   (1, 1)),
    'metronome':     (0,   'normal',   (1, 1)),   # User performs almost any move in the game at random.
    'mimic':         (0,   'normal',   (1, 1)),   # Copies the opponent's last move.
    'minimize':      (0,   'normal',   (1, 1)),   # Sharply raises user's Evasiveness.
    'payday':        (40,  'normal',   (1, 1)),   # Money is earned after the battle.
    'pound':         (40,  'normal',   (1, 1)),
    'quickattack':   (40,  'normal',   (1, 1)),   # User attacks first.
    'rage':          (20,  'normal',   (1, 1)),   # Raises user's Attack when hit.
    'razorwind':     (80,  'normal',   (1, 1)),   # Charges on first turn, attacks on second. High critical hit ratio.
    'recover':       (0,   'normal',   (1, 1)),   # User recovers half its max HP.
    'roar':          (0,   'normal',   (1, 1)),   # In battles, the opponent switches. In the wild, the Pokémon runs.
    'scratch':       (40,  'normal',   (1, 1)),
    'screech':       (0,   'normal',   (1, 1)),   # Sharply lowers opponent's Defense.
    'selfdestruct':  (130, 'normal',   (1, 1)),   # User faints.
    'sharpen':       (0,   'normal',   (1, 1)),   # Raises user's Attack.
    'sing':          (0,   'normal',   (1, 1)),   # Puts opponent to sleep.
    'skullbash':     (100, 'normal',   (1, 1)),   # Raises Defense on first turn, attacks on second.
    'slam':          (80,  'normal',   (1, 1)),
    'slash':         (70,  'normal',   (1, 1)),   # High critical hit ratio.
    'smokescreen':   (0,   'normal',   (1, 1)),   # Lowers opponent's Accuracy.
    'softboiled':    (0,   'normal',   (1, 1)),   # User recovers half its max HP.
    'sonicboom':     (1,   'normal',   (1, 1)),   # Always inflicts 20 HP.
    'spikecannon':   (20,  'normal',   (2, 5)),   # Hits 2-5 times in one turn.
    'splash':        (0,   'normal',   (1, 1)),   # Doesn't do ANYTHING.
    'stomp':         (65,  'normal',   (1, 1)),   # May cause flinching.
    'strength':      (80,  'normal',   (1, 1)),
    'struggle':      (50,  'normal',   (1, 1)),   # Only usable when all PP are gone. Hurts the user.
    'substitute':    (0,   'normal',   (1, 1)),   # Uses HP to creates a decoy that takes hits.
    'superfang':     (1,   'normal',   (1, 1)),   # Always takes off half of the opponent's HP.
    'supersonic':    (0,   'normal',   (1, 1)),   # Confuses opponent.
    'swift':         (60,  'normal',   (1, 1)),   # Ignores Accuracy and Evasiveness.
    'swordsdance':   (0,   'normal',   (1, 1)),   # Sharply raises user's Attack.
    'tackle':        (35,  'normal',   (1, 1)),
    'tailwhip':      (0,   'normal',   (1, 1)),   # Lowers opponent's Defense.
    'takedown':      (90,  'normal',   (1, 1)),   # User receives recoil damage.
    'thrash':        (90,  'normal',   (1, 1)),   # User attacks for 2-3 turns but then becomes confused.
    'transform':     (0,   'normal',   (1, 1)),   # User takes on the form and attacks of the opponent.
    'triattack':     (80,  'normal',   (1, 1)),   # May paralyze, burn or freeze opponent.
    'visegrip':      (55,  'normal',   (1, 1)),
    'whirlwind':     (0,   'normal',   (1, 1)),   # In battles, the opponent switches. In the wild, the Pokémon runs.
    'wrap':          (15,  'normal',   (1, 1)),   # Traps opponent, damaging them for 4-5 turns.
    
    # ── Normal (physical) — protocol / engine-only ───────────────────────────
    'recharge':      (0,   'normal',   (1, 1)),   # forced recharge turn (Hyper Beam)

    # ── Fire (special) ───────────────────────────────────────────────────────
    'ember':         (40,  'fire',     (1, 1)),   # May burn opponent.
    'fireblast':     (120, 'fire',     (1, 1)),   # May burn opponent.
    'firepunch':     (75,  'fire',     (1, 1)),   # May burn opponent.
    'firespin':      (15,  'fire',     (1, 1)),   # Traps opponent, damaging them for 4-5 turns.
    'flamethrower':  (95,  'fire',     (1, 1)),   # May burn opponent.

    # ── Water (special) ──────────────────────────────────────────────────────
    'bubble':        (20,  'water',    (1, 1)),   # May lower opponent's Speed.
    'bubblebeam':    (65,  'water',    (1, 1)),   # May lower opponent's Speed.
    'clamp':         (35,  'water',    (1, 1)),   # Traps opponent, damaging them for 4-5 turns.
    'crabhammer':    (90,  'water',    (1, 1)),   # High critical hit ratio.
    'hydropump':     (120, 'water',    (1, 1)),
    'surf':          (95,  'water',    (1, 1)),   # Hits all adjacent Pokémon.
    'watergun':      (40,  'water',    (1, 1)),
    'waterfall':     (80,  'water',    (1, 1)),   # May cause flinching.
    'withdraw':      (0,   'water',    (1, 1)),   # Raises user's Defense.

    # ── Electric (special) ───────────────────────────────────────────────────
    'thunder':       (120, 'electric', (1, 1)),   # May paralyze opponent.
    'thunderpunch':  (75,  'electric', (1, 1)),   # May paralyze opponent.
    'thundershock':  (40,  'electric', (1, 1)),   # May paralyze opponent.
    'thunderwave':   (0,   'electric', (1, 1)),   # Paralyzes opponent.
    'thunderbolt':   (95,  'electric', (1, 1)),   # May paralyze opponent.

    # ── Grass (special) ──────────────────────────────────────────────────────
    'absorb':        (20,  'grass',    (1, 1)),   # User recovers half the HP inflicted on opponent.
    'leechseed':     (0,   'grass',    (1, 1)),   # Drains HP from opponent each turn.
    'megadrain':     (40,  'grass',    (1, 1)),   # User recovers half the HP inflicted on opponent.
    'petaldance':    (70,  'grass',    (1, 1)),   # User attacks for 2-3 turns but then becomes confused.
    'razorleaf':     (55,  'grass',    (1, 1)),   # High critical hit ratio.
    'sleeppowder':   (0,   'grass',    (1, 1)),   # Puts opponent to sleep.
    'solarbeam':     (120, 'grass',    (1, 1)),   # Charges on first turn, attacks on second.
    'spore':         (0,   'grass',    (1, 1)),   # Puts opponent to sleep.
    'stunspore':     (0,   'grass',    (1, 1)),   # Paralyzes opponent. 
    'vinewhip':      (35,  'grass',    (1, 1)),

    # ── Ice (special) ────────────────────────────────────────────────────────
    'aurorabeam':    (65,  'ice',      (1, 1)),   # May lower opponent's Attack.
    'blizzard':      (120, 'ice',      (1, 1)),   # May freeze opponent.
    'haze':          (0,   'ice',      (1, 1)),   # Resets all stat changes.
    'icebeam':       (95,  'ice',      (1, 1)),   # May freeze opponent.
    'icepunch':      (75,  'ice',      (1, 1)),   # May freeze opponent.
    'mist':          (0,   'ice',      (1, 1)),   # User's stats cannot be changed for a period of time.

    # ── Fighting (physical) ──────────────────────────────────────────────────
    'counter':       (1,   'fighting', (1, 1)),   # When hit by a Physical Attack, user strikes back with 2x power.
    'doublekick':    (30,  'fighting', (2, 2)),   # Hits twice in one turn.
    'highjumpkick':  (85,  'fighting', (1, 1)),   # If it misses, the user loses half their HP.
    'jumpkick':      (70,  'fighting', (1, 1)),   # If it misses, the user loses half their HP.
    'karatechop':    (50,  'normal',   (1, 1)),   # High critical hit ratio.
    'lowkick':       (50,  'fighting', (1, 1)),   # The heavier the opponent, the stronger the attack.
    'rollingkick':   (60,  'fighting', (1, 1)),   # May cause flinching.
    'seismictoss':   (1,   'fighting', (1, 1)),   # Inflicts damage equal to user's level.
    'submission':    (80,  'fighting', (1, 1)),   # User receives recoil damage.

    # ── Poison (physical) ────────────────────────────────────────────────────
    'acid':          (40,  'poison',   (1, 1)),   # May lower opponent's Special Defense.
    'acidarmor':     (0,   'poison',   (1, 1)),   # Sharply raises user's Defense.
    'poisongas':     (0,   'poison',   (1, 1)),   # Poisons opponent.
    'poisonpowder':  (0,   'poison',   (1, 1)),   # Poisons opponent.
    'poisonsting':   (15,  'poison',   (1, 1)),   # May poison the opponent.
    'sludge':        (65,  'poison',   (1, 1)),   # May poison opponent.
    'smog':          (20,  'poison',   (1, 1)),   # May poison opponent.
    'toxic':         (0,   'poison',   (1, 1)),   # Badly poisons opponent.

    # ── Ground (physical) ────────────────────────────────────────────────────
    'boneclub':      (65,  'ground',   (1, 1)),   # May cause flinching.
    'bonemerang':    (50,  'ground',   (2, 2)),   # Hits twice in one turn.
    'dig':           (100, 'ground',   (1, 1)),   # Digs underground on first turn, attacks on second. Can also escape from caves.
    'earthquake':    (100, 'ground',   (1, 1)),   # Power is doubled if opponent is underground from using Dig.
    'fissure':       (1,   'ground',   (1, 1)),   # One-Hit-KO, if it hits.
    'sandattack':    (0,   'normal',   (1, 1)),   # Lowers opponent's Accuracy.

    # ── Flying (physical) ────────────────────────────────────────────────────
    'drillpeck':     (80,  'flying',   (1, 1)),
    'fly':           (70,  'flying',   (1, 1)),   # Flies up on first turn, attacks on second turn.
    'gust':          (40,  'normal',   (1, 1)),   # Hits Pokémon using Fly/Bounce/Sky Drop with double power.
    'mirrormove':    (0,   'flying',   (1, 1)),   # User performs the opponent's last move.
    'peck':          (35,  'flying',   (1, 1)),
    'skyattack':     (140, 'flying',   (1, 1)),   # Charges on first turn, attacks on second. May cause flinching. High critical hit ratio.
    'wingattack':    (35,  'flying',   (1, 1)),

    # ── Psychic (special) ────────────────────────────────────────────────────
    'agility':       (0,   'psychic',  (1, 1)),   # Sharply raises user's Speed.
    'amnesia':       (0,   'psychic',  (1, 1)),   # Sharply raises user's Special Defense.
    'barrier':       (0,   'psychic',  (1, 1)),   # Sharply raises user's Defense.
    'confusion':     (50,  'psychic',  (1, 1)),   # May confuse opponent.
    'dreameater':    (100, 'psychic',  (1, 1)),   # User recovers half the HP inflicted on a sleeping opponent.
    'hypnosis':      (0,   'psychic',  (1, 1)),   # Puts opponent to sleep.
    'kinesis':       (0,   'psychic',  (1, 1)),   # Lowers opponent's Accuracy.
    'lightscreen':   (0,   'psychic',  (1, 1)),   # Halves damage from Special attacks for 5 turns.
    'meditate':      (0,   'psychic',  (1, 1)),   # Raises user's Attack.
    'psybeam':       (65,  'psychic',  (1, 1)),   # May confuse opponent.
    'psychic':       (90,  'psychic',  (1, 1)),   # May lower opponent's Special Defense.
    'psywave':       (1,   'psychic',  (1, 1)),   # Inflicts damage 50-150% of user's level.
    'reflect':       (0,   'psychic',  (1, 1)),   # Halves damage from Physical attacks for 5 turns.
    'rest':          (0,   'psychic',  (1, 1)),   # User sleeps for 2 turns, but user is fully healed.
    'teleport':      (0,   'psychic',  (1, 1)),   # Allows user to flee wild battles; also warps player to last PokéCenter.

    # ── Bug (physical) ───────────────────────────────────────────────────────
    'leechlife':     (20,  'bug',      (1, 1)),   # User recovers half the HP inflicted on opponent.
    'pinmissile':    (14,  'bug',      (2, 5)),   # Hits 2-5 times in one turn.
    'stringshot':    (0,   'bug',      (1, 1)),   # Sharply lowers opponent's Speed.
    'twineedle':     (25,  'bug',      (2, 2)),   # Hits twice in one turn. May poison opponent.

    # ── Rock (physical) ──────────────────────────────────────────────────────
    'rockslide':     (75,  'rock',     (1, 1)),   # May cause flinching.
    'rockthrow':     (50,  'rock',     (1, 1)),

    # ── Ghost (physical) ─────────────────────────────────────────────────────
    'confuseray':    (0,   'ghost',    (1, 1)),   # Confuses opponent.
    'lick':          (20,  'ghost',    (1, 1)),   # May paralyze opponent.
    'nightshade':    (0,   'ghost',    (1, 1)),   # Inflicts damage equal to user's level.

    # ── Dragon (special) ─────────────────────────────────────────────────────
    'dragonrage':    (1,   'dragon',   (1, 1)),   # Always inflicts 40 HP.

}


# =============================================================================
# SPECIAL TYPES — drives physical/special split in Gen 1 damage calc
# In Gen 1 the category is TYPE-based, not move-based.
# These types use the Special stat for both offense and defense.
# =============================================================================

SPECIAL_TYPES = frozenset({'fire', 'water', 'electric', 'grass', 'ice', 'psychic', 'dragon'})


# =============================================================================
# MOVE CATEGORY SETS — used by competitive_player / gen1_engine fast-paths
# =============================================================================

# Moves that deal fixed damage (ignore type chart and stats entirely)
# sonicboom=20, dragonrage=40, nightshade/seismictoss/psywave = level-based
FIXED_DAMAGE_MOVES = frozenset({'seismictoss', 'nightshade', 'psywave', 'sonicboom', 'dragonrage'})

# One-hit KO moves
OHKO_MOVES = frozenset({'guillotine', 'horndrill', 'fissure'})

# Sleep-inducing moves
SLEEP_MOVES = frozenset({'hypnosis', 'sleeppowder', 'spore', 'lovelykiss', 'sing'})

# Ice moves that can freeze in Gen 1 (all damaging Ice-type moves, 10% chance)
FREEZE_MOVES = frozenset({'blizzard', 'icebeam', 'icepunch'})

# Moves the bot should never auto-pick — always defer to LLM
LLM_ONLY_MOVES = frozenset({'explosion', 'selfdestruct', 'counter'})

# Protocol artifacts / forced turns — ignore in move selection
IGNORE_MOVES = frozenset({'recharge', 'struggle', 'splash'})

# Trapping / partial-trapping moves (lock opponent in for 2-5 turns in Gen 1)
TRAPPING_MOVES = frozenset({'wrap', 'bind', 'clamp', 'firespin'})


# =============================================================================
# CONVENIENCE ACCESSORS
# =============================================================================

def get_move_category(move_id: str) -> str:
    """
    Derive the Gen 1 category for a move from its type, with explicit
    exceptions for status, fixed-damage, and OHKO moves.

    Returns one of: 'physical', 'special', 'status', 'fixed', 'ohko'.

    Gen 1 physical/special split is purely type-based:
      special  — Fire / Water / Electric / Grass / Ice / Psychic / Dragon
      physical — Normal / Fighting / Rock / Ground / Flying / Bug / Ghost / Poison
    """
    key = move_id.lower().replace(' ', '').replace('-', '')
    if key in OHKO_MOVES:
        return 'ohko'
    if key in FIXED_DAMAGE_MOVES:
        return 'fixed'
    m = MOVES.get(key)
    if m is None:
        return 'status'
    bp, move_type, _hits = m
    if bp == 0:
        return 'status'
    return 'special' if move_type in SPECIAL_TYPES else 'physical'


def get_move(move_id: str):
    """
    Return (BP, type, category, (min_hits, max_hits)) or None if unknown.

    Category is derived via get_move_category() — it is not stored in MOVES.
    """
    key = move_id.lower().replace(' ', '').replace('-', '')
    m = MOVES.get(key)
    if m is None:
        return None
    bp, move_type, hits = m
    return (bp, move_type, get_move_category(key), hits)

# =============================================================================
# STAT STAGE MULTIPLIERS
# Gen 1 formula: multiplier = max(2, 2+s) / max(2, 2-s)
# =============================================================================

_STAGE_MULT = {
    -6: (2, 8), -5: (2, 7), -4: (2, 6),
    -3: (2, 5), -2: (2, 4), -1: (2, 3),
     0: (2, 2),
     1: (3, 2), 2: (4, 2),  3: (5, 2),
     4: (6, 2), 5: (7, 2),  6: (8, 2),
}

def apply_stage(stat: int, stage: int) -> int:
    """Apply a stat-stage modifier; result capped at 999 in Gen 1."""
    stage = max(-6, min(6, stage))
    num, den = _STAGE_MULT[stage]
    return min(999, max(1, stat * num // den))

# =============================================================================
# SELF-TEST
# =============================================================================

if __name__ == '__main__':
    print(f"Pokemon loaded:   {len(POKEMON)}")
    if len(POKEMON) == 151:
        print("Pokemon Count Correct")
    else:
        # Calculate the difference to show exactly how far off it is
        diff = len(POKEMON) - 151
        print(f"{m_type} is off by {diff} (Got {actual}, expected 151)")
    print()
    print(f"Moves loaded:   {len(MOVES)}")
    
    move_counts = {}
    for move_id in MOVES:
        m_type = get_move(move_id)[1]
        move_counts[m_type] = move_counts.get(m_type,0) + 1

    expected_counts = {
        'normal': 79,    # +1 from Recharge
        'fire': 5,
        'water': 9,
        'electric': 5,
        'grass': 10,
        'ice': 6,
        'fighting': 8,  # -1 from Karate Chop
        'poison': 8,
        'ground': 5,    # -1 from Sand Attack
        'flying': 6,    # -1 from Gust
        'psychic': 15,
        'bug': 4,
        'rock': 2,
        'ghost': 3,
        'dragon': 1,
        }
        
    for m_type, target in expected_counts.items():
        actual = move_counts.get(m_type, 0)
    
        if actual != target:
            # Calculate the difference to show exactly how far off it is
            diff = actual - target
            print(f"{m_type} is off by {diff} (Got {actual}, expected {target})")
    print()
    # Pokémon accessors
    for species in ['tauros', 'alakazam', 'chansey', 'starmie', 'snorlax']:
        print(f"  {species:12s}: stats={get_stats(species)}  types={get_types(species)}")
    print()
    
    for move_id in OHKO_MOVES:
        if get_move_category(move_id) != 'ohko':
            print(move_id)
    for move_id in SLEEP_MOVES:
        if get_move_category(move_id) != 'status':
            print(move_id)

    # Type chart sanity
    print("Ghost → Psychic (Gen 1 bug, should be 0x):",
          type_effectiveness('ghost', ['psychic']),
          '✓' if type_effectiveness('ghost', ['psychic']) == 0 else '✗')
    print("Bug → Poison (should be 2x):",
          type_effectiveness('bug', ['poison']),
          '✓' if type_effectiveness('bug', ['poison']) == 2 else '✗')
    print("Poison → Bug (should be 2x):",
          type_effectiveness('poison', ['bug']),
          '✓' if type_effectiveness('poison', ['bug']) == 2 else '✗')
    print("Ice → Fire (should be 1x):",
      type_effectiveness('ice', ['fire']),
      '✓' if type_effectiveness('ice', ['fire']) == 1 else '✗')


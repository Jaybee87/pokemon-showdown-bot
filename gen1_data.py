"""
gen1_data.py
============
Single source of truth for all Gen 1 Pokémon and move data.

Replaces the split STATS / POKEMON_TYPES tables in gen1_calc.py and
the scattered move dicts across gen1_calc.py / gen1_engine.py.

gen1_calc.py can now be archived once callers are updated to import from here.

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

MOVES = {
    # ── Normal (physical) ────────────────────────────────────────────────────
    'tackle':        (35,  'normal',   (1, 1)),
    'scratch':       (40,  'normal',   (1, 1)),
    'cut':           (50,  'normal',   (1, 1)),
    'pound':         (40,  'normal',   (1, 1)),
    'headbutt':      (70,  'normal',   (1, 1)),
    'strength':      (80,  'normal',   (1, 1)),
    'bodyslam':      (85,  'normal',   (1, 1)),
    'doubleedge':    (100, 'normal',   (1, 1)),
    'hyperbeam':     (150, 'normal',   (1, 1)),
    'megapunch':     (80,  'normal',   (1, 1)),
    'megakick':      (120, 'normal',   (1, 1)),
    'slash':         (70,  'normal',   (1, 1)),
    'stomp':         (65,  'normal',   (1, 1)),
    'hornattack':    (65,  'normal',   (1, 1)),
    'furyattack':    (15,  'normal',   (2, 5)),   # multi-hit
    'cometpunch':    (18,  'normal',   (2, 5)),   # multi-hit
    'doubleslap':    (15,  'normal',   (2, 5)),   # multi-hit
    'spikecannon':   (20,  'normal',   (2, 5)),   # multi-hit
    'furyswipes':    (18,  'normal',   (2, 5)),   # multi-hit
    'wrap':          (15,  'normal',   (1, 1)),   # trapping; single hit per turn
    'bind':          (15,  'normal',   (1, 1)),   # trapping; single hit per turn
    'rage':          (20,  'normal',   (1, 1)),
    'swift':         (60,  'normal',   (1, 1)),
    'bide':          (0,   'normal',   (1, 1)),   # returns 2x damage taken; BP=0, not status
    'explosion':     (170, 'normal',   (1, 1)),   # raw .asm BP; engine halves target Def
    'selfdestruct':  (130, 'normal',   (1, 1)),   # raw .asm BP; engine halves target Def
    'takedown':      (90,  'normal',   (1, 1)),
    'thrash':        (90,  'normal',   (1, 1)),   # locks 2-3 turns; single hit per turn
    'skullbash':     (100, 'normal',   (1, 1)),   # charges then hits
    'eggbomb':       (100, 'normal',   (1, 1)),
    'quickattack':   (40,  'normal',   (1, 1)),
    'triattack':     (80,  'normal',   (1, 1)),
    'superfang':     (1,   'normal',   (1, 1)),   # halves target HP; fixed-style, engine handles
    'softboiled':    (0,   'normal',   (1, 1)),
    'recover':       (0,   'normal',   (1, 1)),
    'substitute':    (0,   'normal',   (1, 1)),
    'swordsdance':   (0,   'normal',   (1, 1)),
    'growl':         (0,   'normal',   (1, 1)),
    'tailwhip':      (0,   'normal',   (1, 1)),
    'disable':       (0,   'normal',   (1, 1)),
    'mimic':         (0,   'normal',   (1, 1)),
    'screech':       (0,   'normal',   (1, 1)),
    'leer':          (0,   'normal',   (1, 1)),
    'sharpen':       (0,   'normal',   (1, 1)),
    'conversion':    (0,   'normal',   (1, 1)),
    'harden':        (0,   'normal',   (1, 1)),
    'minimize':      (0,   'normal',   (1, 1)),
    'metronome':     (0,   'normal',   (1, 1)),
    'supersonic':    (0,   'normal',   (1, 1)),
    'glare':         (0,   'normal',   (1, 1)),
    'lovelykiss':    (0,   'normal',   (1, 1)),
    'sing':          (0,   'normal',   (1, 1)),
    'splash':        (0,   'normal',   (1, 1)),
    'transform':     (0,   'normal',   (1, 1)),
    'whirlwind':     (0,   'normal',   (1, 1)),
    'smokescreen':   (0,   'normal',   (1, 1)),
    'sandattack':    (0,   'normal',   (1, 1)),   # Normal type in Gen 1 (not Ground)
    'focusenergy':   (0,   'normal',   (1, 1)),   # Normal type in Gen 1 (not Fighting)
    'doubleteam':    (0,   'normal',   (1, 1)),
    'defensecurl':   (0,   'normal',   (1, 1)),
    'flash':         (0,   'normal',   (1, 1)),
    'sonicboom':     (1,   'normal',   (1, 1)),   # fixed 20 damage; see FIXED_DAMAGE_MOVES
    'guillotine':    (1,   'normal',   (1, 1)),   # OHKO; see OHKO_MOVES
    'horndrill':     (1,   'normal',   (1, 1)),   # OHKO; see OHKO_MOVES
    'razorwind':     (80,  'normal',   (1, 1)),   # charges then hits
    'struggle':      (50,  'normal',   (1, 1)),   # recoil; used when no PP

    # ── Fire (special) ───────────────────────────────────────────────────────
    'ember':         (40,  'fire',     (1, 1)),
    'flamethrower':  (95,  'fire',     (1, 1)),
    'fireblast':     (120, 'fire',     (1, 1)),
    'firespin':      (15,  'fire',     (1, 1)),   # trapping; single hit per turn
    'firepunch':     (75,  'fire',     (1, 1)),

    # ── Water (special) ──────────────────────────────────────────────────────
    'watergun':      (40,  'water',    (1, 1)),
    'surf':          (95,  'water',    (1, 1)),
    'hydropump':     (120, 'water',    (1, 1)),
    'bubble':        (20,  'water',    (1, 1)),
    'bubblebeam':    (65,  'water',    (1, 1)),
    'clamp':         (35,  'water',    (1, 1)),   # trapping; single hit per turn
    'crabhammer':    (90,  'water',    (1, 1)),
    'waterfall':     (80,  'water',    (1, 1)),
    'withdraw':      (0,   'water',    (1, 1)),

    # ── Electric (special) ───────────────────────────────────────────────────
    'thundershock':  (40,  'electric', (1, 1)),
    'thunderbolt':   (95,  'electric', (1, 1)),
    'thunder':       (120, 'electric', (1, 1)),
    'thunderwave':   (0,   'electric', (1, 1)),
    'thunderpunch':  (75,  'electric', (1, 1)),

    # ── Grass (special) ──────────────────────────────────────────────────────
    'vinewhip':      (35,  'grass',    (1, 1)),
    'razorleaf':     (55,  'grass',    (1, 1)),
    'solarbeam':     (120, 'grass',    (1, 1)),   # charges then hits
    'megadrain':     (40,  'grass',    (1, 1)),
    'absorb':        (20,  'grass',    (1, 1)),
    'petaldance':    (70,  'grass',    (1, 1)),   # locks 2-3 turns; Grass type (not Normal)
    'sleeppowder':   (0,   'grass',    (1, 1)),
    'stunspore':     (0,   'grass',    (1, 1)),
    'leechseed':     (0,   'grass',    (1, 1)),
    'spore':         (0,   'grass',    (1, 1)),
    'growth':        (0,   'grass',    (1, 1)),

    # ── Ice (special) ────────────────────────────────────────────────────────
    'icebeam':       (95,  'ice',      (1, 1)),
    'blizzard':      (120, 'ice',      (1, 1)),
    'icepunch':      (75,  'ice',      (1, 1)),
    'aurorabeam':    (65,  'ice',      (1, 1)),
    'mist':          (0,   'ice',      (1, 1)),
    'haze':          (0,   'ice',      (1, 1)),

    # ── Fighting (physical) ──────────────────────────────────────────────────
    'karatechop':    (50,  'fighting', (1, 1)),
    'lowkick':       (50,  'fighting', (1, 1)),
    'doublekick':    (30,  'fighting', (2, 2)),   # always 2 hits
    'jumpkick':      (70,  'fighting', (1, 1)),
    'highjumpkick':  (85,  'fighting', (1, 1)),
    'rollingkick':   (60,  'fighting', (1, 1)),
    'submission':    (80,  'fighting', (1, 1)),
    'seismictoss':   (1,   'fighting', (1, 1)),   # fixed level-based; see FIXED_DAMAGE_MOVES
    'counter':       (1,   'fighting', (1, 1)),   # reflects physical damage; engine handles

    # ── Poison (physical) ────────────────────────────────────────────────────
    'poisonsting':   (15,  'poison',   (1, 1)),
    'sludge':        (65,  'poison',   (1, 1)),
    'smog':          (20,  'poison',   (1, 1)),
    'acid':          (40,  'poison',   (1, 1)),
    'toxic':         (0,   'poison',   (1, 1)),
    'poisongas':     (0,   'poison',   (1, 1)),
    'poisonpowder':  (0,   'poison',   (1, 1)),   # Poison type (not Grass)
    'acidarmor':     (0,   'poison',   (1, 1)),

    # ── Ground (physical) ────────────────────────────────────────────────────
    'earthquake':    (100, 'ground',   (1, 1)),
    'fissure':       (1,   'ground',   (1, 1)),   # OHKO; see OHKO_MOVES
    'dig':           (100, 'ground',   (1, 1)),   # charges then hits
    'bonemerang':    (50,  'ground',   (2, 2)),   # always 2 hits

    # ── Flying (physical) ────────────────────────────────────────────────────
    'gust':          (40,  'flying',   (1, 1)),
    'wingattack':    (35,  'flying',   (1, 1)),
    'drillpeck':     (80,  'flying',   (1, 1)),
    'peck':          (35,  'flying',   (1, 1)),
    'skyattack':     (140, 'flying',   (1, 1)),   # charges then hits
    'fly':           (70,  'flying',   (1, 1)),   # charges then hits
    'mirrormove':    (0,   'flying',   (1, 1)),

    # ── Psychic (special) ────────────────────────────────────────────────────
    'psybeam':       (65,  'psychic',  (1, 1)),
    'psychic':       (90,  'psychic',  (1, 1)),
    'confusion':     (50,  'psychic',  (1, 1)),
    'dreameater':    (100, 'psychic',  (1, 1)),
    'psywave':       (1,   'psychic',  (1, 1)),   # variable level-based; see FIXED_DAMAGE_MOVES
    'teleport':      (0,   'psychic',  (1, 1)),
    'hypnosis':      (0,   'psychic',  (1, 1)),
    'amnesia':       (0,   'psychic',  (1, 1)),
    'reflect':       (0,   'psychic',  (1, 1)),
    'lightscreen':   (0,   'psychic',  (1, 1)),
    'barrier':       (0,   'psychic',  (1, 1)),
    'agility':       (0,   'psychic',  (1, 1)),   # Psychic type in Gen 1
    'meditate':      (0,   'psychic',  (1, 1)),   # Psychic type in Gen 1 (not Fighting)
    'rest':          (0,   'psychic',  (1, 1)),
    'kinesis':       (0,   'psychic',  (1, 1)),

    # ── Bug (physical) ───────────────────────────────────────────────────────
    'leechlife':     (20,  'bug',      (1, 1)),
    'pinmissile':    (14,  'bug',      (2, 5)),   # multi-hit
    'twineedle':     (25,  'bug',      (2, 2)),   # always 2 hits; may poison
    'stringshot':    (0,   'bug',      (1, 1)),

    # ── Rock (physical) ──────────────────────────────────────────────────────
    'rockthrow':     (50,  'rock',     (1, 1)),
    'rockslide':     (75,  'rock',     (1, 1)),

    # ── Ghost (physical) ─────────────────────────────────────────────────────
    'lick':          (20,  'ghost',    (1, 1)),
    'nightshade':    (0,   'ghost',    (1, 1)),   # fixed level-based; see FIXED_DAMAGE_MOVES
    'confuseray':    (0,   'ghost',    (1, 1)),

    # ── Dragon (special) ─────────────────────────────────────────────────────
    'dragonrage':    (1,   'dragon',   (1, 1)),   # fixed 40 damage; see FIXED_DAMAGE_MOVES

    # ── Normal (physical) — protocol / engine-only ───────────────────────────
    'barrage':       (15,  'normal',   (2, 5)),   # multi-hit
    'payday':        (40,  'normal',   (1, 1)),
    'recharge':      (0,   'normal',   (1, 1)),   # forced recharge turn (Hyper Beam)
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


def get_move_type(move_id: str) -> str | None:
    """Return just the type string for a move, or None."""
    key = move_id.lower().replace(' ', '').replace('-', '')
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
# SELF-TEST
# =============================================================================

if __name__ == '__main__':
    print(f"Pokémon loaded: {len(POKEMON)}")
    print("All 151 Pokémon present?", len(POKEMON) == 151)
    print(f"Moves loaded:   {len(MOVES)}")
    print()

    # Spot-check a few Pokémon
    for species in ['tauros', 'alakazam', 'exeggutor', 'chansey', 'starmie', 'snorlax']:
        stats = get_stats(species)
        types = get_types(species)
        print(f"  {species:12s}: stats={stats}  types={types}")

    print()
    
    for move_id in ['flash', 'kinesis', 'sandattack', 'smokescreen']:
            print(move_id, get_move_category(move_id), get_move(move_id), get_move_type(move_id), is_damaging(move_id), average_hits(move_id))

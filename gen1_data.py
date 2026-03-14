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
    'move_id': (BP, type, category, hits)

hits = (min_hits, max_hits).  Single-hit moves use (1, 1).
Multi-hit moves like Pin Missile use (2, 5).
Status/fixed moves keep BP=0 and hits=(1,1).

Categories:
    'physical' — Normal/Fighting/Rock/Ground/Flying/Bug/Ghost/Poison
    'special'  — Fire/Water/Electric/Grass/Ice/Psychic/Dragon
    'status'   — no damage
    'fixed'    — fixed 100 damage (Seismic Toss, Night Shade, etc.)
    'ohko'     — one-hit KO (Guillotine, Horn Drill, Fissure)
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

    # --- Jigglypuff line ---
    'jigglypuff':   [115, 45,  20,  25,  20,  'normal',   None],
    'wigglytuff':   [140, 70,  45,  50,  45,  'normal',   None],

    # --- Zubat line ---
    'zubat':        [40,  45,  35,  40,  55,  'poison',   'flying'],
    'golbat':       [75,  80,  70,  75,  90,  'poison',   'flying'],

    # --- Oddish line ---
    'oddish':       [45,  50,  55,  75,  30,  'grass',    'poison'],
    'gloom':        [60,  65,  70,  85,  40,  'grass',    'poison'],
    'vileplume':    [75,  80,  85,  100, 50,  'grass',    'poison'],

    # --- Paras line ---
    'paras':        [35,  70,  55,  55,  25,  'bug',      'grass'],
    'parasect':     [60,  95,  80,  80,  30,  'bug',      'grass'],

    # --- Venonat line ---
    'venonat':      [60,  55,  50,  40,  45,  'bug',      'poison'],
    'venomoth':     [70,  65,  60,  90,  90,  'bug',      'poison'],

    # --- Diglett line ---
    'diglett':      [10,  55,  25,  45,  95,  'ground',   None],
    'dugtrio':      [35,  100, 50,  70,  120, 'ground',   None],

    # --- Meowth line ---
    'meowth':       [40,  45,  35,  40,  90,  'normal',   None],
    'persian':      [65,  70,  60,  65,  115, 'normal',   None],

    # --- Psyduck line ---
    'psyduck':      [50,  52,  48,  50,  55,  'water',    None],
    'golduck':      [80,  82,  78,  80,  85,  'water',    None],

    # --- Mankey line ---
    'mankey':       [40,  80,  35,  35,  70,  'fighting', None],
    'primeape':     [65,  105, 60,  60,  95,  'fighting', None],

    # --- Growlithe line ---
    'growlithe':    [55,  70,  45,  70,  60,  'fire',     None],
    'arcanine':     [90,  110, 80,  100, 95,  'fire',     None],

    # --- Poliwag line ---
    'poliwag':      [40,  50,  40,  40,  90,  'water',    None],
    'poliwhirl':    [65,  65,  65,  50,  90,  'water',    None],
    'poliwrath':    [90,  95,  95,  70,  70,  'water',    'fighting'],

    # --- Abra line ---
    'abra':         [25,  20,  15,  105, 90,  'psychic',  None],
    'kadabra':      [40,  35,  30,  120, 105, 'psychic',  None],
    'alakazam':     [55,  50,  45,  135, 120, 'psychic',  None],

    # --- Machop line ---
    'machop':       [70,  80,  50,  35,  35,  'fighting', None],
    'machoke':      [80,  100, 70,  50,  45,  'fighting', None],
    'machamp':      [90,  130, 80,  65,  55,  'fighting', None],

    # --- Bellsprout line ---
    'bellsprout':   [50,  75,  35,  70,  40,  'grass',    'poison'],
    'weepinbell':   [65,  90,  50,  85,  55,  'grass',    'poison'],
    'victreebel':   [80,  105, 65,  100, 70,  'grass',    'poison'],

    # --- Tentacool line ---
    'tentacool':    [40,  40,  35,  100, 70,  'water',    'poison'],
    'tentacruel':   [80,  70,  65,  120, 100, 'water',    'poison'],

    # --- Geodude line ---
    'geodude':      [40,  80,  100, 30,  20,  'rock',     'ground'],
    'graveler':     [55,  95,  115, 45,  35,  'rock',     'ground'],
    'golem':        [80,  110, 130, 55,  45,  'rock',     'ground'],

    # --- Ponyta line ---
    'ponyta':       [50,  85,  55,  65,  90,  'fire',     None],
    'rapidash':     [65,  100, 70,  80,  105, 'fire',     None],

    # --- Slowpoke line ---
    'slowpoke':     [90,  65,  65,  40,  15,  'water',    'psychic'],
    'slowbro':      [95,  75,  110, 80,  30,  'water',    'psychic'],

    # --- Magnemite line ---
    'magnemite':    [25,  35,  70,  95,  45,  'electric', None],
    'magneton':     [50,  60,  95,  120, 70,  'electric', None],

    # --- Farfetchd ---
    "farfetchd":    [52,  65,  55,  58,  60,  'normal',   'flying'],

    # --- Doduo line ---
    'doduo':        [35,  85,  45,  35,  75,  'normal',   'flying'],
    'dodrio':       [60,  110, 70,  60,  110, 'normal',   'flying'],

    # --- Seel line ---
    'seel':         [65,  45,  55,  45,  45,  'water',    None],
    'dewgong':      [90,  70,  80,  95,  70,  'water',    'ice'],

    # --- Grimer line ---
    'grimer':       [80,  80,  50,  40,  25,  'poison',   None],
    'muk':          [105, 105, 75,  65,  50,  'poison',   None],

    # --- Shellder line ---
    'shellder':     [30,  65,  100, 45,  40,  'water',    None],
    'cloyster':     [50,  95,  180, 85,  70,  'water',    'ice'],

    # --- Gastly line ---
    'gastly':       [30,  35,  30,  100, 80,  'ghost',    'poison'],
    'haunter':      [45,  50,  45,  115, 95,  'ghost',    'poison'],
    'gengar':       [60,  65,  60,  130, 110, 'ghost',    'poison'],

    # --- Onix ---
    'onix':         [35,  45,  160, 30,  70,  'rock',     'ground'],

    # --- Drowzee line ---
    'drowzee':      [60,  48,  45,  90,  42,  'psychic',  None],
    'hypno':        [85,  73,  70,  115, 67,  'psychic',  None],

    # --- Krabby line ---
    'krabby':       [30,  105, 90,  25,  50,  'water',    None],
    'kingler':      [55,  130, 115, 50,  75,  'water',    None],

    # --- Voltorb line ---
    'voltorb':      [40,  30,  50,  55,  100, 'electric', None],
    'electrode':    [60,  50,  70,  80,  140, 'electric', None],

    # --- Exeggcute line ---
    'exeggcute':    [60,  40,  80,  60,  40,  'grass',    'psychic'],
    'exeggutor':    [95,  95,  85,  125, 55,  'grass',    'psychic'],

    # --- Cubone line ---
    'cubone':       [50,  50,  95,  40,  35,  'ground',   None],
    'marowak':      [60,  80,  110, 50,  45,  'ground',   None],

    # --- Hitmon line ---
    'hitmonlee':    [50,  120, 53,  35,  87,  'fighting', None],
    'hitmonchan':   [50,  105, 79,  35,  76,  'fighting', None],

    # --- Lickitung ---
    'lickitung':    [90,  55,  75,  60,  30,  'normal',   None],

    # --- Koffing line ---
    'koffing':      [40,  65,  95,  60,  35,  'poison',   None],
    'weezing':      [65,  90,  120, 85,  60,  'poison',   None],

    # --- Rhyhorn line ---
    'rhyhorn':      [80,  85,  95,  30,  25,  'ground',   'rock'],
    'rhydon':       [105, 130, 120, 45,  40,  'ground',   'rock'],

    # --- Chansey ---
    'chansey':      [250, 5,   5,   105, 50,  'normal',   None],

    # --- Tangela ---
    'tangela':      [65,  55,  115, 100, 60,  'grass',    None],

    # --- Kangaskhan ---
    'kangaskhan':   [105, 95,  80,  40,  90,  'normal',   None],

    # --- Horsea line ---
    'horsea':       [30,  40,  70,  70,  60,  'water',    None],
    'seadra':       [55,  65,  95,  95,  85,  'water',    None],

    # --- Goldeen line ---
    'goldeen':      [45,  67,  60,  50,  63,  'water',    None],
    'seaking':      [80,  92,  65,  80,  68,  'water',    None],

    # --- Staryu line ---
    'staryu':       [30,  45,  55,  70,  85,  'water',    None],
    'starmie':      [60,  75,  85,  100, 115, 'water',    'psychic'],

    # --- Mr. Mime ---
    'mrmime':       [40,  45,  65,  100, 90,  'psychic',  None],

    # --- Scyther ---
    'scyther':      [70,  110, 80,  55,  105, 'bug',      'flying'],

    # --- Jynx ---
    'jynx':         [65,  50,  35,  95,  95,  'ice',      'psychic'],

    # --- Electabuzz ---
    'electabuzz':   [65,  83,  57,  95,  105, 'electric', None],

    # --- Magmar ---
    'magmar':       [65,  95,  57,  100, 93,  'fire',     None],

    # --- Pinsir ---
    'pinsir':       [65,  125, 100, 55,  85,  'bug',      None],

    # --- Tauros ---
    'tauros':       [75,  100, 95,  70,  110, 'normal',   None],

    # --- Magikarp line ---
    'magikarp':     [20,  10,  55,  20,  80,  'water',    None],
    'gyarados':     [95,  125, 79,  100, 81,  'water',    'flying'],

    # --- Lapras ---
    'lapras':       [130, 85,  80,  95,  60,  'water',    'ice'],

    # --- Ditto ---
    'ditto':        [48,  48,  48,  48,  48,  'normal',   None],

    # --- Eevee line ---
    'eevee':        [55,  55,  50,  45,  55,  'normal',   None],
    'vaporeon':     [130, 65,  60,  110, 65,  'water',    None],
    'jolteon':      [65,  65,  60,  110, 130, 'electric', None],
    'flareon':      [65,  130, 60,  110, 65,  'fire',     None],

    # --- Porygon ---
    'porygon':      [65,  60,  70,  75,  40,  'normal',   None],

    # --- Omanyte line ---
    'omanyte':      [35,  40,  100, 90,  35,  'rock',     'water'],
    'omastar':      [70,  60,  125, 115, 55,  'rock',     'water'],

    # --- Kabuto line ---
    'kabuto':       [30,  80,  90,  45,  55,  'rock',     'water'],
    'kabutops':     [60,  115, 105, 70,  80,  'rock',     'water'],

    # --- Aerodactyl ---
    'aerodactyl':   [80,  105, 65,  60,  130, 'rock',     'flying'],

    # --- Snorlax ---
    'snorlax':      [160, 110, 65,  65,  30,  'normal',   None],

    # --- Legendary Birds ---
    'articuno':     [90,  85,  100, 125, 85,  'ice',      'flying'],
    'zapdos':       [90,  90,  85,  125, 100, 'electric', 'flying'],
    'moltres':      [90,  100, 90,  125, 90,  'fire',     'flying'],

    # --- Dratini line ---
    'dratini':      [41,  64,  45,  50,  50,  'dragon',   None],
    'dragonair':    [61,  84,  65,  70,  70,  'dragon',   None],
    'dragonite':    [91,  134, 95,  100, 80,  'dragon',   'flying'],

    # --- Mewtwo / Mew ---
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
# Format: 'move_id': (BP, type, category, (min_hits, max_hits))
#
# Multi-hit moves: Pin Missile (2-5 hits), Barrage (2-5), Fury Attack (2-5),
#   Fury Swipes (2-5), Spike Cannon (2-5), Double Slap (2-5), Comet Punch (2-5),
#   Double Kick (2 hits fixed), Twineedle (2 hits fixed), Bone Rush (2-5 Gen 2+;
#   Gen 1 is 3-5 — treated as (3,5))
# =============================================================================

MOVES = {
    # ── Normal (physical) ────────────────────────────────────────────────────
    'tackle':        (35,  'normal',   'physical', (1, 1)),
    'scratch':       (40,  'normal',   'physical', (1, 1)),
    'cut':           (50,  'normal',   'physical', (1, 1)),
    'pound':         (40,  'normal',   'physical', (1, 1)),
    'headbutt':      (70,  'normal',   'physical', (1, 1)),
    'strength':      (80,  'normal',   'physical', (1, 1)),
    'bodyslam':      (85,  'normal',   'physical', (1, 1)),
    'doubleedge':    (100, 'normal',   'physical', (1, 1)),
    'hyperbeam':     (150, 'normal',   'physical', (1, 1)),
    'megapunch':     (80,  'normal',   'physical', (1, 1)),
    'megakick':      (120, 'normal',   'physical', (1, 1)),
    'slash':         (70,  'normal',   'physical', (1, 1)),
    'stomp':         (65,  'normal',   'physical', (1, 1)),
    'hornattack':    (65,  'normal',   'physical', (1, 1)),
    'furyattack':    (15,  'normal',   'physical', (2, 5)),   # multi-hit
    'cometpunch':    (18,  'normal',   'physical', (2, 5)),   # multi-hit
    'doubleslap':    (15,  'normal',   'physical', (2, 5)),   # multi-hit
    'spikecannon':   (20,  'normal',   'physical', (2, 5)),   # multi-hit
    'wrap':          (15,  'normal',   'physical', (2, 5)),   # trapping / multi-hit
    'bind':          (15,  'normal',   'physical', (2, 5)),   # trapping / multi-hit
    'rage':          (20,  'normal',   'physical', (1, 1)),
    'swift':         (60,  'normal',   'physical',  (1, 1)),   # Gen 1: special split
    'bide':          (0,   'normal',   'physical', (1, 1)),   # returns 2x damage taken
    'explosion':     (340, 'normal',   'physical', (1, 1)),   # halves target's Def
    'selfdestruct':  (260, 'normal',   'physical', (1, 1)),   # halves target's Def
    'takedown':      (90,  'normal',   'physical', (1, 1)),
    'thrash':        (90,  'normal',   'physical', (2, 3)),   # 2-3 turns
    'petaldance':    (70,  'normal',   'special',  (2, 3)),
    'skullbash':     (100, 'normal',   'physical', (1, 1)),   # charges then hits
    'eggbomb':       (100, 'normal',   'physical', (1, 1)),
    'softboiled':    (0,   'normal',   'status',   (1, 1)),
    'recover':       (0,   'normal',   'status',   (1, 1)),
    'substitute':    (0,   'normal',   'status',   (1, 1)),
    'swordsdance':   (0,   'normal',   'status',   (1, 1)),
    'growl':         (0,   'normal',   'status',   (1, 1)),
    'tailwhip':      (0,   'normal',   'status',   (1, 1)),
    'string shot':   (0,   'normal',   'status',   (1, 1)),
    'stringshot':    (0,   'normal',   'status',   (1, 1)),
    'disable':       (0,   'normal',   'status',   (1, 1)),
    'mimic':         (0,   'normal',   'status',   (1, 1)),
    'screech':       (0,   'normal',   'status',   (1, 1)),
    'leer':          (0,   'normal',   'status',   (1, 1)),
    'sharpen':       (0,   'normal',   'status',   (1, 1)),
    'conversion':    (0,   'normal',   'status',   (1, 1)),
    'harden':        (0,   'normal',   'status',   (1, 1)),
    'minimize':      (0,   'normal',   'status',   (1, 1)),
    'metronome':     (0,   'normal',   'status',   (1, 1)),
    'supersonic':    (0,   'normal',   'status',   (1, 1)),
    'glare':         (0,   'normal',   'status',   (1, 1)),
    'lovelykiss':    (0,   'normal',   'status',   (1, 1)),
    'sing':          (0,   'normal',   'status',   (1, 1)),
    'splash':        (0,   'normal',   'status',   (1, 1)),
    'transform':     (0,   'normal',   'status',   (1, 1)),
    'rest':          (0,   'psychic',  'status',   (1, 1)),   # restores HP + sleep
    'sonicboom':     (0,   'normal',   'fixed',    (1, 1)),

    # ── Fire (special) ───────────────────────────────────────────────────────
    'ember':         (40,  'fire',     'special',  (1, 1)),
    'flamethrower':  (95,  'fire',     'special',  (1, 1)),
    'fireblast':     (120, 'fire',     'special',  (1, 1)),
    'firespin':      (15,  'fire',     'special',  (2, 5)),   # trapping / multi-hit
    'firepunch':     (75,  'fire',     'special',  (1, 1)),

    # ── Water (special) ──────────────────────────────────────────────────────
    'watergun':      (40,  'water',    'special',  (1, 1)),
    'surf':          (95,  'water',    'special',  (1, 1)),
    'hydropump':     (120, 'water',    'special',  (1, 1)),
    'bubble':        (20,  'water',    'special',  (1, 1)),
    'bubblebeam':    (65,  'water',    'special',  (1, 1)),
    'clamp':         (35,  'water',    'special', (2, 5)),   # trapping; physical in Gen1
    'crabhammer':    (90,  'water',    'special', (1, 1)),   # physical in Gen1
    'waterfall':     (80,  'water',    'special', (1, 1)),   # physical in Gen1
    'withdraw':      (0,   'water',    'status',   (1, 1)),

    # ── Electric (special) ───────────────────────────────────────────────────
    'thundershock':  (40,  'electric', 'special',  (1, 1)),
    'thunderbolt':   (95,  'electric', 'special',  (1, 1)),
    'thunder':       (120, 'electric', 'special',  (1, 1)),
    'thunderwave':   (0,   'electric', 'status',   (1, 1)),
    'thunderpunch':  (75,  'electric', 'special',  (1, 1)),

    # ── Grass (special) ──────────────────────────────────────────────────────
    'vinewhip':      (35,  'grass',    'special',  (1, 1)),
    'razorleaf':     (55,  'grass',    'special',  (1, 1)),
    'solarbeam':     (120, 'grass',    'special',  (1, 1)),   # charges + fires
    'megadrain':     (40,  'grass',    'special',  (1, 1)),
    'absorb':        (20,  'grass',    'special',  (1, 1)),
    'sleeppowder':   (0,   'grass',    'status',   (1, 1)),
    'stunspore':     (0,   'grass',    'status',   (1, 1)),
    'poisonpowder':  (0,   'grass',    'status',   (1, 1)),
    'leechseed':     (0,   'grass',    'status',   (1, 1)),
    'spore':         (0,   'grass',    'status',   (1, 1)),

    # ── Ice (special) ────────────────────────────────────────────────────────
    'icebeam':       (95,  'ice',      'special',  (1, 1)),
    'blizzard':      (120, 'ice',      'special',  (1, 1)),
    'icepunch':      (75,  'ice',      'special',  (1, 1)),
    'mist':          (0,   'ice',      'status',   (1, 1)),

    # ── Fighting (physical) ──────────────────────────────────────────────────
    'karatechop':    (50,  'fighting', 'physical', (1, 1)),
    'lowkick':       (50,  'fighting', 'physical', (1, 1)),
    'doublekick':    (30,  'fighting', 'physical', (2, 2)),   # always 2 hits
    'jumpkick':      (70,  'fighting', 'physical', (1, 1)),
    'highjumpkick':  (85,  'fighting', 'physical', (1, 1)),
    'rollingkick':   (60,  'fighting', 'physical', (1, 1)),
    'submission':    (80,  'fighting', 'physical', (1, 1)),
    'seismictoss':   (0,   'fighting', 'fixed',    (1, 1)),   # level-based fixed
    'counter':       (0,   'fighting', 'status',   (1, 1)),   # reflects physical 2x
    'focusenergy':   (0,   'fighting', 'status',   (1, 1)),   # Gen1 bug: decreases crit
    'meditate':      (0,   'fighting', 'status',   (1, 1)),

    # ── Poison (physical) ────────────────────────────────────────────────────
    'poisonsting':   (15,  'poison',   'physical', (1, 1)),
    'twineedle':     (25,  'bug',      'physical', (2, 2)),   # Bug type, 2 fixed hits, poisons
    'sludge':        (65,  'poison',   'physical', (1, 1)),
    'smog':          (20,  'poison',   'special',  (1, 1)),   # special in Gen1
    'acid':          (40,  'poison',   'special',  (1, 1)),   # special in Gen1
    'toxic':         (0,   'poison',   'status',   (1, 1)),

    # ── Ground (physical) ────────────────────────────────────────────────────
    'sandattack':    (0,   'ground',   'status',   (1, 1)),
    'earthquake':    (100, 'ground',   'physical', (1, 1)),
    'fissure':       (0,   'ground',   'ohko',     (1, 1)),
    'dig':           (100, 'ground',   'physical', (1, 1)),   # charges + hits
    'bonemerang':    (50,  'ground',   'physical', (2, 2)),   # always 2 hits
    'bonerush':      (25,  'ground',   'physical', (3, 5)),   # 3-5 hits in Gen 1 (changed Gen2+)

    # ── Flying (physical) ────────────────────────────────────────────────────
    'gust':          (40,  'flying',   'physical', (1, 1)),
    'wingattack':    (35,  'flying',   'physical', (1, 1)),
    'drillpeck':     (80,  'flying',   'physical', (1, 1)),
    'peck':          (35,  'flying',   'physical', (1, 1)),
    'skyattack':     (140, 'flying',   'physical', (1, 1)),   # charges + hits
    'fly':           (70,  'flying',   'physical', (1, 1)),   # charges + hits
    'mirrormove':    (0,   'flying',   'status',   (1, 1)),
    'agility':       (0,   'psychic',  'status',   (1, 1)),   # actually Psychic type in Gen1

    # ── Psychic (special) ────────────────────────────────────────────────────
    'psybeam':       (65,  'psychic',  'special',  (1, 1)),
    'psychic':       (90,  'psychic',  'special',  (1, 1)),
    'psywave':       (0,   'psychic',  'fixed',    (1, 1)),   # 0.5-1.5x level damage
    'teleport':      (0,   'psychic',  'status',   (1, 1)),
    'hypnosis':      (0,   'psychic',  'status',   (1, 1)),
    'amnesia':       (0,   'psychic',  'status',   (1, 1)),
    'reflect':       (0,   'psychic',  'status',   (1, 1)),
    'lightscreen':   (0,   'psychic',  'status',   (1, 1)),
    'barrier':       (0,   'psychic',  'status',   (1, 1)),

    # ── Bug (physical) ───────────────────────────────────────────────────────
    'leechlife':     (20,  'bug',      'physical', (1, 1)),
    'pinmissile':    (14,  'bug',      'physical', (2, 5)),   # multi-hit
    'barrage':       (15,  'normal',   'physical', (2, 5)),   # multi-hit (Normal type)
    'stringshot':    (0,   'bug',      'status',   (1, 1)),

    # ── Rock (physical) ──────────────────────────────────────────────────────
    'rockthrow':     (50,  'rock',     'physical', (1, 1)),
    'rockslide':     (75,  'rock',     'physical', (1, 1)),
    'guillotine':    (0,   'normal',   'ohko',     (1, 1)),
    'horndrill':     (0,   'normal',   'ohko',     (1, 1)),

    # ── Ghost (physical) ─────────────────────────────────────────────────────
    'lick':          (20,  'ghost',    'physical', (1, 1)),   # note: same id as normal Lick above
    'nightshade':    (0,   'ghost',    'fixed',    (1, 1)),   # level-based fixed
    'confuseray':    (0,   'ghost',    'status',   (1, 1)),

    # ── Dragon (special) ─────────────────────────────────────────────────────
    'dragonrage':    (0,   'dragon',   'fixed',    (1, 1)),  # fixed 40 damage
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

# Moves that deal fixed damage (ignore type chart entirely)
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

def get_move(move_id: str):
    """
    Return (BP, type, category, (min_hits, max_hits)) or None if unknown.
    """
    return MOVES.get(move_id.lower().replace(' ', '').replace('-', ''))


def get_move_type(move_id: str) -> str | None:
    """Return just the type string for a move, or None."""
    m = get_move(move_id)
    return m[1] if m else None


def is_damaging(move_id: str) -> bool:
    """True if the move deals damage (physical, special, fixed, or ohko)."""
    m = get_move(move_id)
    return bool(m) and m[2] not in ('status',)


def average_hits(move_id: str) -> float:
    """
    Return the expected number of hits for a move.
    Multi-hit (2-5): average is ~3.17 in Gen 1 (uniform distribution).
    """
    m = get_move(move_id)
    if not m:
        return 1.0
    lo, hi = m[3]
    if lo == hi:
        return float(lo)
    return sum(range(lo, hi + 1)) / (hi - lo + 1)


# =============================================================================
# SELF-TEST
# =============================================================================

if __name__ == '__main__':
    print(f"Pokémon loaded: {len(POKEMON)}")
    print(f"Moves loaded:   {len(MOVES)}")
    print()

    # Spot-check a few Pokémon
    for species in ['tauros', 'alakazam', 'exeggutor', 'chansey', 'starmie', 'snorlax']:
        stats = get_stats(species)
        types = get_types(species)
        print(f"  {species:12s}: stats={stats}  types={types}")

    print()

    # Spot-check multi-hit moves
    for move_id in ['pinmissile', 'doublekick', 'bonemerang', 'hyperbeam']:
        m = get_move(move_id)
        avg = average_hits(move_id)
        print(f"  {move_id:16s}: BP={m[0]:3}  type={m[1]:10s}  cat={m[2]:8s}  "
              f"hits={m[3]}  avg={avg:.2f}")

    print()
    print("All 151 Pokémon present?", len(POKEMON) == 151)
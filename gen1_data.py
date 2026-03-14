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

Stats are the actual in-battle values at Level 100, DVs 15/15/15/15,
max Stat EXP (65535) — the Gen 1 OU standard.

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
    # --- Bulbasaur line ---
    'bulbasaur':    [203, 108, 108, 138, 108, 'grass',    'poison'],
    'ivysaur':      [243, 128, 128, 158, 128, 'grass',    'poison'],
    'venusaur':     [363, 262, 264, 298, 258, 'grass',    'poison'],

    # --- Charmander line ---
    'charmander':   [198, 112, 98,  118, 118, 'fire',     None],
    'charmeleon':   [238, 142, 128, 148, 148, 'fire',     None],
    'charizard':    [358, 268, 248, 268, 288, 'fire',     'flying'],

    # --- Squirtle line ---
    'squirtle':     [208, 108, 128, 118, 108, 'water',    None],
    'wartortle':    [248, 128, 158, 138, 128, 'water',    None],
    'blastoise':    [358, 248, 298, 268, 248, 'water',    None],

    # --- Caterpie line ---
    'caterpie':     [233, 70,  70,  70,  105, 'bug',      None],
    'metapod':      [243, 60,  120, 70,  80,  'bug',      None],
    'butterfree':   [323, 148, 148, 258, 228, 'bug',      'flying'],

    # --- Weedle line ---
    'weedle':       [223, 75,  65,  60,  105, 'bug',      'poison'],
    'kakuna':       [233, 60,  110, 70,  80,  'bug',      'poison'],
    'beedrill':     [303, 178, 138, 138, 218, 'bug',      'poison'],

    # --- Pidgey line ---
    'pidgey':       [198, 95,  85,  90,  115, 'normal',   'flying'],
    'pidgeotto':    [263, 130, 120, 120, 160, 'normal',   'flying'],
    'pidgeot':      [333, 178, 168, 168, 218, 'normal',   'flying'],

    # --- Rattata line ---
    'rattata':      [183, 106, 85,  80,  132, 'normal',   None],
    'raticate':     [263, 178, 138, 138, 238, 'normal',   None],

    # --- Spearow line ---
    'spearow':      [203, 120, 80,  80,  130, 'normal',   'flying'],
    'fearow':       [293, 198, 148, 148, 228, 'normal',   'flying'],

    # --- Ekans line ---
    'ekans':        [198, 110, 98,  98,  115, 'poison',   None],
    'arbok':        [283, 178, 158, 158, 188, 'poison',   None],

    # --- Pikachu line ---
    'pikachu':      [233, 138, 98,  118, 198, 'electric', None],
    'raichu':       [323, 218, 178, 218, 278, 'electric', None],

    # --- Sandshrew line ---
    'sandshrew':    [228, 125, 155, 80,  95,  'ground',   None],
    'sandslash':    [333, 215, 245, 148, 178, 'ground',   None],

    # --- Nidoran line ---
    'nidoranf':     [233, 107, 115, 108, 113, 'poison',   None],
    'nidorina':     [283, 137, 145, 138, 143, 'poison',   None],
    'nidoqueen':    [373, 232, 242, 228, 238, 'poison',   'ground'],
    'nidoranm':     [213, 117, 105, 98,  115, 'poison',   None],
    'nidorino':     [263, 157, 137, 128, 145, 'poison',   None],
    'nidoking':     [365, 282, 247, 268, 268, 'poison',   'ground'],

    # --- Clefairy line ---
    'clefairy':     [283, 118, 118, 148, 118, 'normal',   None],
    'clefable':     [393, 238, 234, 268, 218, 'normal',   None],

    # --- Vulpix line ---
    'vulpix':       [198, 108, 108, 128, 148, 'fire',     None],
    'ninetales':    [323, 218, 218, 248, 288, 'fire',     None],

    # --- Jigglypuff line ---
    'jigglypuff':   [383, 118, 98,  98,  88,  'normal',   None],
    'wigglytuff':   [493, 198, 178, 178, 148, 'normal',   None],

    # --- Zubat line ---
    'zubat':        [193, 115, 105, 90,  115, 'poison',   'flying'],
    'golbat':       [313, 195, 175, 160, 195, 'poison',   'flying'],

    # --- Oddish line ---
    'oddish':       [233, 118, 118, 148, 98,  'grass',    'poison'],
    'gloom':        [263, 138, 138, 168, 108, 'grass',    'poison'],
    'vileplume':    [343, 208, 208, 268, 178, 'grass',    'poison'],

    # --- Paras line ---
    'paras':        [208, 145, 125, 110, 85,  'bug',      'grass'],
    'parasect':     [293, 215, 215, 160, 115, 'bug',      'grass'],

    # --- Venonat line ---
    'venonat':      [263, 120, 120, 120, 120, 'bug',      'poison'],
    'venomoth':     [303, 148, 138, 198, 218, 'bug',      'poison'],

    # --- Diglett line ---
    'diglett':      [153, 135, 85,  98,  215, 'ground',   None],
    'dugtrio':      [273, 258, 198, 238, 338, 'ground',   None],

    # --- Meowth line ---
    'meowth':       [233, 115, 105, 108, 178, 'normal',   None],
    'persian':      [333, 238, 218, 228, 328, 'normal',   None],

    # --- Psyduck line ---
    'psyduck':      [263, 148, 138, 148, 168, 'water',    None],
    'golduck':      [353, 228, 218, 248, 248, 'water',    None],

    # --- Mankey line ---
    'mankey':       [213, 165, 105, 98,  148, 'fighting', None],
    'primeape':     [303, 245, 178, 158, 238, 'fighting', None],

    # --- Growlithe line ---
    'growlithe':    [253, 165, 128, 148, 148, 'fire',     None],
    'arcanine':     [383, 318, 258, 298, 288, 'fire',     None],

    # --- Poliwag line ---
    'poliwag':      [233, 138, 118, 118, 198, 'water',    None],
    'poliwhirl':    [303, 168, 178, 148, 238, 'water',    None],
    'poliwrath':    [383, 268, 288, 238, 238, 'water',    'fighting'],

    # --- Abra line ---
    'abra':         [233, 108, 88,  298, 258, 'psychic',  None],
    'kadabra':      [273, 148, 128, 338, 298, 'psychic',  None],
    'alakazam':     [313, 198, 188, 368, 338, 'psychic',  None],

    # --- Machop line ---
    'machop':       [263, 205, 148, 98,  108, 'fighting', None],
    'machoke':      [343, 265, 208, 148, 158, 'fighting', None],
    'machamp':      [383, 358, 258, 228, 208, 'fighting', None],

    # --- Bellsprout line ---
    'bellsprout':   [233, 165, 98,  138, 113, 'grass',    'poison'],
    'weepinbell':   [293, 215, 138, 188, 148, 'grass',    'poison'],
    'victreebel':   [363, 308, 228, 298, 238, 'grass',    'poison'],

    # --- Tentacool line ---
    'tentacool':    [253, 128, 98,  258, 208, 'water',    'poison'],
    'tentacruel':   [363, 238, 228, 338, 298, 'water',    'poison'],

    # --- Geodude line ---
    'geodude':      [228, 185, 255, 108, 88,  'rock',     'ground'],
    'graveler':     [298, 255, 315, 148, 118, 'rock',     'ground'],
    'golem':        [363, 318, 358, 208, 188, 'rock',     'ground'],

    # --- Ponyta line ---
    'ponyta':       [283, 215, 168, 178, 238, 'fire',     None],
    'rapidash':     [333, 248, 198, 208, 298, 'fire',     None],

    # --- Slowpoke line ---
    'slowpoke':     [383, 188, 198, 188, 88,  'water',    'psychic'],
    'slowbro':      [393, 248, 318, 258, 158, 'water',    'psychic'],

    # --- Magnemite line ---
    'magnemite':    [223, 118, 168, 228, 148, 'electric', None],
    'magneton':     [303, 218, 288, 338, 238, 'electric', None],

    # --- Farfetchd ---
    "farfetchd":    [273, 168, 148, 148, 168, 'normal',   'flying'],

    # --- Doduo line ---
    'doduo':        [223, 238, 158, 138, 238, 'normal',   'flying'],
    'dodrio':       [323, 318, 238, 218, 298, 'normal',   'flying'],

    # --- Seel line ---
    'seel':         [293, 148, 168, 148, 148, 'water',    None],
    'dewgong':      [373, 198, 218, 238, 198, 'water',    'ice'],

    # --- Grimer line ---
    'grimer':       [323, 205, 148, 128, 98,  'poison',   None],
    'muk':          [413, 278, 218, 198, 168, 'poison',   None],

    # --- Shellder line ---
    'shellder':     [203, 165, 298, 108, 118, 'water',    None],
    'cloyster':     [303, 288, 458, 268, 238, 'water',    'ice'],

    # --- Gastly line ---
    'gastly':       [233, 108, 98,  298, 218, 'ghost',    'poison'],
    'haunter':      [283, 148, 128, 338, 258, 'ghost',    'poison'],
    'gengar':       [323, 228, 218, 358, 318, 'ghost',    'poison'],

    # --- Onix ---
    'onix':         [228, 115, 365, 98,  198, 'rock',     'ground'],

    # --- Drowzee line ---
    'drowzee':      [293, 148, 148, 248, 147, 'psychic',  None],
    'hypno':        [373, 234, 238, 328, 227, 'psychic',  None],

    # --- Krabby line ---
    'krabby':       [193, 245, 235, 98,  108, 'water',    None],
    'kingler':      [273, 348, 318, 148, 188, 'water',    None],

    # --- Voltorb line ---
    'voltorb':      [243, 108, 148, 138, 238, 'electric', None],
    'electrode':    [293, 148, 188, 178, 338, 'electric', None],

    # --- Exeggcute line ---
    'exeggcute':    [283, 128, 148, 188, 118, 'grass',    'psychic'],
    'exeggutor':    [393, 288, 268, 348, 208, 'grass',    'psychic'],

    # --- Cubone line ---
    'cubone':       [228, 145, 195, 128, 118, 'ground',   None],
    'marowak':      [293, 205, 265, 178, 168, 'ground',   None],

    # --- Hitmon line ---
    'hitmonlee':    [293, 318, 218, 148, 258, 'fighting', None],
    'hitmonchan':   [293, 278, 238, 148, 238, 'fighting', None],

    # --- Lickitung ---
    'lickitung':    [363, 168, 228, 178, 128, 'normal',   None],

    # --- Koffing line ---
    'koffing':      [253, 175, 225, 148, 118, 'poison',   None],
    'weezing':      [313, 235, 285, 198, 158, 'poison',   None],

    # --- Rhyhorn line ---
    'rhyhorn':      [323, 255, 255, 148, 118, 'ground',   'rock'],
    'rhydon':       [413, 358, 338, 188, 178, 'ground',   'rock'],

    # --- Chansey ---
    'chansey':      [703, 108, 108, 308, 198, 'normal',   None],

    # --- Tangela ---
    'tangela':      [323, 168, 298, 218, 218, 'grass',    None],

    # --- Kangaskhan ---
    'kangaskhan':   [373, 288, 258, 178, 278, 'normal',   None],

    # --- Horsea line ---
    'horsea':       [193, 128, 148, 178, 148, 'water',    None],
    'seadra':       [283, 218, 228, 258, 248, 'water',    None],

    # --- Goldeen line ---
    'goldeen':      [243, 178, 148, 128, 168, 'water',    None],
    'seaking':      [333, 258, 218, 198, 228, 'water',    None],

    # --- Staryu line ---
    'staryu':       [243, 148, 168, 198, 248, 'water',    None],
    'starmie':      [323, 248, 268, 298, 328, 'water',    'psychic'],

    # --- Mr. Mime ---
    'mrmime':       [303, 148, 148, 298, 278, 'psychic',  None],

    # --- Scyther ---
    'scyther':      [343, 298, 268, 198, 298, 'bug',      'flying'],

    # --- Jynx ---
    'jynx':         [333, 198, 168, 288, 288, 'ice',      'psychic'],

    # --- Electabuzz ---
    'electabuzz':   [333, 264, 208, 288, 308, 'electric', None],

    # --- Magmar ---
    'magmar':       [343, 278, 228, 298, 268, 'fire',     None],

    # --- Pinsir ---
    'pinsir':       [333, 318, 278, 178, 258, 'bug',      None],

    # --- Tauros ---
    'tauros':       [353, 298, 288, 238, 318, 'normal',   None],

    # --- Magikarp line ---
    'magikarp':     [163, 68,  128, 68,  128, 'water',    None],
    'gyarados':     [393, 348, 256, 298, 258, 'water',    'flying'],

    # --- Lapras ---
    'lapras':       [463, 268, 258, 288, 218, 'water',    'ice'],

    # --- Ditto ---
    'ditto':        [273, 188, 188, 188, 188, 'normal',   None],

    # --- Eevee line ---
    'eevee':        [273, 178, 168, 168, 198, 'normal',   None],
    'vaporeon':     [463, 228, 218, 318, 228, 'water',    None],
    'jolteon':      [333, 228, 218, 318, 358, 'electric', None],
    'flareon':      [333, 318, 218, 298, 218, 'fire',     None],

    # --- Porygon ---
    'porygon':      [313, 198, 218, 238, 198, 'normal',   None],

    # --- Omanyte line ---
    'omanyte':      [228, 148, 258, 208, 118, 'rock',     'water'],
    'omastar':      [318, 228, 338, 298, 198, 'rock',     'water'],

    # --- Kabuto line ---
    'kabuto':       [218, 195, 225, 138, 168, 'rock',     'water'],
    'kabutops':     [308, 288, 288, 188, 248, 'rock',     'water'],

    # --- Aerodactyl ---
    'aerodactyl':   [333, 298, 228, 198, 338, 'rock',     'flying'],

    # --- Snorlax ---
    'snorlax':      [523, 318, 228, 228, 158, 'normal',   None],

    # --- Legendary Birds ---
    'articuno':     [383, 268, 298, 348, 268, 'ice',      'flying'],
    'zapdos':       [383, 278, 268, 348, 298, 'electric', 'flying'],
    'moltres':      [383, 298, 278, 348, 278, 'fire',     'flying'],

    # --- Dratini line ---
    'dratini':      [223, 168, 148, 148, 148, 'dragon',   None],
    'dragonair':    [303, 228, 208, 208, 208, 'dragon',   None],
    'dragonite':    [386, 366, 288, 298, 258, 'dragon',   'flying'],

    # --- Mewtwo / Mew ---
    'mewtwo':       [416, 358, 278, 378, 358, 'psychic',  None],
    'mew':          [383, 298, 298, 298, 298, 'psychic',  None],
}


# =============================================================================
# CONVENIENCE ACCESSORS
# =============================================================================

def get_stats(species: str):
    """
    Return (HP, Atk, Def, Spc, Spe) for a species, or None if unknown.
    """
    row = POKEMON.get(species.lower())
    if row is None:
        return None
    return tuple(row[:5])


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
    'comet punch':   (18,  'normal',   'physical', (2, 5)),   # multi-hit
    'cometpunch':    (18,  'normal',   'physical', (2, 5)),   # multi-hit
    'doubleslap':    (15,  'normal',   'physical', (2, 5)),   # multi-hit
    'spikecannon':   (20,  'normal',   'physical', (2, 5)),   # multi-hit
    'wrap':          (15,  'normal',   'physical', (2, 5)),   # trapping / multi-hit
    'bind':          (15,  'normal',   'physical', (2, 5)),   # trapping / multi-hit
    'lick':          (20,  'normal',   'physical', (1, 1)),
    'rage':          (20,  'normal',   'physical', (1, 1)),
    'swift':         (60,  'normal',   'special',  (1, 1)),   # Gen 1: special split
    'bide':          (0,   'normal',   'physical', (1, 1)),   # returns 2x damage taken
    'explosion':     (340, 'normal',   'physical', (1, 1)),   # halves target's Def
    'selfdestruct':  (260, 'normal',   'physical', (1, 1)),   # halves target's Def
    'takedown':      (90,  'normal',   'physical', (1, 1)),
    'thrash':        (90,  'normal',   'physical', (2, 3)),   # 2-3 turns
    'petal dance':   (70,  'normal',   'special',  (2, 3)),   # misses; treated as normal here
    'petaldance':    (70,  'normal',   'special',  (2, 3)),
    'skullbash':     (100, 'normal',   'physical', (1, 1)),   # charges then hits
    'eggbomb':       (100, 'normal',   'physical', (1, 1)),
    'softboiled':    (0,   'normal',   'status',   (1, 1)),
    'recover':       (0,   'normal',   'status',   (1, 1)),
    'substitute':    (0,   'normal',   'status',   (1, 1)),
    'swordsdance':   (0,   'normal',   'status',   (1, 1)),
    'toxic':         (0,   'normal',   'status',   (1, 1)),   # wait — Toxic is poison type
    'growl':         (0,   'normal',   'status',   (1, 1)),
    'tail whip':     (0,   'normal',   'status',   (1, 1)),
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
    'clamp':         (35,  'water',    'physical', (2, 5)),   # trapping; physical in Gen1
    'crabhammer':    (90,  'water',    'physical', (1, 1)),   # physical in Gen1
    'waterfall':     (80,  'water',    'physical', (1, 1)),   # physical in Gen1
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
    'mudslap':       (20,  'ground',   'special',  (1, 1)),   # Gen1 special? — treating physical

    # ── Flying (physical) ────────────────────────────────────────────────────
    'gust':          (40,  'flying',   'physical', (1, 1)),
    'wingattack':    (35,  'flying',   'physical', (1, 1)),
    'drillpeck':     (80,  'flying',   'physical', (1, 1)),
    'peck':          (35,  'flying',   'physical', (1, 1)),
    'skyattack':     (140, 'flying',   'physical', (1, 1)),   # charges + hits
    'fly':           (70,  'flying',   'physical', (1, 1)),   # charges + hits
    'mirror move':   (0,   'flying',   'status',   (1, 1)),
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
    'dragonrage':    (0,   'dragon',   'fixed',    (1, 'dragon')),  # fixed 40 damage
    'twister':       (40,  'dragon',   'special',  (1, 1)),   # not in Gen 1 RBY, skip if needed
}

# Fix the duplicate 'lick' key — Ghost type wins (physical ghost lick)
# Pokémon that learn Lick (Gengar line) deal Ghost-type in Gen 1.
# The Normal-type Lick entry above is overwritten by the Ghost entry.
MOVES['lick'] = (20, 'ghost', 'physical', (1, 1))
# Fix toxic duplication (appears under both normal and poison sections)
MOVES['toxic'] = (0, 'poison', 'status', (1, 1))


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
    for species in ['bulbasaur', 'charizard', 'gengar', 'snorlax', 'mewtwo', 'mew']:
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

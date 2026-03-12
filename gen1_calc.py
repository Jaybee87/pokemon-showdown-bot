"""
gen1_calc.py
============
Gen 1 damage calculator and speed table for competitive play.

All values are pre-computed for:
  Level 100, DVs 15/15/15/15, Stat EXP 65535 (max everything)

This is the standard for Gen 1 OU — there's no variation.
No items, no abilities, no natures. Pure maths.

The damage formula is deterministic except for a random factor (217-255)/255,
giving a range of ~85% to 100% of max damage per hit.

Usage:
    from gen1_calc import calc_damage, can_ko, outspeeds, get_speed

    # Damage range: Tauros Body Slam vs Alakazam
    lo, hi = calc_damage('tauros', 'bodyslam', 'alakazam')

    # Can Tauros KO Alakazam at 45% HP?
    can_ko('tauros', 'bodyslam', 'alakazam', hp_pct=0.45)

    # Does Alakazam outspeed Tauros?
    outspeeds('alakazam', 'tauros')
"""

from gen1_engine import type_effectiveness, TYPE_CHART, FIXED_DAMAGE_MOVES

# =============================================================================
# GEN 1 STATS — L100, 15 DVs, max Stat EXP
#
# Format: (HP, Attack, Defense, Special, Speed)
# In Gen 1, Special is used for both offense and defense for special moves.
# These are the actual in-battle stats, not base stats.
# Formula: ((Base + DV) * 2 + ceil(sqrt(Stat EXP)) / 4) * Level / 100 + 5
#          (HP adds Level + 10 instead of + 5)
# =============================================================================

STATS = {
    # Pokemon        HP   Atk  Def  Spc  Spe
    'alakazam':    (313, 198, 188, 368, 338),
    'articuno':    (383, 268, 298, 348, 268),
    'chansey':     (703, 108, 108, 308, 198),
    'clefable':    (393, 238, 234, 268, 218),
    'cloyster':    (303, 288, 458, 268, 238),
    'dodrio':      (323, 318, 238, 218, 298),
    'dragonite':   (386, 366, 288, 298, 258),
    'dugtrio':     (273, 258, 198, 238, 338),
    'electabuzz':  (333, 264, 208, 288, 308),
    'exeggutor':   (393, 288, 268, 348, 208),
    'gengar':      (323, 228, 218, 358, 318),
    'golem':       (363, 318, 358, 208, 188),
    'gyarados':    (393, 348, 256, 298, 258),
    'hypno':       (373, 234, 238, 328, 227),
    'jolteon':     (333, 228, 218, 318, 358),
    'jynx':        (333, 198, 168, 288, 288),
    'kangaskhan':  (373, 288, 258, 178, 278),
    'lapras':      (463, 268, 258, 288, 218),
    'moltres':     (383, 298, 278, 348, 278),
    'nidoking':    (365, 282, 247, 268, 268),
    'persian':     (333, 238, 218, 228, 328),
    'rhydon':      (413, 358, 338, 188, 178),
    'slowbro':     (393, 248, 318, 258, 158),
    'snorlax':     (523, 318, 228, 228, 158),
    'starmie':     (323, 248, 268, 298, 328),
    'tauros':      (353, 298, 288, 238, 318),
    'vaporeon':    (463, 228, 218, 318, 228),
    'zapdos':      (383, 278, 268, 348, 298),
    # Lower tiers that show up sometimes
    'arcanine':    (383, 318, 258, 298, 288),
    'magneton':    (303, 218, 288, 338, 238),
    'tentacruel':  (363, 238, 228, 338, 298),
    'venusaur':    (363, 262, 264, 298, 258),
    'victreebel':  (363, 308, 228, 298, 238),
    'poliwrath':   (383, 268, 288, 238, 238),
    'machamp':     (383, 358, 258, 228, 208),
}

# =============================================================================
# MOVE DATA — base power, type, physical/special
#
# In Gen 1: Normal, Fighting, Rock, Ground, Flying, Bug, Ghost, Poison = Physical
#           Fire, Water, Electric, Grass, Ice, Psychic, Dragon = Special
# =============================================================================

SPECIAL_TYPES = {'fire', 'water', 'electric', 'grass', 'ice', 'psychic', 'dragon'}

MOVES = {
    # Move ID         BP   Type         Category
    'bodyslam':      (85,  'normal',    'physical'),
    'hyperbeam':     (150, 'normal',    'physical'),
    'earthquake':    (100, 'ground',    'physical'),
    'blizzard':      (120, 'ice',       'special'),
    'icebeam':       (95,  'ice',       'special'),
    'thunderbolt':   (95,  'electric',  'special'),
    'thunder':       (120, 'electric',  'special'),
    'psychic':       (90,  'psychic',   'special'),
    'surf':          (95,  'water',     'special'),
    'fireblast':     (120, 'fire',      'special'),
    'megadrain':     (40,  'grass',     'special'),
    'drillpeck':     (80,  'flying',    'physical'),
    'rockslide':     (75,  'rock',      'physical'),
    'explosion':     (340, 'normal',    'physical'),  # halves target def in Gen 1
    'selfdestruct':  (260, 'normal',    'physical'),
    'nightshade':    (0,   'ghost',     'fixed'),     # fixed 100 damage
    'seismictoss':   (0,   'fighting',  'fixed'),     # fixed 100 damage
    'sleeppowder':   (0,   'grass',     'status'),
    'stunspore':     (0,   'grass',     'status'),
    'thunderwave':   (0,   'electric',  'status'),
    'hypnosis':      (0,   'psychic',   'status'),
    'lovelykiss':    (0,   'normal',    'status'),
    'sing':          (0,   'normal',    'status'),
    'toxic':         (0,   'poison',    'status'),
    'recover':       (0,   'normal',    'status'),
    'softboiled':    (0,   'normal',    'status'),
    'rest':          (0,   'psychic',   'status'),
    'substitute':    (0,   'normal',    'status'),
    'megapunch':     (80,  'normal',    'physical'),
    'megakick':      (120, 'normal',    'physical'),
    'swift':         (60,  'normal',    'special'),
    'strength':      (80,  'normal',    'physical'),
    'rage':          (20,  'normal',    'physical'),
}


# =============================================================================
# POKEMON TYPES (for STAB and effectiveness)
# =============================================================================

POKEMON_TYPES = {
    'alakazam':    ('psychic',),
    'articuno':    ('ice', 'flying'),
    'chansey':     ('normal',),
    'clefable':    ('normal',),
    'cloyster':    ('water', 'ice'),
    'dodrio':      ('normal', 'flying'),
    'dragonite':   ('dragon', 'flying'),
    'dugtrio':     ('ground',),
    'electabuzz':  ('electric',),
    'exeggutor':   ('grass', 'psychic'),
    'gengar':      ('ghost', 'poison'),
    'golem':       ('rock', 'ground'),
    'gyarados':    ('water', 'flying'),
    'hypno':       ('psychic',),
    'jolteon':     ('electric',),
    'jynx':        ('ice', 'psychic'),
    'kangaskhan':  ('normal',),
    'lapras':      ('water', 'ice'),
    'moltres':     ('fire', 'flying'),
    'nidoking':    ('poison', 'ground'),
    'persian':     ('normal',),
    'rhydon':      ('ground', 'rock'),
    'slowbro':     ('water', 'psychic'),
    'snorlax':     ('normal',),
    'starmie':     ('water', 'psychic'),
    'tauros':      ('normal',),
    'vaporeon':    ('water',),
    'zapdos':      ('electric', 'flying'),
    'arcanine':    ('fire',),
    'magneton':    ('electric',),
    'tentacruel':  ('water', 'poison'),
    'venusaur':    ('grass', 'poison'),
    'victreebel':  ('grass', 'poison'),
    'poliwrath':   ('water', 'fighting'),
    'machamp':     ('fighting',),
}


# =============================================================================
# DAMAGE CALCULATOR
# =============================================================================

def calc_damage(attacker, move_id, defender, crit=False, par_attacker=False):
    """
    Calculate the damage range for a specific attack.

    Args:
        attacker:  species name (e.g. 'tauros')
        move_id:   move ID (e.g. 'bodyslam')
        defender:  species name (e.g. 'alakazam')
        crit:      critical hit (doubles in Gen 1 — ignores stat mods)
        par_attacker: is the attacker paralyzed? (not used in damage, just for speed)

    Returns:
        (min_damage, max_damage) tuple, or (100, 100) for fixed-damage moves.
        Returns (0, 0) for status moves or immune matchups.
    """
    if move_id not in MOVES:
        return (0, 0)

    bp, move_type, category = MOVES[move_id]

    # Status moves do no damage
    if category == 'status':
        return (0, 0)

    # Fixed damage moves
    if category == 'fixed' or move_id in FIXED_DAMAGE_MOVES:
        # Check immunity: Night Shade (ghost) can't hit Normal, Seismic Toss (fighting) can't hit Ghost
        def_types = list(POKEMON_TYPES.get(defender, ('normal',)))
        eff = type_effectiveness(move_type, def_types)
        if eff == 0:
            return (0, 0)
        return (100, 100)

    # Get stats
    atk_stats = STATS.get(attacker)
    def_stats = STATS.get(defender)
    if not atk_stats or not def_stats:
        return (0, 0)

    atk_types = POKEMON_TYPES.get(attacker, ('normal',))
    def_types = list(POKEMON_TYPES.get(defender, ('normal',)))

    # Determine attack and defense stats
    if category == 'physical' or move_type not in SPECIAL_TYPES:
        attack = atk_stats[1]   # Attack
        defense = def_stats[2]  # Defense
    else:
        attack = atk_stats[3]   # Special (offense)
        defense = def_stats[3]  # Special (defense)

    # Explosion/Self-Destruct halve target's defense in Gen 1
    if move_id in ('explosion', 'selfdestruct'):
        defense = max(1, defense // 2)

    # Critical hit: use base stats * 2 (simplified — ignores stat stages)
    if crit:
        attack = attack * 2
        defense = defense * 2

    # Type effectiveness
    eff = type_effectiveness(move_type, def_types)
    if eff == 0:
        return (0, 0)

    # STAB
    stab = 1.5 if move_type in atk_types else 1.0

    # Gen 1 damage formula
    # ((2 * Level / 5 + 2) * Power * Attack / Defense) / 50 + 2
    base_damage = ((2 * 100 / 5 + 2) * bp * attack / defense) / 50 + 2
    base_damage = int(base_damage * stab)
    base_damage = int(base_damage * eff)

    # Random factor: 217/255 to 255/255
    min_dmg = max(1, int(base_damage * 217 / 255))
    max_dmg = base_damage

    return (min_dmg, max_dmg)


def calc_damage_pct(attacker, move_id, defender, **kwargs):
    """
    Calculate damage as a percentage of the defender's max HP.
    Returns (min_pct, max_pct) as floats (0.0 to 1.0+).
    """
    lo, hi = calc_damage(attacker, move_id, defender, **kwargs)
    def_stats = STATS.get(defender)
    if not def_stats:
        return (0.0, 0.0)
    max_hp = def_stats[0]
    return (lo / max_hp, hi / max_hp)


# =============================================================================
# KO CHECKS
# =============================================================================

def can_ko(attacker, move_id, defender, hp_pct=1.0, use_avg=True):
    """
    Can this move KO the defender at the given HP percentage?

    Args:
        hp_pct:  defender's current HP as a fraction (0.0 to 1.0)
        use_avg: if True, use average damage; if False, use minimum (conservative)

    Returns True if the move can KO.
    """
    lo, hi = calc_damage(attacker, move_id, defender)
    def_stats = STATS.get(defender)
    if not def_stats:
        return False
    current_hp = int(def_stats[0] * hp_pct)
    if use_avg:
        avg = (lo + hi) // 2
        return avg >= current_hp
    return lo >= current_hp  # guaranteed KO


def find_ko_move(attacker, moves, defender, hp_pct=1.0):
    """
    From a list of move IDs, find the best one that can KO the defender.
    Prefers guaranteed KOs (min roll) over average KOs.

    Returns (move_id, guaranteed) or (None, False) if nothing KOs.
    """
    best_guaranteed = None
    best_avg = None

    for move_id in moves:
        lo, hi = calc_damage(attacker, move_id, defender)
        def_stats = STATS.get(defender)
        if not def_stats:
            continue
        current_hp = int(def_stats[0] * hp_pct)

        if lo >= current_hp:
            # Guaranteed KO — prefer highest overkill
            if best_guaranteed is None or lo > best_guaranteed[1]:
                best_guaranteed = (move_id, lo)
        elif (lo + hi) // 2 >= current_hp:
            # Average KO
            if best_avg is None or (lo + hi) > best_avg[1]:
                best_avg = (move_id, lo + hi)

    if best_guaranteed:
        return (best_guaranteed[0], True)
    if best_avg:
        return (best_avg[0], False)
    return (None, False)


# =============================================================================
# SPEED TABLE
# =============================================================================

def get_speed(species, paralyzed=False):
    """Get a Pokemon's effective speed. Paralysis quarters it in Gen 1."""
    stats = STATS.get(species)
    if not stats:
        return 0
    speed = stats[4]
    if paralyzed:
        speed = speed // 4
    return speed


def outspeeds(species_a, species_b, a_par=False, b_par=False):
    """
    Does species_a outspeed species_b?
    Returns True if A is faster, False if B is faster or tied.
    In Gen 1, ties are broken randomly — we return False (conservative).
    """
    return get_speed(species_a, a_par) > get_speed(species_b, b_par)


def get_speed_tier(species, paralyzed=False):
    """
    Return a human-readable speed tier label.
    Useful for LLM prompts and debugging.
    """
    speed = get_speed(species, paralyzed)
    if speed >= 350:
        return "blazing"
    elif speed >= 300:
        return "fast"
    elif speed >= 250:
        return "moderate"
    elif speed >= 200:
        return "slow"
    else:
        return "very slow"


# =============================================================================
# CONVENIENCE: two-hit KO check
# =============================================================================

def can_2hko(attacker, move_id, defender, hp_pct=1.0):
    """Can two hits of this move KO the defender at current HP?"""
    lo, hi = calc_damage(attacker, move_id, defender)
    def_stats = STATS.get(defender)
    if not def_stats:
        return False
    current_hp = int(def_stats[0] * hp_pct)
    avg = (lo + hi) // 2
    return avg * 2 >= current_hp


# =============================================================================
# SELF-TEST
# =============================================================================

if __name__ == "__main__":
    print("Gen 1 Damage Calculator — sanity checks\n")

    # Tauros Body Slam vs Alakazam
    lo, hi = calc_damage('tauros', 'bodyslam', 'alakazam')
    pct_lo, pct_hi = calc_damage_pct('tauros', 'bodyslam', 'alakazam')
    print(f"Tauros Body Slam vs Alakazam: {lo}-{hi} ({pct_lo*100:.0f}%-{pct_hi*100:.0f}%)")

    # Tauros Earthquake vs Gengar (should be immune — Normal doesn't affect Ghost... wait, EQ is Ground)
    lo, hi = calc_damage('tauros', 'earthquake', 'gengar')
    print(f"Tauros Earthquake vs Gengar: {lo}-{hi}")

    # Alakazam Psychic vs Gengar (Psychic→Ghost/Poison = 0x * 2x... Ghost immune to Psychic in Gen1? No.)
    # Actually: Psychic→Ghost = 1x (Gen1), Psychic→Poison = 2x, so combined = 2x
    lo, hi = calc_damage('alakazam', 'psychic', 'gengar')
    pct_lo, pct_hi = calc_damage_pct('alakazam', 'psychic', 'gengar')
    print(f"Alakazam Psychic vs Gengar: {lo}-{hi} ({pct_lo*100:.0f}%-{pct_hi*100:.0f}%)")

    # Chansey Seismic Toss vs Snorlax (fixed 100)
    lo, hi = calc_damage('chansey', 'seismictoss', 'snorlax')
    print(f"Chansey Seismic Toss vs Snorlax: {lo}-{hi}")

    # Chansey Ice Beam vs Snorlax
    lo, hi = calc_damage('chansey', 'icebeam', 'snorlax')
    pct_lo, pct_hi = calc_damage_pct('chansey', 'icebeam', 'snorlax')
    print(f"Chansey Ice Beam vs Snorlax: {lo}-{hi} ({pct_lo*100:.0f}%-{pct_hi*100:.0f}%)")

    # Speed checks
    print(f"\nSpeed tiers:")
    for mon in ['jolteon', 'alakazam', 'tauros', 'starmie', 'gengar', 'exeggutor', 'snorlax', 'chansey', 'rhydon']:
        spd = get_speed(mon)
        par_spd = get_speed(mon, paralyzed=True)
        tier = get_speed_tier(mon)
        print(f"  {mon:12s}: {spd} ({tier}) | paralyzed: {par_spd}")

    # KO check: Tauros Body Slam vs Alakazam at 45% HP
    print(f"\nTauros Body Slam KOs Alakazam at 45%? {can_ko('tauros', 'bodyslam', 'alakazam', 0.45)}")
    print(f"Tauros Body Slam KOs Alakazam at 45% (guaranteed)? {can_ko('tauros', 'bodyslam', 'alakazam', 0.45, use_avg=False)}")

    # Find KO move
    print(f"\nBest KO move — Starmie vs Snorlax at 20%:")
    move, guaranteed = find_ko_move('starmie', ['surf', 'thunderbolt', 'psychic'], 'snorlax', 0.20)
    print(f"  {move} (guaranteed: {guaranteed})")
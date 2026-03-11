"""
gen1_data.py
============
Builds a complete Gen 1 Pokemon dataset by fetching directly from the
pret/pokered decompilation project — the canonical source of truth for
what moves each Pokemon could legally know in the original cartridges.

Tier data is sourced from the local Pokemon Showdown install.

If Showdown rejects a move that appears here, that is a Showdown bug.

Usage:
    python3 gen1_data.py                    # fetch all 151, save gen1_data.json
    load_format_data("OU")                  # returns OU-legal Pokemon + moves
    load_format_data("UU")                  # same for UU, etc.
"""

import re
import json
import os
import urllib.request
from collections import Counter

# =============================================================================
# CONFIGURATION
# =============================================================================

POKERED_BASE_STATS = "https://raw.githubusercontent.com/pret/pokered/master/data/pokemon/base_stats/{}.asm"
POKERED_LEARNSETS  = "https://raw.githubusercontent.com/pret/pokered/master/data/pokemon/moves/{}.asm"
SHOWDOWN_PATH      = os.path.expanduser("~/pokemon-showdown")

# =============================================================================
# ALL 151 POKEMON
# Showdown internal names — these are also the pokered filenames.
# No mapping needed, they match exactly.
# =============================================================================

ALL_POKEMON = [
    'bulbasaur', 'ivysaur', 'venusaur', 'charmander', 'charmeleon', 'charizard',
    'squirtle', 'wartortle', 'blastoise', 'caterpie', 'metapod', 'butterfree',
    'weedle', 'kakuna', 'beedrill', 'pidgey', 'pidgeotto', 'pidgeot',
    'rattata', 'raticate', 'spearow', 'fearow', 'ekans', 'arbok',
    'pikachu', 'raichu', 'sandshrew', 'sandslash', 'nidoranf', 'nidorina',
    'nidoqueen', 'nidoranm', 'nidorino', 'nidoking', 'clefairy', 'clefable',
    'vulpix', 'ninetales', 'jigglypuff', 'wigglytuff', 'zubat', 'golbat',
    'oddish', 'gloom', 'vileplume', 'paras', 'parasect', 'venonat',
    'venomoth', 'diglett', 'dugtrio', 'meowth', 'persian', 'psyduck',
    'golduck', 'mankey', 'primeape', 'growlithe', 'arcanine', 'poliwag',
    'poliwhirl', 'poliwrath', 'abra', 'kadabra', 'alakazam', 'machop',
    'machoke', 'machamp', 'bellsprout', 'weepinbell', 'victreebel', 'tentacool',
    'tentacruel', 'geodude', 'graveler', 'golem', 'ponyta', 'rapidash',
    'slowpoke', 'slowbro', 'magnemite', 'magneton', 'farfetchd', 'doduo',
    'dodrio', 'seel', 'dewgong', 'grimer', 'muk', 'shellder',
    'cloyster', 'gastly', 'haunter', 'gengar', 'onix', 'drowzee',
    'hypno', 'krabby', 'kingler', 'voltorb', 'electrode', 'exeggcute',
    'exeggutor', 'cubone', 'marowak', 'hitmonlee', 'hitmonchan', 'lickitung',
    'koffing', 'weezing', 'rhyhorn', 'rhydon', 'chansey', 'tangela',
    'kangaskhan', 'horsea', 'seadra', 'goldeen', 'seaking', 'staryu',
    'starmie', 'mrmime', 'scyther', 'jynx', 'electabuzz', 'magmar',
    'pinsir', 'tauros', 'magikarp', 'gyarados', 'lapras', 'ditto',
    'eevee', 'vaporeon', 'jolteon', 'flareon', 'porygon', 'omanyte',
    'omastar', 'kabuto', 'kabutops', 'aerodactyl', 'snorlax', 'articuno',
    'zapdos', 'moltres', 'dratini', 'dragonair', 'dragonite', 'mewtwo', 'mew',
]

# Display name overrides — only needed where .capitalize() isn't enough
DISPLAY_NAME_OVERRIDES = {
    'nidoranf':  'Nidoran-F',
    'nidoranm':  'Nidoran-M',
    'mrmime':    'Mr. Mime',
    'farfetchd': "Farfetch'd",
}

# Format filter definitions
FORMAT_INCLUDES = {
    "OU":    {"OU"},
    "Ubers": {"Uber", "OU"},
    "UU":    {"UU"},
    "NU":    {"NU"},
    "PU":    {"PU"},
    "ZU":    {"ZU"},
    "LC":    {"LC"},
    "NFE":   {"NFE"},
}

# =============================================================================
# MOVE NAME MAP: pokered constant → Showdown internal name
# =============================================================================

MOVE_NAME_MAP = {
    'ABSORB':          'absorb',
    'ACID':            'acid',
    'ACID_ARMOR':      'acidarmor',
    'AGILITY':         'agility',
    'AMNESIA':         'amnesia',
    'AURORA_BEAM':     'aurorabeam',
    'BARRAGE':         'barrage',
    'BARRIER':         'barrier',
    'BIDE':            'bide',
    'BIND':            'bind',
    'BITE':            'bite',
    'BLIZZARD':        'blizzard',
    'BODY_SLAM':       'bodyslam',
    'BONE_CLUB':       'boneclub',
    'BONEMERANG':      'bonemerang',
    'BUBBLE':          'bubble',
    'BUBBLE_BEAM':     'bubblebeam',
    'BUBBLEBEAM':      'bubblebeam',
    'CLAMP':           'clamp',
    'COMET_PUNCH':     'cometpunch',
    'CONFUSE_RAY':     'confuseray',
    'CONFUSION':       'confusion',
    'CONSTRICT':       'constrict',
    'CONVERSION':      'conversion',
    'COUNTER':         'counter',
    'CRABHAMMER':      'crabhammer',
    'CUT':             'cut',
    'DEFENSE_CURL':    'defensecurl',
    'DIG':             'dig',
    'DISABLE':         'disable',
    'DIZZY_PUNCH':     'dizzypunch',
    'DOUBLE_EDGE':     'doubleedge',
    'DOUBLE_KICK':     'doublekick',
    'DOUBLE_SLAP':     'doubleslap',
    'DOUBLE_TEAM':     'doubleteam',
    'DRAGON_RAGE':     'dragonrage',
    'DREAM_EATER':     'dreameater',
    'DRILL_PECK':      'drillpeck',
    'EARTHQUAKE':      'earthquake',
    'EGG_BOMB':        'eggbomb',
    'EMBER':           'ember',
    'EXPLOSION':       'explosion',
    'FIRE_BLAST':      'fireblast',
    'FIRE_PUNCH':      'firepunch',
    'FIRE_SPIN':       'firespin',
    'FISSURE':         'fissure',
    'FLASH':           'flash',
    'FLY':             'fly',
    'FOCUS_ENERGY':    'focusenergy',
    'FURY_ATTACK':     'furyattack',
    'FURY_SWIPES':     'furyswipes',
    'GLARE':           'glare',
    'GROWL':           'growl',
    'GROWTH':          'growth',
    'GUILLOTINE':      'guillotine',
    'GUST':            'gust',
    'HARDEN':          'harden',
    'HAZE':            'haze',
    'HEADBUTT':        'headbutt',
    'HIGH_JUMP_KICK':  'highjumpkick',
    'HORN_ATTACK':     'hornattack',
    'HORN_DRILL':      'horndrill',
    'HYDRO_PUMP':      'hydropump',
    'HYPER_BEAM':      'hyperbeam',
    'HYPER_FANG':      'hyperfang',
    'HYPNOSIS':        'hypnosis',
    'ICE_BEAM':        'icebeam',
    'ICE_PUNCH':       'icepunch',
    'JUMP_KICK':       'jumpkick',
    'KARATE_CHOP':     'karatechop',
    'KINESIS':         'kinesis',
    'LEECH_LIFE':      'leechlife',
    'LEECH_SEED':      'leechseed',
    'LEER':            'leer',
    'LICK':            'lick',
    'LIGHT_SCREEN':    'lightscreen',
    'LOVELY_KISS':     'lovelykiss',
    'LOW_KICK':        'lowkick',
    'MEDITATE':        'meditate',
    'MEGA_DRAIN':      'megadrain',
    'MEGA_KICK':       'megakick',
    'MEGA_PUNCH':      'megapunch',
    'METRONOME':       'metronome',
    'MIMIC':           'mimic',
    'MINIMIZE':        'minimize',
    'MIST':            'mist',
    'NIGHT_SHADE':     'nightshade',
    'PAYDAY':          'payday',
    'PECK':            'peck',
    'PETAL_DANCE':     'petaldance',
    'PIN_MISSILE':     'pinmissile',
    'POISON_GAS':      'poisongas',
    'POISON_POWDER':   'poisonpowder',
    'POISON_STING':    'poisonsting',
    'POUND':           'pound',
    'PSYBEAM':         'psybeam',
    'PSYCHIC_M':       'psychic',
    'PSYWAVE':         'psywave',
    'QUICK_ATTACK':    'quickattack',
    'RAGE':            'rage',
    'RAZOR_LEAF':      'razorleaf',
    'RAZOR_WIND':      'razorwind',
    'RECOVER':         'recover',
    'REFLECT':         'reflect',
    'REST':            'rest',
    'ROAR':            'roar',
    'ROCK_SLIDE':      'rockslide',
    'ROCK_THROW':      'rockthrow',
    'ROLLING_KICK':    'rollingkick',
    'SAND_ATTACK':     'sandattack',
    'SCRATCH':         'scratch',
    'SCREECH':         'screech',
    'SEISMIC_TOSS':    'seismictoss',
    'SELF_DESTRUCT':   'selfdestruct',
    'SING':            'sing',
    'SKULL_BASH':      'skullbash',
    'SKY_ATTACK':      'skyattack',
    'SLAM':            'slam',
    'SLASH':           'slash',
    'SLEEP_POWDER':    'sleeppowder',
    'SLUDGE':          'sludge',
    'SMOG':            'smog',
    'SMOKESCREEN':     'smokescreen',
    'SOFT_BOILED':     'softboiled',
    'SOLAR_BEAM':      'solarbeam',
    'SPIKE_CANNON':    'spikecannon',
    'SPLASH':          'splash',
    'STOMP':           'stomp',
    'STRING_SHOT':     'stringshot',
    'STRENGTH':        'strength',
    'STUN_SPORE':      'stunspore',
    'SUBMISSION':      'submission',
    'SUBSTITUTE':      'substitute',
    'SUPER_FANG':      'superfang',
    'SUPERSONIC':      'supersonic',
    'SURF':            'surf',
    'SWIFT':           'swift',
    'TACKLE':          'tackle',
    'TAIL_WHIP':       'tailwhip',
    'TAKE_DOWN':       'takedown',
    'TELEPORT':        'teleport',
    'THRASH':          'thrash',
    'THUNDER':         'thunder',
    'THUNDER_PUNCH':   'thunderpunch',
    'THUNDER_SHOCK':   'thundershock',
    'THUNDER_WAVE':    'thunderwave',
    'THUNDERBOLT':     'thunderbolt',
    'TOXIC':           'toxic',
    'TRANSFORM':       'transform',
    'TRI_ATTACK':      'triattack',
    'TWINEEDLE':       'twineedle',
    'VICE_GRIP':       'vicegrip',
    'VINE_WHIP':       'vinewhip',
    'WATER_GUN':       'watergun',
    'WATERFALL':       'waterfall',
    'WHIRLWIND':       'whirlwind',
    'WING_ATTACK':     'wingattack',
    'WITHDRAW':        'withdraw',
    'WRAP':            'wrap',
    'ZAP_CANNON':      'zapcannon',
    'NO_MOVE':         None,
}

# Moves banned by Gen 1 OU format clauses
FORMAT_BANLIST = {
    'dig',        # Banned by Standard clause
    'fly',        # Banned by Standard clause
    'doubleteam', # Evasion Moves Clause
    'minimize',   # Evasion Moves Clause
    'fissure',    # OHKO Clause
    'guillotine', # OHKO Clause
    'horndrill',  # OHKO Clause
}

# =============================================================================
# HELPERS
# =============================================================================

def get_display_name(showdown_name):
    return DISPLAY_NAME_OVERRIDES.get(showdown_name, showdown_name.capitalize())


def fetch_url(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read().decode('utf-8')
    except Exception:
        return None


def parse_base_stats_moves(asm, showdown_name):
    """Extract level 1 and TM/HM moves from base stats ASM"""
    moves = set()
    unmapped = set()

    # Level 1 moves
    level1 = re.search(
        r'db\s+([\w_]+),\s*([\w_]+),\s*([\w_]+),\s*([\w_]+)\s*;.*level 1',
        asm
    )
    if level1:
        for m in level1.groups():
            mapped = MOVE_NAME_MAP.get(m)
            if mapped:
                moves.add(mapped)

    # TM/HM block
    tmhm = re.search(
        r'tmhm\s+([\w_,\s\\]+?)(?:\n\s*;\s*end|\n\s*db\s+0)',
        asm, re.DOTALL
    )
    if tmhm:
        for m in re.findall(r'[A-Z][A-Z0-9_]+', tmhm.group(1)):
            mapped = MOVE_NAME_MAP.get(m)
            if mapped:
                moves.add(mapped)
            elif m != 'NO_MOVE':
                unmapped.add(m)

    if unmapped:
        print(f"  [unmapped TM/HM for {showdown_name}]: {unmapped}")

    return moves


def parse_learnset_moves(asm, showdown_name):
    """Extract level-up moves from learnset ASM"""
    moves = set()
    unmapped = set()

    for m in re.findall(r'db\s+\d+,\s*([\w_]+)', asm):
        mapped = MOVE_NAME_MAP.get(m)
        if mapped:
            moves.add(mapped)
        elif m != 'NO_MOVE':
            unmapped.add(m)

    if unmapped:
        print(f"  [unmapped learnset for {showdown_name}]: {unmapped}")

    return moves


def get_tiers():
    """Parse tier tags from Showdown's gen1 formats-data.ts"""
    path = os.path.join(SHOWDOWN_PATH, "data/mods/gen1/formats-data.ts")
    tiers = {}
    try:
        with open(path) as f:
            content = f.read()
        for match in re.finditer(
            r'(\w+):\s*\{[^}]*tier:\s*"([^"]+)"', content, re.DOTALL
        ):
            tiers[match.group(1)] = match.group(2)
    except Exception as e:
        print(f"ERROR reading tiers: {e}")
    return tiers


# =============================================================================
# MAIN BUILD
# =============================================================================

def build_gen1_data():
    """Fetch all 151 Pokemon from pokered and tag with Showdown tiers"""

    print("Loading tier data from Showdown...")
    tiers = get_tiers()
    print(f"Found {len(tiers)} tiered Pokemon\n")

    data = {}

    for showdown_name in ALL_POKEMON:
        tier = tiers.get(showdown_name, "Untiered")

        if tier == "Illegal":
            print(f"Skipping {get_display_name(showdown_name)} (Illegal)")
            continue

        display_name = get_display_name(showdown_name)
        print(f"Fetching {display_name} [{tier}]...")

        base_asm = fetch_url(POKERED_BASE_STATS.format(showdown_name))
        if not base_asm:
            print(f"  ERROR: Could not fetch base stats for {showdown_name}")
            continue

        moves = parse_base_stats_moves(base_asm, showdown_name)

        learnset_asm = fetch_url(POKERED_LEARNSETS.format(showdown_name))
        if learnset_asm:
            moves |= parse_learnset_moves(learnset_asm, showdown_name)

        moves -= FORMAT_BANLIST

        data[showdown_name] = {
            "name":  display_name,
            "tier":  tier,
            "moves": sorted(moves)
        }

        print(f"  ✓ {len(moves)} moves")

    return data


# =============================================================================
# PUBLIC API
# =============================================================================

def load_format_data(format_name="OU", path="gen1_data.json"):
    """
    Load gen1_data.json and return only Pokemon legal in the given format.

    Examples:
        load_format_data("OU")   → 12 OU Pokemon
        load_format_data("UU")   → UU Pokemon
        load_format_data("LC")   → LC Pokemon
    """
    allowed_tiers = FORMAT_INCLUDES.get(format_name, {format_name})
    with open(path) as f:
        all_data = json.load(f)
    return {k: v for k, v in all_data.items() if v["tier"] in allowed_tiers}


def build_prompt_context(data):
    """Build compact move pool string for LLM prompt injection"""
    lines = ["VIABLE POKEMON AND THEIR LEGAL MOVES:"]
    for internal, info in data.items():
        lines.append(f"{info['name']}: {', '.join(info['moves'])}")
    return "\n".join(lines)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Gen 1 Data Builder")
    print("Source of truth: github.com/pret/pokered")
    print("=" * 60 + "\n")

    data = build_gen1_data()

    print(f"\n{'=' * 60}")
    print(f"Total: {len(data)} Pokemon fetched\n")

    tier_counts = Counter(v["tier"] for v in data.values())
    for tier, count in sorted(tier_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {tier:8s}: {count}")

    print()
    for check in ['tauros', 'starmie', 'alakazam', 'nidoranf', 'mrmime']:
        if check in data:
            d = data[check]
            print(f"{d['name']} [{d['tier']}] — {len(d['moves'])} moves")
            print(f"  {', '.join(d['moves'])}\n")

    with open("gen1_data.json", "w") as f:
        json.dump(data, f, indent=2)
    print("Saved to gen1_data.json")

    for fmt in ["OU", "UU", "LC"]:
        filtered = load_format_data(fmt)
        print(f"{fmt}: {len(filtered)} Pokemon — {', '.join(sorted(filtered.keys()))}")
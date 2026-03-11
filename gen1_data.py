import re
import json
import os

# Path to your Showdown install
SHOWDOWN_PATH = os.path.expanduser("~/pokemon-showdown")

# All 151 Gen 1 Pokemon (internal Showdown names, lowercase no spaces)
GEN1_POKEMON = [
    'bulbasaur','ivysaur','venusaur','charmander','charmeleon','charizard',
    'squirtle','wartortle','blastoise','caterpie','metapod','butterfree',
    'weedle','kakuna','beedrill','pidgey','pidgeotto','pidgeot','rattata',
    'raticate','spearow','fearow','ekans','arbok','pikachu','raichu',
    'sandshrew','sandslash','nidoranf','nidorina','nidoqueen','nidoranm',
    'nidorino','nidoking','clefairy','clefable','vulpix','ninetales',
    'jigglypuff','wigglytuff','zubat','golbat','oddish','gloom','vileplume',
    'paras','parasect','venonat','venomoth','diglett','dugtrio','meowth',
    'persian','psyduck','golduck','mankey','primeape','growlithe','arcanine',
    'poliwag','poliwhirl','poliwrath','abra','kadabra','alakazam','machop',
    'machoke','machamp','bellsprout','weepinbell','victreebel','tentacool',
    'tentacruel','geodude','graveler','golem','ponyta','rapidash','slowpoke',
    'slowbro','magnemite','magneton','farfetchd','doduo','dodrio','seel',
    'dewgong','grimer','muk','shellder','cloyster','gastly','haunter','gengar',
    'onix','drowzee','hypno','krabby','kingler','voltorb','electrode',
    'exeggcute','exeggutor','cubone','marowak','hitmonlee','hitmonchan',
    'lickitung','koffing','weezing','rhyhorn','rhydon','chansey','tangela',
    'kangaskhan','horsea','seadra','goldeen','seaking','staryu','starmie',
    'mrmime','scyther','jynx','electabuzz','magmar','pinsir','tauros',
    'magikarp','gyarados','lapras','ditto','eevee','vaporeon','jolteon',
    'flareon','porygon','omanyte','omastar','kabuto','kabutops','aerodactyl',
    'snorlax','articuno','zapdos','moltres','dratini','dragonair','dragonite',
    'mewtwo','mew'
]

# FIX 1: Use a set for exact membership checks, not a string for substring matching
OU_TIERS = {"OU"}

# Moves banned by Gen 1 OU format clauses
FORMAT_BANLIST = {
    'dig',
    'fly',
    'doubleteam',
    'minimize',
    'fissure',
    'guillotine',
    'horndrill',
}

KNOWN_EXCEPTIONS = {
    'exeggutor': {'confusion'},
}

# Moves that exist in Gen 1 (move.gen == 1) but were NOT available in RBY —
# they only became learnable via Gen 2 tutors, then entered Showdown's
# learnset data via the Gen 7 move reminder for VC-transferred Pokemon.
# Cross-referenced against pokered's tm_hm_learnsets and evos_moves.
GEN2_TUTOR_ONLY_MOVES = {
    'headbutt',     # Gen 2 move tutor (Ilex Forest), not in any RBY TM/learnset
    'confuseray',   # Gen 2 move tutor, not in any RBY TM/learnset
    'rollout',      # Gen 2 move tutor
    'dynamicpunch', # Gen 2 TM, not in RBY
    'icy wind',     # Gen 2 move tutor
    'icywind',      # Gen 2 move tutor
    'snore',        # Gen 2 move tutor
    'swagger',      # Gen 2 move tutor
    'sleeptalk',    # Gen 2 TM
    'attract',      # Gen 2 TM
    'thief',        # Gen 2 TM
    'steelwing',    # Gen 2 TM
    'curse',        # Gen 2 TM
}

def get_move_gens(moves_path):
    """
    Parse moves.ts to get each move's num, then compute gen using
    the same logic as sim/dex-moves.ts.
    """
    with open(moves_path) as f:
        lines = f.readlines()

    move_gens = {}
    current_move = None

    for line in lines:
        stripped = line.strip()

        move_match = re.match(r'^(\w+): \{$', stripped)
        if move_match:
            current_move = move_match.group(1)
            continue

        if current_move:
            num_match = re.match(r'num: (-?\d+),', stripped)
            if num_match:
                num = int(num_match.group(1))
                # FIX 2: Mirror dex-moves.ts exactly.
                # Previous code had Gen4 >= 294 (wrong) and was missing Gen 9.
                # Actual boundaries from sim/dex-moves.ts:
                if num >= 920:   gen = 9  # was missing entirely
                elif num >= 622: gen = 8  # was labelled gen=9 before
                elif num >= 560: gen = 7  # was labelled gen=8 before (cascade)
                elif num >= 468: gen = 6
                elif num >= 355: gen = 5  # was >= 294, which is wrong
                elif num >= 252: gen = 4  # was labelled gen=5 before
                elif num >= 166: gen = 3  # was labelled gen=4 before
                elif num >= 166: gen = 2  # correct boundary, but unreachable due to above bug
                elif num >= 1:   gen = 1
                else:            gen = 99
                move_gens[current_move] = gen
                current_move = None

    return move_gens


def get_ou_pool(formats_data_path):
    """Parse formats-data.ts to get OU eligible Pokemon"""
    with open(formats_data_path) as f:
        content = f.read()

    # FIX: Use re.DOTALL so \s* matches across newlines.
    # The gen1 mod formats-data.ts uses multi-line entries:
    #   tauros: {
    #       tier: "OU",    <- newline between { and tier:
    #   },
    # The original \s* without DOTALL only matches same-line whitespace.
    entries = re.findall(r'(\w+): \{[^}]*?tier: "([^"]+)"', content, re.DOTALL)
    tiered = {name: tier for name, tier in entries}

    ou_pool = []
    for pokemon in GEN1_POKEMON:
        tier = tiered.get(pokemon, "")
        if tier in OU_TIERS:
            ou_pool.append(pokemon)
    return ou_pool


def is_gen1_legal(move_name, codes_raw, move_gens, pokemon_name=None):
    """
    A move is Gen 1 legal if:
    1. move.gen <= 1
    2. Has 7V source (proves VC origin)
    3. Is NOT exclusively a Gen 2 tutor move that was never in RBY cartridge data
    """
    move_gen = move_gens.get(move_name, 99)
    if move_gen > 1:
        return False

    if pokemon_name and move_name in KNOWN_EXCEPTIONS.get(pokemon_name, set()):
        return False

    # Block moves that only entered Gen 1 Pokemon learnsets via Gen 2 tutors.
    # These appear in Showdown's learnset with 7V but were never in pokered's
    # TM/HM bitmasks or level-up tables.
    if move_name in GEN2_TUTOR_ONLY_MOVES:
        return False

    codes = [c.strip().strip('"') for c in codes_raw.split(',')]

    # 7V is the primary proof of RBY cartridge origin for VC-transferred Pokemon.
    # Most Gen 1 TM moves in Showdown's data only have 7V, not explicit 1M codes.
    if '7V' in codes:
        return True

    # Fallback: explicit Gen 1 source
    for code in codes:
        if code and code[0] == '1' and len(code) >= 2 and code[1] in ('L', 'M'):
            return True

    return False


def get_gen1_learnsets(learnsets_path, ou_pool, move_gens):
    """
    Parse learnsets.ts line by line to extract Gen 1 legal moves.
    """
    ou_set = set(ou_pool)
    learnsets = {p: [] for p in ou_pool}

    with open(learnsets_path) as f:
        lines = f.readlines()

    current_pokemon = None
    in_learnset = False

    for line in lines:
        stripped = line.rstrip('\n')
        content = stripped.lstrip('\t')
        tabs = len(stripped) - len(content)
        content = content.strip()

        if tabs == 1 and re.match(r'^(\w+): \{$', content):
            name = re.match(r'^(\w+)', content).group(1)
            current_pokemon = name if name in ou_set else None
            in_learnset = False
            continue

        if tabs == 2 and current_pokemon and content == "learnset: {":
            in_learnset = True
            continue

        if tabs == 2 and in_learnset and content in ("},", "}"):
            in_learnset = False
            continue

        if tabs == 1 and content in ("},", "}"):
            current_pokemon = None
            in_learnset = False
            continue

        if tabs == 3 and in_learnset and current_pokemon:
            move_match = re.match(r'^(\w+): \[([^\]]+)\]', content)
            if move_match:
                move_name = move_match.group(1)
                codes_raw = move_match.group(2)
                if (is_gen1_legal(move_name, codes_raw, move_gens, current_pokemon)
                    and move_name not in FORMAT_BANLIST):
                    learnsets[current_pokemon].append(move_name)

    return learnsets


def format_pokemon_name(internal_name):
    special_cases = {
        'nidoranf': 'Nidoran-F',
        'nidoranm': 'Nidoran-M',
        'mrmime': 'Mr. Mime',
        'farfetchd': "Farfetch'd",
    }
    if internal_name in special_cases:
        return special_cases[internal_name]
    return internal_name.capitalize()


def load_gen1_ou_data():
    # FIX 4: Use the main formats-data.ts, not the gen1 mod overlay.
    # data/mods/gen1/formats-data.ts contains gen1-relative tiers (e.g. bulbasaur: LC)
    # which are not the same as the Smogon OU tier strings we're filtering on.
    formats_path = os.path.join(SHOWDOWN_PATH, "data/mods/gen1/formats-data.ts")
    learnsets_path = os.path.join(SHOWDOWN_PATH, "data/learnsets.ts")
    moves_path = os.path.join(SHOWDOWN_PATH, "data/moves.ts")

    print("Parsing move generations from moves.ts...")
    move_gens = get_move_gens(moves_path)
    gen1_moves = [m for m, g in move_gens.items() if g == 1]
    print(f"Found {len(gen1_moves)} Gen 1 native moves\n")

    ou_pool = get_ou_pool(formats_path)
    print(f"Found {len(ou_pool)} OU Pokemon: {', '.join(ou_pool)}\n")

    learnsets = get_gen1_learnsets(learnsets_path, ou_pool, move_gens)

    data = {}
    for pokemon in ou_pool:
        moves = learnsets.get(pokemon, [])
        if moves:
            data[pokemon] = {
                "name": format_pokemon_name(pokemon),
                "moves": moves
            }
        else:
            print(f"WARNING: No Gen 1 moves found for {pokemon}")

    return data


def build_prompt_context(data):
    lines = ["GEN 1 OU VIABLE POKEMON AND THEIR LEGAL MOVES:"]
    lines.append("(Only use Pokemon and moves from this list)\n")
    for internal_name, info in data.items():
        moves_display = ", ".join(info["moves"])
        lines.append(f"{info['name']}: {moves_display}")
    return "\n".join(lines)


if __name__ == "__main__":
    print("Loading Gen 1 OU data from Showdown install...")
    data = load_gen1_ou_data()

    print(f"\nOU Pool: {len(data)} Pokemon with moves\n")
    for internal, info in list(data.items())[:3]:
        print(f"{info['name']}: {len(info['moves'])} legal moves")
        print(f"  Moves: {', '.join(info['moves'])}")
        print()

    with open("gen1_ou_data.json", "w") as f:
        json.dump(data, f, indent=2)
    print("\nSaved to gen1_ou_data.json")

    context = build_prompt_context(data)
    tokens_estimate = len(context.split()) * 1.3
    print(f"Prompt context: ~{int(tokens_estimate)} tokens")


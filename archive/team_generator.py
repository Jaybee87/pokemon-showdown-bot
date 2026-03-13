"""
team_generator.py
=================
Anchor-based team builder for Gen 1 Pokemon Showdown.

Philosophy:
- Human (or config) picks an anchor Pokemon
- Python analyses weaknesses and finds complementary teammates
- LLM picks moves for each Pokemon ONE AT A TIME from that Pokemon's legal list
- Battle feedback is parsed into per-Pokemon move constraints and passed into each prompt
- Small focused prompts + real battle data = dramatically better move selection

Usage:
    python3 team_generator.py --format OU --anchor gengar
    python3 team_generator.py --format OU  # random anchor from pool
"""

import re
import random
import argparse
from collections import defaultdict

from config import LLM_MODEL
from gen1_data import load_format_data
from gen1_engine import (
    TYPES, get_weaknesses, get_strengths,
    get_weaknesses_summary, get_immunities, get_resistances
)
from llm_bridge import call_llm, strip_think_tags, parse_move_picks


# =============================================================================
# KNOWN-BAD MOVES — hard-banned based on battle data and Gen 1 competitive knowledge
# These consistently produce DEAD WEIGHT verdicts or are passively useless.
# =============================================================================

GLOBALLY_BANNED_MOVES = {
    'bide',        # 2-turn charge, returns damage — never contributes offensively
    'mimic',       # copies opponent's last move — random and unreliable
    'metronome',   # random move — produces phantom move entries in tracking
    'counter',     # only works vs normal/fighting — useless on most Pokemon
    'reflect',     # raises defence — passive waste of a moveslot
    'whirlwind',   # forces switch — does nothing useful
    'flash',       # lowers accuracy — passive, no damage or status
    'screech',     # lowers defence — wastes a turn
    'leer',        # lowers defence — wastes a turn
    'smokescreen', # lowers accuracy — passive
    'sandattack',  # lowers accuracy — passive
    'growl',       # lowers attack — passive
    'tailwhip',
    'disable',     # situational and unreliable
    'supersonic',  # 55% accuracy confusion — too unreliable
    'psywave',     # variable damage based on level — inconsistent in RBY
    'eggbomb',     # 75% accuracy, no secondary effect, outclassed by hyperbeam
    'barrage',     # multi-hit, outclassed
    'stomp',       # weak, flinch requires paralysis first
    'doubleedge',  # recoil move — suicide on fragile Pokemon
    'submission',  # recoil + fighting — rarely optimal
    'takedown',    # recoil + weak — rarely optimal
    'tackle',      # weakest normal move — never competitive
    'scratch',
    'pound',
    'peck',
    'vicegrip',
    'bind',        # trapping moves — bugged in Gen 1, not useful
    'wrap',
    'clamp',
    'firespin',
    'razorwind',   # 2-turn charge, no status — outclassed by hyper beam
    'skyattack',   # 2-turn charge — outclassed
}


# =============================================================================
# COMPETITIVE MOVE HINTS — per-Pokemon guidance grounded in Gen 1 OU knowledge
# must_consider: moves that belong on almost every set for this Pokemon
# good: situationally strong
# avoid: moves that look valid but rarely help competitively
# note: context the LLM needs to make good decisions
# =============================================================================

COMPETITIVE_HINTS = {
    'gengar': {
        'must_consider': ['thunderbolt', 'icebeam', 'nightshade', 'hypnosis', 'megadrain'],
        'good':          ['confuseray', 'toxic', 'explosion'],
        'avoid':         ['lick', 'counter', 'doubleedge', 'selfdestruct'],
        'note':          'Gengar is Ghost/Poison — immune to Normal and Fighting moves. '
                         'Lick has only 30 base power, which is very weak. '
                         'Hypnosis + Dreameater is a strong combo if both fit the moveset. '
                         'Thunderbolt and Ice Beam give excellent type coverage.',
    },
    'alakazam': {
        'must_consider': ['psychic', 'recover', 'thunderwave', 'seismictoss'],
        'good':          ['icebeam', 'thunderbolt', 'fireblast'],
        'avoid':         ['confusion', 'metronome', 'teleport'],
        'note':          'Alakazam is the premier Psychic attacker in Gen 1. '
                         'Psychic is its primary STAB. Recover keeps it alive all match. '
                         'Thunder Wave cripples fast threats. '
                         'Seismic Toss deals consistent damage equal to level regardless of its low Attack.',
    },
    'exeggutor': {
        'must_consider': ['psychic', 'sleeppowder', 'megadrain', 'hyperbeam'],
        'good':          ['explosion', 'stunspore'],
        'avoid':         ['barrage', 'eggbomb', 'bide'],
        'note':          'Exeggutor is a Grass/Psychic powerhouse. '
                         'Sleep Powder is one of the most dangerous moves in Gen 1 — always consider it. '
                         'Explosion is a nuclear last resort. Mega Drain provides recovery. '
                         'Psychic and Hyper Beam are its main damage dealers.',
    },
    'starmie': {
        'must_consider': ['surf', 'thunderbolt', 'psychic', 'recover'],
        'good':          ['icebeam', 'thunderwave'],
        'avoid':         ['reflect', 'bide', 'watergun'],
        'note':          'Starmie is a versatile Water/Psychic attacker with great speed and coverage. '
                         'Surf is its reliable Water STAB. Recover gives longevity. '
                         'Thunderbolt covers other Water types. Psychic is strong STAB. '
                         'This is one of the best movesets in Gen 1 — stick to it.',
    },
    'chansey': {
        'must_consider': ['softboiled', 'thunderwave', 'icebeam', 'seismictoss'],
        'good':          ['thunderbolt', 'counter'],
        'avoid':         ['megapunch', 'reflect', 'bide'],
        'note':          'Chansey is a special wall with enormous HP. '
                         'Soft-Boiled is essential — it keeps Chansey at full health all match. '
                         'Thunder Wave cripples fast sweepers. '
                         'Seismic Toss deals consistent damage despite Chansey\'s low Attack. '
                         'Ice Beam provides reliable damage and freezes.',
    },
    'tauros': {
        'must_consider': ['bodyslam', 'hyperbeam', 'earthquake', 'blizzard'],
        'good':          ['fireblast'],
        'avoid':         ['mimic', 'bide', 'stomp'],
        'note':          'Tauros is the single strongest Pokemon in Gen 1 OU. '
                         'Body Slam is its primary STAB with paralysis chance. '
                         'Hyper Beam is its finisher — massive damage. '
                         'Earthquake and Blizzard give coverage against everything. '
                         'This is essentially the optimal Tauros moveset.',
    },
    'snorlax': {
        'must_consider': ['bodyslam', 'earthquake', 'icebeam', 'selfdestruct'],
        'good':          ['hyperbeam', 'amnesia', 'rest'],
        'avoid':         ['reflect', 'bide'],
        'note':          'Snorlax is a bulky physical attacker. Body Slam is reliable STAB with paralysis. '
                         'Earthquake provides Ground coverage. '
                         'Self-Destruct is a nuclear option when it\'s about to faint. '
                         'Amnesia + Rest is a win condition if you can set up safely.',
    },
    'zapdos': {
        'must_consider': ['thunderbolt', 'drillpeck', 'thunderwave', 'agility'],
        'good':          ['icebeam'],
        'avoid':         ['thunder', 'skyattack', 'razorwind', 'reflect', 'whirlwind'],
        'note':          'Zapdos is an Electric/Flying attacker. '
                         'Thunderbolt is its STAB — reliable and strong. '
                         'DO NOT use Thunder — it only has 70% accuracy and is not worth the misses. '
                         'Drill Peck is its best Flying STAB. '
                         'Thunder Wave cripples opponents. Agility makes it a fast sweeper.',
    },
    'jolteon': {
        'must_consider': ['thunderbolt', 'thunderwave', 'pinmissile', 'doublekick'],
        'good':          ['icebeam', 'bodyslam'],
        'avoid':         ['hyperbeam', 'tackle', 'substitute'],
        'note':          'Jolteon is the fastest Electric type in Gen 1. '
                         'Thunderbolt is its primary STAB. '
                         'Thunder Wave support cripples faster threats. '
                         'Pin Missile and Double Kick provide coverage against Psychic types, '
                         'which resist Electric.',
    },
    'cloyster': {
        'must_consider': ['blizzard', 'icebeam', 'surf', 'explosion'],
        'good':          ['thunderwave', 'clamp'],
        'avoid':         ['aurorabeam', 'supersonic', 'substitute', 'bide'],
        'note':          'Cloyster is a powerful Ice/Water attacker and threat. '
                         'Blizzard is its primary STAB — high power with freeze chance. '
                         'Surf provides Water coverage. '
                         'Explosion is Cloyster\'s signature threat — massive damage as a last resort. '
                         'Aurora Beam is significantly weaker than Blizzard — avoid it.',
    },
    'rhydon': {
        'must_consider': ['earthquake', 'rockslide', 'bodyslam', 'substitute'],
        'good':          ['surf'],
        'avoid':         ['bide', 'counter', 'doubleedge'],
        'note':          'Rhydon is a powerful Ground/Rock attacker. '
                         'Earthquake is its primary STAB and one of the best moves in Gen 1. '
                         'Rock Slide provides secondary STAB coverage. '
                         'Body Slam has paralysis chance. '
                         'Surf is an unexpected coverage move that beats other Rhydon.',
    },
    'jynx': {
        'must_consider': ['lovelykiss', 'blizzard', 'psychic', 'dreameater'],
        'good':          ['icebeam', 'bodyslam'],
        'avoid':         ['psywave', 'bide'],
        'note':          'Jynx has the most reliable sleep move in Gen 1 — Lovely Kiss at 75% accuracy. '
                         'Blizzard is its powerful Ice STAB. '
                         'Dream Eater pairs with sleep for healing while the opponent is sleeping. '
                         'Psychic is secondary STAB.',
    },
}


# =============================================================================
# POKEMON TYPES
# =============================================================================

POKEMON_TYPES = {
    'alakazam':  ('psychic', None),
    'cloyster':  ('water', 'ice'),
    'gengar':    ('ghost', 'poison'),
    'exeggutor': ('grass', 'psychic'),
    'rhydon':    ('ground', 'rock'),
    'chansey':   ('normal', None),
    'starmie':   ('water', 'psychic'),
    'jynx':      ('ice', 'psychic'),
    'tauros':    ('normal', None),
    'jolteon':   ('electric', None),
    'snorlax':   ('normal', None),
    'zapdos':    ('electric', 'flying'),
    'articuno':  ('ice', 'flying'),
    'clefable':  ('normal', None),
    'dodrio':    ('normal', 'flying'),
    'dragonite': ('dragon', 'flying'),
    'dugtrio':   ('ground', None),
    'electabuzz':('electric', None),
    'gyarados':  ('water', 'flying'),
    'haunter':   ('ghost', 'poison'),
    'hypno':     ('psychic', None),
    'kangaskhan':('normal', None),
    'lapras':    ('water', 'ice'),
    'moltres':   ('fire', 'flying'),
    'ninetales': ('fire', None),
    'persian':   ('normal', None),
    'raichu':    ('electric', None),
    'rapidash':  ('fire', None),
    'slowbro':   ('water', 'psychic'),
    'dragonair': ('dragon', None),
    'kadabra':   ('psychic', None),
    'wartortle': ('water', None),
    'arcanine':  ('fire', None),
    'blastoise': ('water', None),
    'charizard': ('fire', 'flying'),
    'clefairy':  ('normal', None),
    'dewgong':   ('water', 'ice'),
    'doduo':     ('normal', 'flying'),
    'electrode': ('electric', None),
    'flareon':   ('fire', None),
    'golduck':   ('water', None),
    'golem':     ('rock', 'ground'),
    'graveler':  ('rock', 'ground'),
    'hitmonlee': ('fighting', None),
    'hitmonchan':('fighting', None),
    'kabutops':  ('rock', 'water'),
    'kingler':   ('water', None),
    'lickitung': ('normal', None),
    'machamp':   ('fighting', None),
    'magmar':    ('fire', None),
    'magneton':  ('electric', None),
    'marowak':   ('ground', None),
    'mrmime':    ('psychic', None),
    'nidoking':  ('poison', 'ground'),
    'nidoqueen': ('poison', 'ground'),
    'omastar':   ('rock', 'water'),
    'parasect':  ('bug', 'grass'),
    'pinsir':    ('bug', None),
    'poliwrath': ('water', 'fighting'),
    'porygon':   ('normal', None),
    'primeape':  ('fighting', None),
    'scyther':   ('bug', 'flying'),
    'seaking':   ('water', None),
    'tangela':   ('grass', None),
    'tentacruel':('water', 'poison'),
    'vaporeon':  ('water', None),
    'venomoth':  ('bug', 'poison'),
    'venusaur':  ('grass', 'poison'),
    'victreebel':('grass', 'poison'),
    'vileplume': ('grass', 'poison'),
    'weezing':   ('poison', None),
}


# =============================================================================
# WEAKNESS ANALYSIS
# =============================================================================

def get_team_weaknesses(team_names):
    weakness_counts = {t: 0 for t in TYPES}
    for name in team_names:
        types = POKEMON_TYPES.get(name)
        if not types:
            continue
        type1, type2 = types
        weaknesses = get_weaknesses(type1, type2)
        for i, mult in enumerate(weaknesses):
            if mult > 1:
                weakness_counts[TYPES[i]] += mult - 1
    return weakness_counts


def find_best_complement(candidate_pool, current_team):
    team_weak  = get_team_weaknesses(current_team)
    best_pick  = None
    best_score = -1

    for name in candidate_pool:
        if name in current_team:
            continue
        types = POKEMON_TYPES.get(name)
        if not types:
            continue
        type1, type2 = types
        score      = 0
        weaknesses = get_weaknesses(type1, type2)
        for i, mult in enumerate(weaknesses):
            team_vulnerability = team_weak.get(TYPES[i], 0)
            if mult < 1 and team_vulnerability > 0:
                score += team_vulnerability * (1 - mult)
            if mult > 1:
                score -= team_vulnerability * 0.5
        if score > best_score:
            best_score = score
            best_pick  = name

    return best_pick


def format_weaknesses(weak_summary):
    """Convert get_weaknesses_summary dict to clean readable string."""
    if not weak_summary:
        return "none"
    if isinstance(weak_summary, dict):
        parts = [f"{t} x{int(round(float(v)))}" for t, v in sorted(weak_summary.items())]
        return ", ".join(parts) if parts else "none"
    return str(weak_summary)


def build_team_composition(anchor, format_data):
    pool = list(format_data.keys())
    team = [anchor]

    print(f"\n🎯 Anchor: {format_data[anchor]['name']}")
    types = POKEMON_TYPES.get(anchor, ('normal', None))
    weak  = format_weaknesses(get_weaknesses_summary(types[0], types[1]))
    print(f"   Type weaknesses: {weak}")

    for slot in range(5):
        remaining = [p for p in pool if p not in team]
        if not remaining:
            break
        pick = find_best_complement(remaining, team)
        if not pick:
            pick = random.choice(remaining)
        team.append(pick)
        types = POKEMON_TYPES.get(pick, ('normal', None))
        weak  = format_weaknesses(get_weaknesses_summary(types[0], types[1]))
        print(f"   Slot {slot + 2}: {format_data[pick]['name']} | type weaknesses: {weak}")

    return team


# =============================================================================
# FEEDBACK PARSING
# =============================================================================

def parse_move_feedback(battle_feedback):
    """
    Parse move performance section from battle_runner output.
    Returns {pokemon_name: {move_name: {'verdict': ..., 'summary': ...}}}
    When a move appears in multiple sections (vs random + self-play),
    keeps the worst verdict (DEAD WEIGHT > LOW ACCURACY > RELIABLE).
    """
    move_data       = defaultdict(dict)
    current_pokemon = None
    in_move_section = False

    VERDICT_RANK = {'DEAD WEIGHT': 2, 'LOW ACCURACY': 1, 'RELIABLE': 0, None: -1}

    for line in battle_feedback.split('\n'):
        if 'Move performance:' in line:
            in_move_section = True
            continue
        if 'Pokemon performance:' in line:
            in_move_section = False
            continue

        if in_move_section:
            poke_match = re.match(r'^\s{2}(\w+):\s*$', line)
            if poke_match:
                current_pokemon = poke_match.group(1).lower()
                continue

            move_match = re.match(r'^\s{4}(\w+):\s+(.+)$', line)
            if move_match and current_pokemon:
                move    = move_match.group(1).lower()
                summary = move_match.group(2)

                verdict = None
                if 'DEAD WEIGHT' in summary:
                    verdict = 'DEAD WEIGHT'
                elif 'LOW ACCURACY' in summary:
                    verdict = 'LOW ACCURACY'
                elif '✓ RELIABLE' in summary:
                    verdict = 'RELIABLE'

                if move not in move_data[current_pokemon]:
                    move_data[current_pokemon][move] = {'verdict': verdict, 'summary': summary}
                else:
                    existing = move_data[current_pokemon][move]['verdict']
                    if VERDICT_RANK.get(verdict, -1) > VERDICT_RANK.get(existing, -1):
                        move_data[current_pokemon][move]['verdict'] = verdict

    return dict(move_data)


def distil_feedback(battle_feedback):
    """
    Parse battle feedback into:
      1. Pokemon-level constraints (replace/keep lists)
      2. Per-Pokemon move intelligence for prompt injection

    Returns (constraint_parts, move_intelligence)
      constraint_parts:  list of strings for console display
      move_intelligence: {pokemon_name: {dead_weight: [...], low_accuracy: [...], reliable: [...]}}
    """
    avoid  = []
    prefer = []

    for line in battle_feedback.split('\n'):
        match = re.match(r'\s+(\w+):\s+fainted\s+(\d+)%', line)
        if match:
            name      = match.group(1)
            faint_pct = int(match.group(2))
            if faint_pct == 100:
                avoid.append(name)
            elif faint_pct <= 30:
                prefer.append(name)

    constraint_parts = []
    if avoid:
        constraint_parts.append(f"Replace these (fainted every battle): {', '.join(set(avoid))}")
    if prefer:
        constraint_parts.append(f"Keep these (strong performers): {', '.join(set(prefer))}")

    raw_move_data    = parse_move_feedback(battle_feedback)
    move_intelligence = {}

    for pokemon, moves in raw_move_data.items():
        intel = {'dead_weight': [], 'low_accuracy': [], 'reliable': []}
        for move, data in moves.items():
            v = data['verdict']
            if v == 'DEAD WEIGHT':
                intel['dead_weight'].append(move)
            elif v == 'LOW ACCURACY':
                intel['low_accuracy'].append(move)
            elif v == 'RELIABLE':
                intel['reliable'].append(move)
        if any(intel.values()):
            move_intelligence[pokemon] = intel

    return constraint_parts, move_intelligence


# =============================================================================
# MOVE SELECTION — ONE POKEMON AT A TIME WITH BATTLE INTELLIGENCE
# =============================================================================

def pick_moves_for_pokemon(
        pokemon_name, display_name, legal_moves,
        role_hint="", move_intel=None, max_retries=5):
    """
    Ask the LLM to pick exactly 4 moves for a single Pokemon.

    Flow:
    1. Filter globally banned moves from legal list
    2. Build competitive context from COMPETITIVE_HINTS
    3. Inject real battle data from move_intel if available
    4. Retry up to max_retries with error feedback
    5. Guided fallback uses must_consider moves first
    """
    # Filter banned moves
    filtered_moves = [
        m for m in legal_moves
        if m.lower().replace(' ', '').replace('-', '') not in GLOBALLY_BANNED_MOVES
    ]
    if len(filtered_moves) < 4:
        filtered_moves = legal_moves  # relax ban if pool becomes too small

    moves_list = ", ".join(sorted(filtered_moves))

    # Build context block
    hints         = COMPETITIVE_HINTS.get(pokemon_name, {})
    context_lines = []

    if hints.get('note'):
        context_lines.append(f"COMPETITIVE CONTEXT: {hints['note']}")

    if hints.get('must_consider'):
        relevant = [m for m in hints['must_consider'] if m in filtered_moves]
        if relevant:
            context_lines.append(f"STRONGLY consider including: {', '.join(relevant)}")

    if hints.get('avoid'):
        relevant_avoid = [m for m in hints['avoid'] if m in filtered_moves]
        if relevant_avoid:
            context_lines.append(f"AVOID these (competitive dead ends): {', '.join(relevant_avoid)}")

    # Inject battle data
    if move_intel:
        if move_intel.get('dead_weight'):
            context_lines.append(
                f"BATTLE DATA — these moves did ZERO damage, status, or healing across "
                f"hundreds of uses. DO NOT pick them: {', '.join(move_intel['dead_weight'])}"
            )
        if move_intel.get('low_accuracy'):
            context_lines.append(
                f"BATTLE DATA — these moves missed frequently: "
                f"{', '.join(move_intel['low_accuracy'])}. "
                f"Only include if the utility (sleep/paralysis) justifies the inaccuracy."
            )
        if move_intel.get('reliable'):
            context_lines.append(
                f"BATTLE DATA — these moves consistently dealt damage or inflicted status: "
                f"{', '.join(move_intel['reliable'])}. These are worth keeping."
            )

    context_block = "\n".join(context_lines)

    prompt = f"""You are a Gen 1 competitive Pokemon move selector with access to real battle performance data.

Pick exactly 4 moves for {display_name} from the legal move list below.

LEGAL MOVES FOR {display_name.upper()} (ONLY use moves from this list):
{moves_list}

{context_block}

RULES:
- Pick EXACTLY 4 moves
- Copy move names EXACTLY as written above
- No duplicate moves
- A strong moveset: 1-2 STAB attacks + 1 coverage move + 1 utility (status or recovery)
- Prioritise moves that deal damage or inflict status — passive moves waste turns
- DO NOT pick moves just because they exist in the list

Respond with ONLY the 4 move names, one per line, no dashes, no numbers, no explanation:
move1
move2
move3
move4"""

    for attempt in range(max_retries):
        raw, err = call_llm(prompt)
        if err:
            print(f"  ✗ {display_name} attempt {attempt+1}: LLM error: {err}")
            continue

        picked = parse_move_picks(raw, filtered_moves, expected_count=4)

        if len(picked) == 4:
            print(f"  ✓ {display_name}: {', '.join(picked)}")
            return picked
        else:
            print(f"  ✗ {display_name} attempt {attempt+1}: got {len(picked)} valid moves ({picked})")
            if attempt < max_retries - 1:
                cleaned = strip_think_tags(raw)
                cleaned = re.sub(r'\*\*|\*|#{1,6}', '', cleaned)
                cleaned = re.sub(r'^[-\d]+[\.\)]\s*', '', cleaned, flags=re.MULTILINE)
                legal_set = set(filtered_moves)
                bad = [
                    l.strip().lower().replace(' ', '').replace('-', '')
                    for l in cleaned.strip().split('\n')
                    if l.strip().lower().replace(' ', '').replace('-', '') not in legal_set
                    and l.strip()
                ]
            if attempt < max_retries - 1:
                bad = [
                    l.strip().lower().replace(' ', '').replace('-', '')
                    for l in raw.strip().split('\n')
                    if l.strip().lower().replace(' ', '').replace('-', '') not in legal_set
                    and l.strip()
                ]
                if bad:
                    prompt += f"\n\nDo NOT use these — not in legal list: {', '.join(bad[:5])}"

    # Guided fallback — prefer must_consider moves over random
    print(f"  ⚠ {display_name}: using guided fallback moves")
    must     = [m for m in hints.get('must_consider', []) if m in filtered_moves]
    fallback = must[:4]
    if len(fallback) < 4:
        remaining = [m for m in sorted(filtered_moves) if m not in fallback]
        fallback += remaining[:4 - len(fallback)]
    return fallback[:4]


# =============================================================================
# TEAM ASSEMBLY
# =============================================================================

def assemble_team(team_composition, format_data, move_intelligence=None):
    """
    Pick moves for each Pokemon in the composition.
    Passes per-Pokemon move intelligence from previous battles into each prompt.
    """
    lines          = []
    move_intel_map = move_intelligence or {}

    for i, pokemon_name in enumerate(team_composition):
        info         = format_data[pokemon_name]
        display_name = info['name']
        legal_moves  = info['moves']

        print(f"\n🎮 Picking moves for {display_name}...")

        types    = POKEMON_TYPES.get(pokemon_name, ('normal', None))
        type_str = '/'.join(t for t in types if t)

        intel = (
            move_intel_map.get(pokemon_name)
            or move_intel_map.get(display_name.lower())
        )
        if intel:
            dead  = intel.get('dead_weight', [])
            good  = intel.get('reliable', [])
            print(f"   📊 Battle data: dead_weight={dead}, reliable={good}")

        moves = pick_moves_for_pokemon(
            pokemon_name, display_name, legal_moves,
            role_hint=type_str,
            move_intel=intel
        )

        lines.append(display_name)
        for move in moves:
            lines.append(f"- {move}")
        if i < len(team_composition) - 1:
            lines.append("")

    return "\n".join(lines)


# =============================================================================
# VALIDATION
# =============================================================================

def validate_team(team_text, format_data, format_name="OU"):
    errors = []

    name_lookup = {}
    for internal, info in format_data.items():
        display = info["name"].lower().replace(' ', '').replace('.', '').replace("'", '')
        name_lookup[display] = {
            "display": info["name"],
            "moves":   set(info["moves"])
        }

    teams           = {}
    current_pokemon = None
    current_moves   = []

    for line in [l.strip() for l in team_text.strip().split('\n') if l.strip()]:
        if line.startswith('-'):
            move = line.lstrip('- ').strip().lower().replace(' ', '').replace('-', '')
            if move:
                current_moves.append(move)
        else:
            if current_pokemon:
                teams[current_pokemon] = current_moves
            current_pokemon = line.lower().replace(' ', '').replace('.', '').replace("'", '')
            current_moves   = []

    if current_pokemon:
        teams[current_pokemon] = current_moves

    if len(teams) != 6:
        errors.append(f"Team has {len(teams)} Pokemon, needs exactly 6")

    for poke_name, moves in teams.items():
        if poke_name not in name_lookup:
            errors.append(f"{poke_name} is not in the {format_name} pool")
            continue
        if len(moves) != 4:
            errors.append(f"{name_lookup[poke_name]['display']} has {len(moves)} moves, needs 4")
        for move in moves:
            if move not in name_lookup[poke_name]['moves']:
                errors.append(
                    f"{name_lookup[poke_name]['display']} cannot learn {move} in {format_name}"
                )
        if len(moves) != len(set(moves)):
            errors.append(f"{name_lookup[poke_name]['display']} has duplicate moves")

    return len(errors) == 0, errors


# =============================================================================
# MAIN GENERATE FUNCTION
# =============================================================================

def generate_team(format_data, format_name="OU", anchor=None, battle_feedback=None):
    pool = list(format_data.keys())

    forced_keep       = []
    forced_replace    = []
    move_intelligence = {}

    if battle_feedback:
        constraint_parts, move_intelligence = distil_feedback(battle_feedback)

        for part in constraint_parts:
            if "Replace" in part:
                forced_replace = list(set(re.findall(r'\b(\w+)\b', part.split(':')[1])))
            if "Keep" in part:
                forced_keep = list(set(re.findall(r'\b(\w+)\b', part.split(':')[1])))

        if constraint_parts:
            print(f"\n📋 Team constraints: {' | '.join(constraint_parts)}")
        if move_intelligence:
            print(f"📊 Move data available for: {', '.join(sorted(move_intelligence.keys()))}")

    # Anchor logic
    if anchor and anchor in format_data and anchor not in forced_replace:
        chosen_anchor = anchor
    elif forced_keep and forced_keep[0] in format_data:
        chosen_anchor = forced_keep[0]
        print(f"   Anchor {anchor} replaced by strong performer: {chosen_anchor}")
    else:
        fallback_pool = [p for p in pool if p not in forced_replace]
        chosen_anchor = random.choice(fallback_pool if fallback_pool else pool)
        print(f"   Anchor {anchor} underperformed — new anchor: {chosen_anchor}")

    available_pool = {k: v for k, v in format_data.items()
                      if k not in forced_replace or k == chosen_anchor}

    team_composition = build_team_composition(chosen_anchor, available_pool)
    print(f"\n📋 Team: {[format_data[p]['name'] for p in team_composition]}")

    print("\n🎯 Selecting moves...")
    team_text = assemble_team(team_composition, format_data, move_intelligence)

    print("\n📋 Generated team:")
    print(team_text)

    is_valid, errors = validate_team(team_text, format_data, format_name)
    if is_valid:
        print("\n✅ Team is valid!")
    else:
        print(f"\n⚠️  Validation issues (non-blocking):")
        for e in errors:
            print(f"   - {e}")

    return team_text


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a Gen 1 Pokemon team")
    parser.add_argument("--format", default="OU", help="Format (OU, UU, LC etc)")
    parser.add_argument("--anchor", default=None, help="Anchor Pokemon (e.g. gengar)")
    args = parser.parse_args()

    print(f"Loading Gen 1 {args.format} data...")
    format_data = load_format_data(args.format)
    print(f"Pool: {len(format_data)} Pokemon")

    team = generate_team(format_data, format_name=args.format, anchor=args.anchor)

    if team:
        filename = f"current_team_{args.format.lower()}.txt"
        with open(filename, "w") as f:
            f.write(team)
        print(f"\nSaved to {filename}")
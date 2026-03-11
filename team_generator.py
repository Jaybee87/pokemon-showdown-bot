import ollama
import json
import re


def load_ou_data(path="gen1_ou_data.json"):
    with open(path) as f:
        return json.load(f)


def build_reminders(errors):
    """Build explicit move reminders from previous validation errors"""
    reminders = []
    for error in errors:
        if "cannot learn" in error:
            match = re.match(r'(\S+) cannot learn (\S+) in Gen 1 OU\. Legal moves are: (.+)', error)
            if match:
                pokemon = match.group(1)
                bad_move = match.group(2)
                legal = match.group(3)
                reminders.append(
                    f"- {pokemon}: '{bad_move}' is ILLEGAL. Legal moves are: {legal}"
                )
    if reminders:
        return "\n".join(reminders)
    return ""


def build_prompt(ou_data, battle_feedback=None, validation_errors=None):
    """
    Build the team generation prompt.
    Battle feedback influences team composition.
    Validation errors reinforce move accuracy.
    These are kept separate so battle feedback doesn't destabilise move selection.
    """

    # Build the viable pool context
    pool_lines = []
    for internal, info in ou_data.items():
        moves = ", ".join(info["moves"])
        pool_lines.append(f"{info['name']}: {moves}")
    pool_text = "\n".join(pool_lines)

    # Battle feedback influences WHICH pokemon to pick
    battle_feedback_text = ""
    if battle_feedback:
        battle_feedback_text = f"""
TEAM BUILDING GUIDANCE FROM PREVIOUS BATTLES:
{battle_feedback}

Use this to decide which Pokemon to include or replace.
Do NOT let this affect your move selection - always use the VIABLE POOL for moves.
"""

    # Validation errors reinforce WHICH moves to use
    validation_reminder_text = ""
    if validation_errors:
        reminders = build_reminders(validation_errors)
        if reminders:
            validation_reminder_text = f"""
MOVES YOU USED INCORRECTLY LAST TIME - DO NOT REPEAT THESE MISTAKES:
{reminders}

Remember: ONLY use moves exactly as listed in the VIABLE POOL below.
"""

    prompt = f"""You are a Pokemon Gen 1 OU team builder. Follow these steps carefully.

STEP 1 - TEAM SELECTION:
Choose exactly 6 different Pokemon from the VIABLE POOL below.
{battle_feedback_text}

STEP 2 - MOVE SELECTION:
For each Pokemon you chose, select exactly 4 moves.
CRITICAL RULES FOR MOVES:
- Copy move names EXACTLY as they appear in the VIABLE POOL - character for character
- Do NOT use any move not explicitly listed for that Pokemon
- Do NOT invent move names or use variations (e.g. if "surf" is listed, do NOT write "hydropump")
- Do NOT add type suffixes (e.g. never write "hiddenpower(ice)")
- Do NOT use moves from other Pokemon's lists
- If you are unsure whether a move is legal, pick a different one from the list
{validation_reminder_text}

VIABLE POOL (ONLY use Pokemon and moves from this list):
{pool_text}

RESPONSE FORMAT:
Do NOT use markdown formatting. No bold, no headers, no asterisks.
Respond with EXACTLY 6 Pokemon in plain text Showdown import format.
Each Pokemon MUST have EXACTLY 4 moves - not 3, not 5, EXACTLY 4.
Count your moves before submitting each Pokemon.

Use exactly this format (X = exactly 4 moves per Pokemon):

PokemonName1
- move1of4
- move2of4
- move3of4
- move4of4

PokemonName2
- move1of4
- move2of4
- move3of4
- move4of4

PokemonName3
- move1of4
- move2of4
- move3of4
- move4of4

PokemonName4
- move1of4
- move2of4
- move3of4
- move4of4

PokemonName5
- move1of4
- move2of4
- move3of4
- move4of4

PokemonName6
- move1of4
- move2of4
- move3of4
- move4of4"""

    return prompt


def clean_team_text(team_text, ou_data):
    """Strip any lines that aren't pokemon names or move lines"""
    valid_names = {info["name"].lower() for info in ou_data.values()}

    cleaned = []
    for line in team_text.strip().split('\n'):
        stripped = line.strip()
        
        # Strip markdown bold/italic formatting
        stripped = stripped.replace('**', '').replace('*', '').strip()
        
        if not stripped:
            cleaned.append('')
            continue
        if stripped.startswith('-'):
            cleaned.append(stripped)
            continue
        normalised = stripped.lower().replace(' ', '').replace('.', '').replace("'", '')
        if any(normalised == n.replace(' ', '').replace('.', '').replace("'", '')
               for n in valid_names):
            cleaned.append(stripped)
            continue
        print(f"  [stripped]: {stripped[:80]}")

    return '\n'.join(cleaned)


def validate_team(team_text, ou_data):
    """
    Validate a generated team against the OU data.
    Returns (is_valid, list of errors)
    """
    errors = []

    # Build lookup by normalised display name
    name_lookup = {}
    for internal, info in ou_data.items():
        display = info["name"].lower().replace(' ', '').replace('.', '').replace("'", '')
        name_lookup[display] = {
            "internal": internal,
            "display": info["name"],
            "moves": set(info["moves"])
        }

    teams = {}
    current_pokemon = None
    current_moves = []

    lines = [l.strip() for l in team_text.strip().split('\n') if l.strip()]

    for line in lines:
        if line.startswith('-'):
            move = line.lstrip('- ').strip().lower().replace(' ', '').replace('-', '')
            current_moves.append(move)
        elif line:
            if current_pokemon:
                teams[current_pokemon] = current_moves
            current_pokemon = line.lower().replace(' ', '').replace('.', '').replace("'", '')
            current_moves = []

    if current_pokemon:
        teams[current_pokemon] = current_moves

    # Validate count
    if len(teams) != 6:
        errors.append(f"Team has {len(teams)} Pokemon, needs exactly 6")

    for poke_name, moves in teams.items():
        if poke_name not in name_lookup:
            errors.append(f"{poke_name} is not in the Gen 1 OU viable pool")
            continue

        if len(moves) != 4:
            errors.append(
                f"{name_lookup[poke_name]['display']} has {len(moves)} moves, needs exactly 4"
            )

        legal_moves = name_lookup[poke_name]["moves"]
        for move in moves:
            if move not in legal_moves:
                errors.append(
                    f"{name_lookup[poke_name]['display']} cannot learn {move} in Gen 1 OU. "
                    f"Legal moves are: {', '.join(sorted(legal_moves))}"
                )

        if len(moves) != len(set(moves)):
            errors.append(
                f"{name_lookup[poke_name]['display']} has duplicate moves"
            )

    poke_names = list(teams.keys())
    if len(poke_names) != len(set(poke_names)):
        errors.append("Team has duplicate Pokemon")

    return len(errors) == 0, errors


def generate_team(ou_data, battle_feedback=None, max_retries=5):
    """
    Ask model to generate a team.
    Battle feedback influences team composition.
    Validation errors accumulate across retries to reinforce move accuracy.
    """
    last_errors = []

    for attempt in range(max_retries):
        print(f"\n🤖 Generating team (attempt {attempt + 1}/{max_retries})...")

        prompt = build_prompt(
            ou_data,
            battle_feedback=battle_feedback,
            validation_errors=last_errors
        )

        response = ollama.chat(
            model="deepseek-r1:7b",
            messages=[{"role": "user", "content": prompt}]
        )

        team_text = response['message']['content'].strip()
        team_text = clean_team_text(team_text, ou_data)

        print("\nGenerated team:")
        print(team_text)

        is_valid, errors = validate_team(team_text, ou_data)

        if is_valid:
            print("\n✅ Team is valid!")
            return team_text
        else:
            print(f"\n❌ Validation failed:")
            for error in errors:
                print(f"  - {error}")
            last_errors = errors

    print("\n⚠️ Could not generate valid team after max retries")
    return None


if __name__ == "__main__":
    print("Loading Gen 1 OU data...")
    ou_data = load_ou_data()

    team = generate_team(ou_data)

    if team:
        print("\n🏆 Final team:")
        print(team)

        with open("current_team.txt", "w") as f:
            f.write(team)
        print("\nSaved to current_team.txt")
import ollama
import re

from gen1_data import load_format_data


def build_reminders(errors):
    """Build explicit move reminders from previous validation errors"""
    reminders = []
    for error in errors:
        if "cannot learn" in error:
            match = re.match(r'(\S+) cannot learn (\S+) in (\S+)\. Legal moves are: (.+)', error)
            if match:
                pokemon  = match.group(1)
                bad_move = match.group(2)
                legal    = match.group(4)
                reminders.append(
                    f"- {pokemon}: '{bad_move}' is ILLEGAL. Legal moves are: {legal}"
                )
    if reminders:
        return "\n".join(reminders)
    return ""


def distil_feedback(battle_feedback):
    """
    Convert verbose battle stats into a tight one-line team building constraint.
    Keeps the feedback surgical so deepseek-r1 doesn't reason its way off the rails.
    """
    avoid  = []
    prefer = []

    for line in battle_feedback.split('\n'):
        # Look for pokemon performance lines e.g. "  alakazam: fainted 100% of battles"
        match = re.match(r'\s+(\w+):\s+fainted\s+(\d+)%', line)
        if match:
            name      = match.group(1)
            faint_pct = int(match.group(2))
            if faint_pct == 100:
                avoid.append(name)
            elif faint_pct <= 30:
                prefer.append(name)

    parts = []
    if avoid:
        parts.append(f"Replace these (fainted every battle): {', '.join(avoid)}")
    if prefer:
        parts.append(f"Keep these (strong performers): {', '.join(prefer)}")

    return " | ".join(parts) if parts else ""


def clean_response(text):
    """Remove deepseek-r1 thinking blocks and markdown before parsing"""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'#{1,6}\s+', '', text)
    text = re.sub(r'\*\*|\*', '', text)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^-{3,}$', '', text, flags=re.MULTILINE)  # strip --- dividers
    return text.strip()


def build_prompt(format_data, format_name="OU", battle_feedback=None, validation_errors=None):
    """
    Build the team generation prompt.
    Battle feedback is distilled to a single tight constraint.
    Validation errors are kept separate to reinforce move accuracy.
    """

    pool_lines = []
    for internal, info in format_data.items():
        moves = ", ".join(info["moves"])
        pool_lines.append(f"{info['name']}: {moves}")
    pool_text = "\n".join(pool_lines)

    battle_feedback_text = ""
    if battle_feedback:
        battle_feedback_text = f"""
TEAM SELECTION GUIDANCE: {battle_feedback}
Use this only to decide which Pokemon to include or replace.
Do NOT let this affect your move selection - always copy moves from the VIABLE POOL.
"""

    validation_reminder_text = ""
    if validation_errors:
        reminders = build_reminders(validation_errors)
        if reminders:
            validation_reminder_text = f"""
MOVES YOU USED INCORRECTLY LAST TIME - DO NOT REPEAT:
{reminders}

Remember: ONLY use moves exactly as listed in the VIABLE POOL.
"""

    prompt = f"""You are a Pokemon Gen 1 {format_name} team builder. Follow these steps carefully.

IMPORTANT: Output ONLY the Showdown import format team.
Do not think out loud. Do not explain your choices.
Do not use markdown, headers, numbers, bullet points or dividers.
Do not include any text before or after the 6 Pokemon.
Your entire response must be parseable as a Showdown team import.

STEP 1 - TEAM SELECTION:
Choose exactly 6 different Pokemon from the VIABLE POOL below.
{battle_feedback_text}

STEP 2 - MOVE SELECTION:
For each Pokemon you chose, select exactly 4 moves.
CRITICAL RULES FOR MOVES:
- Copy move names EXACTLY as they appear in the VIABLE POOL - character for character
- Do NOT use any move not explicitly listed for that Pokemon
- Do NOT invent move names or use variations (e.g. if "surf" is listed, do NOT write "hydropump")
- Do NOT use moves from other Pokemon's lists
- "psychic" is the correct move name — NOT "psycho", "psychics" or "psychicm"
- If you are unsure whether a move is legal, pick a different one from the list
{validation_reminder_text}

VIABLE POOL (ONLY use Pokemon and moves from this list):
{pool_text}

RESPONSE FORMAT:
No markdown. No bold. No headers. No asterisks. No dividers.
Respond with EXACTLY 6 Pokemon in plain text Showdown import format.
Each Pokemon MUST have EXACTLY 4 moves - not 3, not 5, EXACTLY 4.
Count your moves before submitting each Pokemon.

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


def clean_team_text(team_text, format_data):
    """Strip any lines that aren't pokemon names or move lines"""
    valid_names = {info["name"].lower() for info in format_data.values()}

    cleaned = []
    for line in team_text.strip().split('\n'):
        stripped = line.strip()
        stripped = stripped.replace('**', '').replace('*', '').strip()
        stripped = stripped.rstrip(':').strip()

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


def validate_team(team_text, format_data, format_name="OU"):
    """
    Validate a generated team against the format data.
    Returns (is_valid, list of errors)
    """
    errors = []

    name_lookup = {}
    for internal, info in format_data.items():
        display = info["name"].lower().replace(' ', '').replace('.', '').replace("'", '')
        name_lookup[display] = {
            "internal": internal,
            "display":  info["name"],
            "moves":    set(info["moves"])
        }

    teams = {}
    current_pokemon = None
    current_moves = []

    lines = [l.strip() for l in team_text.strip().split('\n') if l.strip()]

    for line in lines:
        if line.startswith('-'):
            # Strip and ignore empty move lines (phantom carriage returns)
            move = line.lstrip('- ').strip().lower().replace(' ', '').replace('-', '')
            if move:
                current_moves.append(move)
        elif line:
            if current_pokemon:
                teams[current_pokemon] = current_moves
            current_pokemon = line.lower().replace(' ', '').replace('.', '').replace("'", '')
            current_moves = []

    if current_pokemon:
        teams[current_pokemon] = current_moves

    if len(teams) != 6:
        errors.append(f"Team has {len(teams)} Pokemon, needs exactly 6")

    for poke_name, moves in teams.items():
        if poke_name not in name_lookup:
            errors.append(f"{poke_name} is not in the Gen 1 {format_name} viable pool")
            continue

        if len(moves) != 4:
            errors.append(
                f"{name_lookup[poke_name]['display']} has {len(moves)} moves, needs exactly 4"
            )

        legal_moves = name_lookup[poke_name]["moves"]
        for move in moves:
            if move not in legal_moves:
                errors.append(
                    f"{name_lookup[poke_name]['display']} cannot learn {move} in {format_name}. "
                    f"Legal moves are: {', '.join(sorted(legal_moves))}"
                )

        if len(moves) != len(set(moves)):
            errors.append(f"{name_lookup[poke_name]['display']} has duplicate moves")

    if len(teams) != len(set(teams.keys())):
        errors.append("Team has duplicate Pokemon")

    return len(errors) == 0, errors


def generate_team(format_data, format_name="OU", battle_feedback=None, max_retries=5):
    """
    Ask model to generate a team for the given format.
    Battle feedback is distilled before passing to the prompt.
    Validation errors accumulate across retries.
    """
    last_errors = []

    # Distil verbose battle feedback into a tight constraint
    tight_feedback = distil_feedback(battle_feedback) if battle_feedback else None

    for attempt in range(max_retries):
        print(f"\n🤖 Generating team (attempt {attempt + 1}/{max_retries})...")

        prompt = build_prompt(
            format_data,
            format_name=format_name,
            battle_feedback=tight_feedback,
            validation_errors=last_errors
        )

        response = ollama.chat(
            model="deepseek-r1:7b",
            messages=[{"role": "user", "content": prompt}]
        )

        team_text = response['message']['content'].strip()
        team_text = clean_response(team_text)
        team_text = clean_team_text(team_text, format_data)

        print("\nGenerated team:")
        print(team_text)

        is_valid, errors = validate_team(team_text, format_data, format_name)

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
    import argparse

    parser = argparse.ArgumentParser(description="Generate a Gen 1 Pokemon team")
    parser.add_argument("--format", default="OU", help="Format to generate for (OU, UU, LC etc)")
    args = parser.parse_args()

    print(f"Loading Gen 1 {args.format} data...")
    format_data = load_format_data(args.format)
    print(f"Pool: {len(format_data)} Pokemon\n")

    team = generate_team(format_data, format_name=args.format)

    if team:
        print("\n🏆 Final team:")
        print(team)

        filename = f"current_team_{args.format.lower()}.txt"
        with open(filename, "w") as f:
            f.write(team)
        print(f"\nSaved to {filename}")
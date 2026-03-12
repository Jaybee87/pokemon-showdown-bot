import websocket
import requests
import json
import time
import ollama
import subprocess
from credentials import username, password

# Battle state
battle_log = []
current_turn = 0
player_side = "p1"
active_pokemon = ""

MOVE_NOTES = {
    "Surging Strikes": "always lands 3 hits and always crits",
    "Population Bomb": "hits multiple times",
    "Dual Wingbeat": "always lands 2 hits",
}


def ensure_ollama_running():
    """Check if Ollama is running, start it if not"""
    try:
        ollama.list()
        print("✅ Ollama is running")
        return True
    except Exception:
        print("🔄 Starting Ollama...")
        try:
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(3)
            ollama.list()
            print("✅ Ollama started successfully")
            return True
        except Exception as e:
            print(f"❌ Failed to start Ollama: {e}")
            return False


def hp_percent(condition):
    """Convert raw HP fraction to percentage string"""
    try:
        if "fnt" in condition:
            return "fainted"
        current, total = condition.split("/")
        pct = int(int(current) / int(total) * 100)
        return f"{pct}%"
    except:
        return condition


def parse_battle_message(msg):
    """Parse narrative battle events into readable log entries"""
    global active_pokemon
    lines = msg.strip().split("\n")
    events = []

    for line in lines:
        parts = line.split("|")
        if len(parts) < 2:
            continue

        event = parts[1] if len(parts) > 1 else ""

        if event == "move":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            move = parts[3] if len(parts) > 3 else ""
            target = parts[4].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 4 else ""
            note = f" ({MOVE_NOTES[move]})" if move in MOVE_NOTES else ""
            events.append(f"{pokemon} used {move}{note} on {target}")

        elif event == "switch":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            hp = hp_percent(parts[4]) if len(parts) > 4 else ""
            if player_side in parts[2]:
                active_pokemon = pokemon
                events.append(f"You switched in {pokemon} at {hp} HP")
            else:
                events.append(f"Opponent switched in {pokemon} at {hp} HP")

        elif event == "faint":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            events.append(f"{pokemon} fainted!")

        elif event == "turn":
            turn_num = parts[2] if len(parts) > 2 else ""
            events.append(f"--- Turn {turn_num} ---")

        elif event == "status":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            status = parts[3] if len(parts) > 3 else ""
            events.append(f"{pokemon} got {status}")

        elif event == "weather":
            weather = parts[2] if len(parts) > 2 else ""
            events.append(f"Weather: {weather}")

        elif event == "-damage":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            hp = hp_percent(parts[3]) if len(parts) > 3 else ""
            events.append(f"{pokemon} HP: {hp}")

        elif event == "-miss":
            attacker = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            target = parts[3].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 3 else ""
            events.append(f"{attacker}'s attack missed {target}")

        elif event == "-immune":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            events.append(f"{pokemon} is immune!")

        elif event == "-fail":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            events.append(f"{pokemon}'s move failed")

        elif event == "-supereffective":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            events.append(f"It's super effective against {pokemon}!")

        elif event == "-resisted":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            events.append(f"{pokemon} resisted the move")

        elif event == "-crit":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            events.append(f"Critical hit on {pokemon}!")

        elif event == "-heal":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            hp = hp_percent(parts[3]) if len(parts) > 3 else ""
            events.append(f"{pokemon} healed to {hp}")

        elif event == "-status":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            status = parts[3] if len(parts) > 3 else ""
            events.append(f"{pokemon} was inflicted with {status}")

        elif event == "-curestatus":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            events.append(f"{pokemon} was cured of its status")

        elif event == "-boost":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            stat = parts[3] if len(parts) > 3 else ""
            amount = parts[4] if len(parts) > 4 else ""
            events.append(f"{pokemon}'s {stat} rose by {amount}")

        elif event == "-unboost":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            stat = parts[3] if len(parts) > 3 else ""
            amount = parts[4] if len(parts) > 4 else ""
            events.append(f"{pokemon}'s {stat} fell by {amount}")

        elif event == "-sidestart":
            side = parts[2] if len(parts) > 2 else ""
            condition = parts[3].replace("move: ", "") if len(parts) > 3 else ""
            side_label = "your side" if player_side in side else "opponent's side"
            events.append(f"{condition} was set up on {side_label}")

        elif event == "-sideend":
            side = parts[2] if len(parts) > 2 else ""
            condition = parts[3].replace("move: ", "") if len(parts) > 3 else ""
            side_label = "your side" if player_side in side else "opponent's side"
            events.append(f"{condition} was removed from {side_label}")

        elif event == "-activate":
            pokemon = parts[2].replace("p1a: ", "").replace("p2a: ", "") if len(parts) > 2 else ""
            effect = parts[3] if len(parts) > 3 else ""
            events.append(f"{pokemon} activated {effect} — move was blocked!")

        elif event == "win":
            winner = parts[2] if len(parts) > 2 else ""
            result = "You won the battle!" if winner == username else "You lost the battle."
            events.append(result)

        elif event == "tie":
            events.append("The battle ended in a tie.")

    return events


def parse_request(msg):
    """Extract available moves and team state from request blob"""
    global player_side
    try:
        raw = msg.split("|request|")[1].strip()
        data = json.loads(raw)

        player_side = data.get("side", {}).get("id", "p1")

        team = []
        for p in data.get("side", {}).get("pokemon", []):
            team.append({
                "name": p.get("ident", "").replace("p1: ", "").replace("p2: ", ""),
                "hp": hp_percent(p.get("condition", "")),
                "active": p.get("active", False)
            })

        # Handle forced switch after faint
        if data.get("forceSwitch"):
            moves = [{"name": "SWITCH", "pp": 0, "maxpp": 0}]
            return moves, team

        # Normal move selection
        moves = []
        if "active" in data and data["active"]:
            for m in data["active"][0].get("moves", []):
                if not m.get("disabled", False):
                    moves.append({
                        "name": m["move"],
                        "pp": m.get("pp", 0),
                        "maxpp": m.get("maxpp", 0)
                    })

        return moves, team

    except Exception as e:
        print(f"Request parse error: {e}")
        return [], []


def ask_ollama(battle_log, moves, team):
    """Feed battle state to Ollama and get move suggestion"""

    log_text = "\n".join(battle_log[-20:])

    team_text = "\n".join([
        f"- {p['name']} HP: {p['hp']} {'(active)' if p['active'] else ''}"
        for p in team
    ])

    if moves and moves[0]["name"] == "SWITCH":
        prompt = f"""You are a Pokemon battle advisor playing as {player_side}.
Your active Pokemon just fainted. Choose which Pokemon to send in next.
Only suggest Pokemon that are not fainted.

Battle log (most recent):
{log_text}

Your team:
{team_text}

Respond with:
SWITCH: <pokemon name>
REASON: <one sentence why>"""

    else:
        moves_text = "\n".join([
            f"- {m['name']} (PP: {m['pp']}/{m['maxpp']})"
            for m in moves
        ])

        prompt = f"""You are a Pokemon battle advisor playing as {player_side}.
You are currently using {active_pokemon}.
Analyse the battle and suggest the best move.

Battle log (most recent):
{log_text}

Your team:
{team_text}

Your available moves this turn:
{moves_text}

Respond with:
MOVE: <move name>
REASON: <one sentence why>"""

    response = ollama.chat(
        model="mistral",
        messages=[{"role": "user", "content": prompt}]
    )

    return response['message']['content']


# --- Main ---

if not ensure_ollama_running():
    print("Please start Ollama manually with: ollama serve")
    exit(1)

ws = websocket.create_connection("wss://sim3.psim.us/showdown/websocket")

# Get challstr
challstr = ""
while not challstr:
    msg = ws.recv()
    if "|challstr|" in msg:
        challstr = msg.split("|challstr|")[1].strip()
        print("Got challstr!")

# Authenticate
response = requests.post(
    "https://play.pokemonshowdown.com/api/login",
    data={
        "name": username,
        "pass": password,
        "challstr": challstr
    }
)

data = json.loads(response.text[1:])
assertion = data["assertion"]

# Login
print("Logging in...")
ws.send(f"|/trn {username},0,{assertion}")
while True:
    msg = ws.recv()
    if "|updateuser|" in msg and username in msg:
        print("Message Response:", msg[:200])
        break

print("\n✅ Bot ready! Start a battle in your browser...\n")
ws.settimeout(300)

NARRATIVE_EVENTS = [
    "|move|", "|switch|", "|turn|", "|faint|", "|-damage|", "|status|",
    "|-miss|", "|-immune|", "|-fail|", "|-supereffective|", "|-resisted|",
    "|-crit|", "|-heal|", "|-status|", "|-boost|", "|-unboost|",
    "|-sidestart|", "|-sideend|", "|-activate|", "|win|", "|tie|"
]

while True:
    try:
        msg = ws.recv()

        if any(x in msg for x in NARRATIVE_EVENTS):
            events = parse_battle_message(msg)
            battle_log.extend(events)
            for event in events:
                print(event)

        if "|request|" in msg:
            pending_moves, pending_team = parse_request(msg)
            if pending_moves:
                print("\n🤖 Ollama says:")
                suggestion = ask_ollama(battle_log, pending_moves, pending_team)
                print(suggestion)
                print()

    except websocket.WebSocketTimeoutException:
        print("Waiting for battle...")
    except Exception as e:
        print(f"Error: {e}")
        break

ws.close()
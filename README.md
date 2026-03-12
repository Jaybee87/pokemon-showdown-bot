# Pokemon Showdown Bot

A hybrid Python/LLM competitive Pokemon battle bot for Gen 1 OU, built on [poke-env](https://github.com/hsahovic/poke-env) and [Ollama](https://ollama.com).

Python handles clear-cut decisions instantly. The LLM is called only when genuine ambiguity exists — type matchups are unclear, status moves are available, or a commitment move like Hyper Beam needs weighing. Full reasoning is logged every battle.

---

## How It Works

```
python3 main.py          # Build a team (interactive anchor selection, 5 iterations)
python3 live_challenge.py --accept   # Go live on Showdown
```

That's it. `main.py` handles everything: fetches Gen 1 data from pokered, builds teams with LLM-assisted move selection, stress tests them locally, and iterates. Then `live_challenge.py` takes the final team online.

---

## Quick Start

See [INSTALL.md](INSTALL.md) for full setup (Python, Showdown server, Ollama, credentials).

Once set up:

```bash
# Step 1 — Build and test a team (runs locally, needs Showdown server + Ollama)
python3 main.py

# Step 2 — Go live (connects to play.pokemonshowdown.com)
python3 live_challenge.py --accept
# Then challenge the bot from your browser: /challenge BotName, gen1ou
```

### Console output during live play

The bot runs in compact mode — one line per turn so you can monitor at a glance:

```
  ⚡ T01 gengar(100%) vs alakazam(100%) → nightshade [py]
  🤖 T03 exeggutor(85%) vs alakazam(69%) → psychic [llm]
  ⚡ T06 rhydon(100%) vs zapdos(46%) → rockslide [py]
  🤖 T11 alakazam(100%) vs exeggutor(32%) → seismictoss [llm]
```

`⚡` = Python fast-path decision, `🤖` = LLM decision. Full verbose detail is always captured in the log file.

---

## Project Structure

```
pokemon-showdown-bot/
├── main.py                # Single entry point — build team, stress test, iterate
├── live_challenge.py      # Connect to live Showdown, challenge real players
├── competitive_player.py  # Hybrid Python/LLM decision engine
├── gen1_engine.py         # Gen 1 type chart + effectiveness (single source of truth)
├── gen1_data.py           # Pokemon/move data from pokered ASM + Showdown tiers
├── team_generator.py      # LLM-driven team builder with battle feedback loop
├── battle_runner.py       # Local stress tester (StatTrackingPlayer vs RandomPlayer)
├── llm_bridge.py          # All LLM interaction — thread-safe timeout, response parsing
├── config.py              # Central config — model name, server URLs, format settings
├── credentials.py         # Bot's Showdown login (gitignored, you create this)
├── teams/                 # Generated team iterations + feedback (gitignored)
│   ├── team_ou_iteration_1.txt
│   ├── feedback_ou_iteration_1.txt
│   └── ...
├── live_logs/             # Battle logs from live and local play (gitignored)
│   ├── live_log_001.txt
│   └── ...
└── archive/
    └── showdown.py        # Retired raw websocket client (reference only)
```

---

## Decision Tree

```
PRE-FILTER          → remove all immune moves (0x) from options
                       Electric → Ground, Normal → Ghost, Ghost → Normal,
                       Fighting → Ghost, Ground → Flying, Psychic → Ghost (Gen 1)
                       Fixed-damage moves (Seismic Toss, Night Shade) are kept
RECHARGE TURN       → forced, instant
STEP 1              → no moves + no switches → default
STEP 2              → faint / no real moves  → LLM picks switch-in
STEP 3              → compute best_move (STAB-aware, Hyper Beam penalised)
STEP 4              → immune to all opponent known moves → attack freely
STEP 5              → danger switch (confirmed threat or STAB threat)
STEP 6              → dominant 2x+ advantage (bp ≥ 60) → attack
STEP 7a             → Rest available + HP < 40% → use Rest
STEP 7b             → opponent asleep + Dream Eater → use Dream Eater
STEP 8              → AMBIGUOUS → LLM called with full battle context
```

### Move Filtering (before LLM or Python sees options)

Moves are removed from the available options when they would be wasted:

- All moves dealing 0x damage to the opponent (type immunities)
- Thunder Wave if opponent already has a status condition
- Thunder Wave against Ground types (immune to Electric)
- Dream Eater unless opponent is asleep
- Explosion / Self-Destruct never auto-picked (LLM decision only)

### LLM Trigger Conditions

The LLM is called when Python can't confidently resolve the situation:

- Best move is resisted or we're in danger
- Neutral matchup with unknown opponent moveset
- A switch-in resists the opponent's known moves
- Sleep move available + opponent unstatused
- Thunder Wave available + opponent unstatused
- Hyper Beam is the best move (recharge cost needs weighing)

---

## Live Play

### Accept mode (recommended)

```bash
python3 live_challenge.py --accept
```

The bot logs into Showdown, then waits. You challenge it from your browser. This bypasses Showdown's IP-based spam restrictions on new bot accounts.

### Challenge mode

```bash
python3 live_challenge.py --opponent YourUsername
```

The bot sends the challenge. Your opponent accepts in their browser. May be blocked for new accounts on flagged IPs — use accept mode instead.

### Features

- Auto-starts the battle timer (disconnected opponents auto-forfeit)
- Compact console output — one line per turn for monitoring
- Full verbose reasoning captured in `live_logs/live_log_NNN.txt`
- Thread-safe LLM calls (won't freeze the websocket connection)
- All type immunities filtered before the LLM sees move options

---

## Configuration

All settings live in `config.py`:

```python
LLM_MODEL = "deepseek-r1:7b"     # Change to use a different model
LLM_TIMEOUT_SECONDS = 30          # Hard timeout for live play safety
```

Override with environment variables:

```bash
LLM_MODEL=deepseek-r1:14b python3 live_challenge.py --accept
```

---

## Known Gen 1 Quirks Implemented

- `Ghost → Psychic = 0x` (RBY programming bug — Ghost moves don't work on Psychic)
- `Psychic → Ghost = 1x` (not 0x — Ghost is not immune to Psychic in Gen 1)
- `Ice → Fire = 1x` (neutral in Gen 1, not resisted as in Gen 2+)
- No Dark / Steel / Fairy types
- Seismic Toss / Night Shade deal fixed damage equal to user level (ignore type chart)
- Sleep is the strongest status — Dream Eater combo is explicitly supported

---

## Roadmap

- [x] Connect to live Showdown server
- [x] Challenge real players (accept + challenge modes)
- [x] Thread-safe LLM calls
- [x] Opponent move type tracking
- [x] Type immunity pre-filtering
- [x] Compact console output for ladder monitoring
- [x] Battle timer (disconnect protection)
- [x] Interactive anchor selection
- [x] Organised output directories
- [ ] Upgrade LLM backend (14b+ model for better reasoning)
- [ ] Gen 2 support (held items, expanded pool)
- [ ] Win/loss tracking across sessions
- [ ] Ladder mode (continuous matchmaking)
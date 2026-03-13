# Pokemon Showdown Bot — CDMG_217

A competitive Pokemon battle bot that builds its own team, tests it, and takes it to the live Showdown ladder — starting from nothing.

## What does this project do?

This project automates the full competitive Pokemon pipeline:

1. **Team Building** — An LLM generates a competitive team from scratch, picking Pokemon, moves, and synergies based on the metagame. You choose an anchor Pokemon and the system builds around it. For Gen 1 OU the meta is solved enough that hand-crafted teams now outperform generated ones, but the builder remains the starting point for experimenting with new formats or unconventional strategies.

2. **Team Testing** — The bot stress-tests its team locally against a random opponent across dozens of battles, collecting win rates, move usage, and matchup data. After each iteration it feeds the results back to the LLM to refine the team — swap weak links, adjust movesets, cover bad matchups.

3. **Showdown Battling** — The bot connects to the live Pokemon Showdown server and plays ranked ladder games against real humans. A hybrid decision engine combines a Python fast-path (pre-computed damage calculator, type matchups, speed checks) with an LLM for genuinely ambiguous decisions. It runs unattended and reconnects automatically on network drops.

The goal isn't to build the strongest possible bot — it's to build a system that can start from zero, learn, and compete. Every generation of Pokemon has different mechanics, and the architecture is designed to expand.

---

## Quick Start

See [INSTALL.md](INSTALL.md) for full setup (Python, Showdown server, Ollama, credentials).

```bash
# Build and test a team locally
python3 main.py

# Go live on the ladder
python3 live_challenge.py --ladder 20

# Or wait for challenges (recommended for new accounts)
python3 live_challenge.py --accept
```

### Live Play Modes

```bash
python3 live_challenge.py --accept              # Wait for challenge (you challenge from browser)
python3 live_challenge.py --opponent <username>  # Send challenge to a specific user
python3 live_challenge.py --ladder N             # Play N ranked ladder games
python3 live_challenge.py --battles N            # N battles for accept/opponent modes
python3 live_challenge.py --format gen1ou        # Format (default: gen1ou)
```

---

## Configuration

All settings in `config.py`, overridable with env vars:

| Setting | Default | Env Var | Description |
|---------|---------|---------|-------------|
| `LLM_MODEL` | `deepseek-r1:14b` | `LLM_MODEL` | Ollama model for battle decisions |
| `LLM_CONTEXT_LENGTH` | `2048` | `LLM_CONTEXT` | Context window (128K default wastes VRAM) |
| `LLM_LIVE_TIMEOUT_SECONDS` | `25` | `LLM_LIVE_TIMEOUT` | Max seconds to wait for LLM response |
| `LLM_TIMEOUT_SECONDS` | `30` | `LLM_TIMEOUT` | Timeout for local/team building |

```bash
# Example: use 7b model with 12-second timeout
LLM_MODEL=deepseek-r1:7b LLM_LIVE_TIMEOUT=12 python3 live_challenge.py --ladder 50
```

---

## Project Structure

```
pokemon-showdown-bot/
├── main.py                # Entry point — build team, stress test, iterate
├── live_challenge.py      # Live Showdown connection (accept/challenge/ladder)
├── competitive_player.py  # Hybrid Python/LLM decision engine
├── gen1_engine.py         # Gen 1 type chart + effectiveness (single source of truth)
├── gen1_calc.py           # Damage calculator, speed table, matchup evaluator
├── gen1_data.py           # Pokemon/move data from pokered ASM + Showdown tiers
├── team_generator.py      # LLM-driven team builder with battle feedback loop
├── battle_runner.py       # Local stress tester (StatTrackingPlayer vs RandomPlayer)
├── llm_bridge.py          # All LLM interaction — async, thread-safe, system prompt
├── config.py              # Central config — model, timeouts, server URLs
├── credentials.py         # Bot's Showdown login (gitignored)
├── teams/                 # Team iterations (gitignored)
├── live_logs/             # Battle logs (gitignored)
└── archive/
    └── showdown.py        # Retired raw websocket client (reference only)
```

---

## Decision Tree (competitive_player.py)

```
PRE-FILTER     → Remove immune moves (0x), T-Wave if opponent has status,
                 Dream Eater if opponent not asleep
RECHARGE       → Forced (locked after Hyper Beam)
STEP 1         → No moves + no switches → default
STEP 2         → Fainted / no real moves → LLM picks switch-in
STEP 3         → Compute best_move (STAB-aware, Hyper Beam penalised,
                 Seismic Toss/Night Shade scored at 100 effective BP)
STEP 4         → Immune to all opponent known moves → attack freely
STEP 5         → Danger switch (confirmed 2x threat + low HP)
STEP 5b        → Matchup switch (teammate scores 40+ points better)
                 Only fires once per opponent switch-in
STEP 6         → Dominant 2x+ advantage (bp ≥ 60) → attack
STEP 6b        → KO check (damage calc confirms kill) → finish them
                 Accounts for stat stages, Reflect, Light Screen
STEP 6c        → Thunder Wave (opponent faster + no status) → paralyse
STEP 7a        → Recover / Soft-Boiled at <55% HP → heal
STEP 7b        → Rest at <40% HP (last resort, causes sleep)
STEP 7c        → Dream Eater if opponent asleep
STEP 8         → AMBIGUOUS → LLM called (25s timeout, Python fallback)
```

### Anti-Loop Protection
- Sleep switch: won't switch out if only switch target is <30% HP
- Danger switch: won't switch to asleep or <25% HP target when only 1 option
- Both prevent the "ping-pong between last 2 Pokemon" death spiral

---

## Damage Calculator (gen1_calc.py)

All stats pre-computed for L100, max DVs (15), max Stat EXP. Gen 1 has no variation — every matchup is deterministic except the 217-255 random factor.

### Features
- [x] Full Gen 1 damage formula
- [x] STAB calculation
- [x] Type effectiveness (uses gen1_engine type chart)
- [x] Stat stage modifiers (-6 to +6, Gen 1 approximation formula)
- [x] Reflect (doubles physical defense)
- [x] Light Screen (doubles special defense)
- [x] Explosion/Self-Destruct (halves target defense)
- [x] Critical hits (ignore stat stages and screens)
- [x] Speed table with paralysis (quarters speed)
- [x] KO check: guaranteed vs likely (min roll vs average)
- [x] 2HKO check
- [x] Matchup evaluator (offensive pressure + defensive typing + speed + HP + status)

### Not Yet Implemented
- [ ] Toxic damage accumulation (turn-based increasing damage)
- [ ] Burn damage (1/16 per turn)
- [ ] Leech Seed drain
- [ ] Substitute HP tracking
- [ ] Freeze chance from Ice moves (10% in Gen 1)

---

## LLM Integration (llm_bridge.py)

### Architecture
- System prompt suppresses `<think>` tags → direct DECISION output
- `num_ctx: 2048` passed at runtime (overrides model's 128K default)
- `call_llm_async()` yields control to event loop during inference
- Thread pool executor with 2 workers
- Python fallback fires immediately on timeout or hallucinated move

### LLM Trigger Conditions (Step 8)
- Best move is resisted + no dominant alternative
- Neutral matchup with unknown opponent moveset
- Sleep move available + opponent unstatused
- Hyper Beam is best move (recharge cost needs weighing)
- Counter available (prediction move)
- Explosion/Self-Destruct consideration

### LLM-Only Moves (never auto-picked by Python)
- Explosion / Self-Destruct
- Counter

---

## Infrastructure (live_challenge.py)

- [x] Auto-reconnection on websocket drops (5 retries, 10s backoff)
- [x] Battle progress counter (per-game + cumulative stats)
- [x] Battle timer auto-start (disconnect protection)
- [x] Ping tolerance 30s (accommodates LLM response time)
- [x] Log filter: suppresses Wrap/Bind warnings, forfeit race conditions
- [x] Compact console output (one line per turn, full detail in log)

---

## Gen 1 Quirks Implemented

- `Ghost → Psychic = 0x` (RBY programming bug)
- `Psychic → Ghost = 1x` (not immune in Gen 1)
- `Ice → Fire = 1x` (neutral, not resisted as in Gen 2+)
- `Bug ↔ Poison = 2x` both ways (changed in Gen 2)
- No Dark / Steel / Fairy types
- Special is one stat (offense + defense)
- Seismic Toss / Night Shade = fixed 100 damage at L100
- Critical hits ignore stat stages and screens
- Paralysis quarters speed (not halves)
- Stat stages cap at 999

---

## Roadmap

### Completed
- [x] Live Showdown connection (accept / challenge / ladder)
- [x] Thread-safe async LLM calls
- [x] Pre-computed damage calculator with stat stages
- [x] Matchup-based switching
- [x] KO check before healing
- [x] Thunder Wave fast-path
- [x] Recover / Soft-Boiled healing logic
- [x] Sleep → switch out (with anti-loop)
- [x] Seismic Toss / Night Shade scored correctly
- [x] Reflect / Light Screen detection
- [x] Opponent move type tracking
- [x] Type immunity pre-filtering
- [x] Battle progress counter (per-game + cumulative)
- [x] Auto-reconnection handler
- [x] System prompt to suppress thinking tokens
- [x] Runtime context length control (num_ctx)
- [x] Compact console output
- [x] 14b model with optimised VRAM usage
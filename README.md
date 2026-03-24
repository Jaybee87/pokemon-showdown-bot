# Pokemon Showdown Bot — JofarLLM

A competitive Gen 1 OU battle bot powered by a hybrid Python/Rust decision engine. Connects to the live Pokemon Showdown ladder and plays ranked games against real humans.

## What does this project do?

The bot connects to Pokemon Showdown and plays Gen 1 OU battles autonomously. A Python fast-path handles mechanical certainties — damage calculations, KO detection, status move gating, healing logic — and hands everything strategic to a Rust search engine (MCTS + minimax). It runs unattended and reconnects automatically on network drops.

You provide a team file. The bot plays it.

---

## Quick Start

See [INSTALL.md](INSTALL.md) for full setup.

```bash
# Play 20 ranked ladder games
python3 main.py --ladder 20

# Wait for challenges (recommended for new accounts)
python3 main.py --accept

# Interactive menu
python3 main.py
```

### Live Play Modes

```bash
python3 main.py --accept                     # Wait for challenge from your browser
python3 main.py --opponent <username>         # Send challenge to a specific user
python3 main.py --ladder N                    # Play N ranked ladder games
python3 main.py --battles N                   # N battles for accept/opponent modes
python3 main.py --format gen1ou              # Format (default: gen1ou)
```

---

## Team Files

Place your team in `teams/` with the naming convention `team_ou_iteration_N.txt`. The bot uses the highest-numbered iteration.

```
Tauros
- bodyslam
- hyperbeam
- earthquake
- blizzard

Snorlax
- bodyslam
- earthquake
- hyperbeam
- rest
```

---

## Project Structure

```
pokemon-showdown-bot/
├── main.py                  # Entry point — battle menu, preflight, logging
├── live_challenge.py        # Live Showdown connection (accept/challenge/ladder)
├── competitive_player.py    # Hybrid Python/Rust decision engine
├── rust_engine_bridge.py    # Python ↔ Rust bridge (state serialisation, result parsing)
├── gen1_engine.py           # Gen 1 damage calc, type chart, matchup scoring
├── gen1_data.py             # Base stats, move data, type definitions
├── gen1_engine_rs/          # Rust search engine (MCTS + minimax)
│   └── src/
│       ├── main.rs          # Engine entry point
│       ├── sim.rs           # Battle simulator
│       ├── calc.rs          # Damage calculation (integer arithmetic)
│       ├── state.rs         # BattlePoke, BattleState (pre-computed inline stats)
│       ├── data.rs          # BATTLE_STATS_TABLE, MOVE_DATA_BY_ID (OnceLock)
│       ├── eval.rs          # Position evaluation
│       ├── mcts.rs          # Monte Carlo Tree Search
│       ├── minimax.rs       # Minimax with alpha-beta
│       └── inference.rs     # Opponent team inference
├── config.py                # Server config, format, log level
├── credentials.py           # Bot's Showdown login (gitignored)
├── teams/                   # Team files (gitignored)
├── live_logs/               # Battle logs (gitignored)
└── archive/                 # Retired modules
    ├── llm_bridge.py        # Archived LLM integration (Ollama/deepseek-r1)
    ├── team_generator.py    # LLM-driven team builder
    ├── battle_runner.py     # Local stress tester
    ├── gen1_data.py         # Original data module
    ├── gen1_calc.py         # Original damage calculator
    └── test_battle.py       # Original test suite
```

---

## Decision Tree (competitive_player.py)

```
STEP 1  RECHARGE       → Forced lock after Hyper Beam
STEP 2  FORCED         → No moves + no switches → default
STEP 3  FAINT SWITCH   → Rust picks send-in (Python fallback if Rust errors)
STEP 4  ASLEEP         → Switch out cleanly, or queue best move
STEP 5  GUARANTEED KO  → Python math confirms kill on min roll → finish them
                         (skipped if opponent is faster + has a heal move)
STEP 6  IMMUNE         → All revealed opponent moves do 0x → stay and attack
STEP 7  SLEEP MOVE     → Opponent unstatused, sleep move available → use it
STEP 7b HEAL FILTER    → Toxic: heal immediately (unless futile)
                         Low HP + danger threshold: heal with Softboiled/Recover
                         High HP / heal spam / winning position: suppress heals
STEP 7e TWAVE RECOVER  → Opponent has Recover + we can't 2HKO → Thunder Wave
STEP 8  RUST ENGINE    → All other decisions: switch timing, Hyperbeam risk,
                         damage races, stall breaks, matchup evaluation
STEP 9  HARD FALLBACK  → Best type-effective move if Rust errors
```

### Anti-Loop Protection
- Sleep switch: won't switch out if only target is <30% HP or also asleep
- Switch cooldown: won't immediately switch out a mon that just switched in (unless losing badly)
- Heal spam: won't fire heal on the same mon twice within 2 turns at high HP
- Toxic futility: stops healing when Toxic drain rate exceeds recovery rate

---

## Rust Engine (gen1_engine_rs)

The Rust engine runs as a subprocess, communicates via stdin/stdout JSON, and handles all strategic decisions not covered by Python fast-paths.

### Engine v5 (current) — 57,000 nodes/sec
- `BattlePoke` stores pre-computed stats inline (zero table lookups in hot path)
- Integer HP replacing f32 hp_frac
- `MOVE_DATA_BY_ID` OnceLock replaces linear scan
- All `apply_turn` arithmetic is integer
- **14× throughput improvement** over v1 (4k → 57k nodes/sec)

### Search
- `auto` mode: minimax for endgame (≤4 total Pokémon alive), MCTS otherwise
- Time budget allocated per turn by `TimeManager` based on position complexity
- Depth-6 minimax for endgame; MCTS with 100k iteration ceiling for midgame
- 210s total bank per game, 20% per-turn spending cap

---

## Damage Calculator (gen1_engine.py)

All stats pre-computed for L100, max DVs (15), max Stat EXP. Gen 1 has no variation except the 217–255 random factor.

### Implemented
- [x] Full Gen 1 damage formula
- [x] STAB, type effectiveness (Gen 1 chart including Ghost/Psychic bug)
- [x] Stat stage modifiers (−6 to +6)
- [x] Reflect / Light Screen
- [x] Explosion/Self-Destruct (halves target defence)
- [x] Critical hits (ignore stat stages and screens)
- [x] Speed table with paralysis (quarters speed)
- [x] Guaranteed KO / likely KO / 2HKO checks
- [x] Matchup evaluator (offensive pressure + defensive typing + speed + HP + status)

### Not Yet Implemented
- [ ] Toxic damage accumulation (turn-based increasing damage)
- [ ] Burn damage (1/16 per turn)
- [ ] Leech Seed drain
- [ ] Substitute HP tracking
- [ ] Freeze chance from Ice moves

---

## Gen 1 Quirks Implemented

- `Ghost → Psychic = 0×` (RBY programming bug)
- `Psychic → Ghost = 1×` (not immune in Gen 1)
- `Ice → Fire = 1×` (neutral, not resisted as in Gen 2+)
- `Bug ↔ Poison = 2×` both ways (changed in Gen 2)
- No Dark / Steel / Fairy types
- Special is one stat (offence + defence)
- Seismic Toss / Night Shade = fixed 100 damage at L100
- Critical hits ignore stat stages and screens
- Paralysis quarters speed (not halves)

---

## What you'll see

```
==============================
Turn 8 | My: alakazam (72% HP) vs exeggutor (50% HP)
  My types: ['psychic'] | Opp types: ['grass', 'psychic']
  Moves: ['seismictoss', 'psychic', 'thunderwave', 'recover']
  ⏱  Time budget: 4200ms (bank=187.3s)
  ✅ RUST [minimax]: seismictoss (score=1840, nodes=28441) | guaranteed damage
  ⚙️ T08 alakazam(72%) vs exeggutor(50%) → seismictoss [rust]

============================================================
BATTLE OVER — WON ✓ in 31 turns
  Python decisions: 14
  Rust decisions:   17
  Rust involvement: 54% of turns
============================================================
📈 Progress: 8/20 (5W / 3L)
```

Full verbose reasoning is saved to `live_logs/live_log_NNN.txt`.

---

## Roadmap

### Completed
- [x] Live Showdown connection (accept / challenge / ladder)
- [x] Rust engine v5 — 57k nodes/sec (14× improvement)
- [x] Pre-computed damage calculator with stat stages
- [x] Matchup-based switching
- [x] KO check before healing
- [x] Thunder Wave fast-path + Recover-stall detection
- [x] Recover / Soft-Boiled healing logic with spam prevention
- [x] Sleep → switch out (with anti-loop)
- [x] Seismic Toss / Night Shade scored correctly
- [x] Reflect / Light Screen detection
- [x] Opponent move type tracking
- [x] Type immunity pre-filtering
- [x] Battle progress counter (per-game + cumulative)
- [x] Auto-reconnection handler
- [x] Dynamic time bank allocation (TimeManager)
- [x] Compact console output + full verbose log

### Next
- [ ] Gen 2 port (Special split, Steel/Dark types, held items)
- [ ] Self-play distillation — logistic regression over position features
- [ ] Post-game LLM pattern analysis over structured logs

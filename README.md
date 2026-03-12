# Pokemon Showdown Bot

A hybrid Python/LLM competitive Pokemon battle bot for Gen 1 OU, built on [poke-env](https://github.com/hsahovic/poke-env) and [Ollama](https://ollama.com).

Python handles clear-cut decisions instantly. The LLM is called only when genuine ambiguity exists — type matchups are unclear, status moves are available, or a commitment move like Hyper Beam needs weighing. Full reasoning is logged every battle.

---

## Project Structure

```
gen1_data.py              # Gen 1 Pokemon/move data sourced from pokered ASM
gen1_type_chart.py        # Gen 1 specific type chart (with RBY bugs intact)
team_generator.py         # LLM-driven Gen 1 OU team builder
battle_runner.py          # Dumb stress tester (RandomPlayer vs RandomPlayer)
competitive_player.py     # Smart hybrid Python/LLM player (main entry point)
main.py                   # Orchestrates team builder iteration loop
```

---

## competitive_player.py

The main bot. Runs a hybrid decision engine against a local RandomPlayer opponent.

```bash
python3 competitive_player.py              # auto-loads highest team_ou_iteration_N.txt
python3 competitive_player.py --battles 5  # run multiple battles
python3 competitive_player.py --format ou  # explicit format
```

Logs are auto-numbered: `competitive_log_001.txt`, `002.txt`, etc. Output is tee'd to both stdout and file.

### Decision Tree

```
RECHARGE TURN       → forced, instant
STEP 1              → no moves + no switches → default
STEP 2              → faint / no real moves  → LLM picks switch-in (or auto if only one left)
STEP 3              → compute best_move (STAB-aware, Hyper Beam penalised)
STEP 4              → immune to all opponent known moves → attack freely
STEP 5              → danger switch
                       Tier A: confirmed 2x from revealed moves + <40% HP
                       Tier B: STAB type threat + <50% HP (conservative fallback)
STEP 6              → dominant 2x+ advantage (bp ≥ 60) → attack
STEP 7a             → Rest available + HP < 40% → use Rest
STEP 7b             → opponent asleep + Dream Eater available → use Dream Eater
STEP 8              → AMBIGUOUS → LLM called
```

### LLM Trigger Conditions

The LLM is called when Python can't confidently resolve the situation:

- Best move is resisted or we're in danger
- Neutral matchup with unknown opponent moveset
- A switch-in resists the opponent's known moves
- Sleep move available (Hypnosis, Sleep Powder, Spore, Lovely Kiss) + opponent unstatused
- Thunder Wave available + opponent unstatused
- Hyper Beam is the best move (recharge cost needs weighing)

### Filtered Moves

- **Thunder Wave** — removed from options if opponent already has a status
- **Dream Eater** — removed unless opponent is asleep (SLP)
- **Explosion / Self-Destruct** — never auto-picked; LLM-only decision
- **Struggle / Recharge** — handled as forced turns, bypass decision tree

---

## Team Builder Loop

```bash
python3 main.py
```

Runs an iterative loop: generate team → stress test with battle_runner → collect feedback → regenerate. Teams are saved as `team_ou_iteration_N.txt`. The competitive player auto-loads the highest iteration.

---

## Battle Results (vs RandomPlayer, iteration 5 team)

| Log | Result | Turns | LLM% | Notes |
|-----|--------|-------|------|-------|
| 001 | WIN    | 43    | 85%  | Early build, LLM fallback heavy |
| 002 | WIN    | 46    | 16%  | Recharge + type chart fixes |
| 003 | LOSS   | 48    | 11%  | Hyper Beam spam, no STAB |
| 004 | WIN    | 31    | 25%  | Rest working, faint→LLM |
| 007 | WIN    | 27    | 37%  | Team preview, opponent HP visible |
| 008 | WIN    | 29    | 41%  | Status tracking, Dream Eater fix |
| 009 | LOSS   | 43    | 27%  | Close — paralysis RNG endgame |
| 010 | WIN    | 25    | 40%  | Hypnosis trigger, clean Thunder Wave |

---

## Current Team (iteration 5)

Gengar / Exeggutor / Zapdos / Cloyster / Tauros / Alakazam

---

## Known Gen 1 Quirks Implemented

- `Ghost → Psychic = 0x` (RBY programming bug — Ghost moves don't work on Psychic)
- `Psychic → Ghost = 1x` (not 0x — Ghost is not immune to Psychic in Gen 1)
- No Dark / Steel / Fairy types
- Seismic Toss / Night Shade deal fixed damage equal to user level (ignore type chart)
- Sleep is the strongest status — Dream Eater combo is explicitly supported

---

## Roadmap

- [ ] Connect to live Showdown ladder
- [ ] Upgrade LLM backend (14b+ model for better reasoning)
- [ ] Gen 1 move data from pokered source (`gen1_moves.py`)
- [ ] Gen 2 support (held items, expanded pool)
- [ ] Scale battle_runner iterations
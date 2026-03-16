# gen1_engine_rs — Rust Search Engine for Gen 1 Pokémon

Stockfish-style Minimax + MCTS engine for Pokémon Generation 1 battles,
designed to replace the LLM "AMBIGUOUS" branch in `competitive_player.py`
with fast, deterministic tree search.

---

## Architecture

```
competitive_player.py
        │  build_state(battle) → dict
        │  action_to_poke_env(decision, battle)
        ▼
rust_engine_bridge.py       ← Python side: builds state, manages subprocess
        │  newline-delimited JSON over stdin/stdout
        ▼
gen1_engine_rs              ← Rust binary (cargo build --release)
    ├── main.rs             IPC loop: reads JSON, calls inference, runs search
    ├── data.rs             All 151 Pokémon, all Gen 1 moves, type chart
    ├── state.rs            BattleState / Action / Decision types, legal-action gen
    ├── calc.rs             Gen 1 damage formula (stat stages, screens, crits)
    ├── eval.rs             Static evaluator for minimax leaf nodes
    ├── sim.rs              Single-turn expected-value battle simulator
    ├── inference.rs        Opponent move inference (templates + coverage fallback)
    ├── minimax.rs          Alpha-beta minimax + iterative deepening
    └── mcts.rs             UCB1 MCTS with guided rollouts
```

All `.rs` files live in `src/` and are compiled into one binary.

---

## File layout

```
your_project/
├── competitive_player.py
├── gen1_engine.py
├── gen1_data.py
├── rust_engine_bridge.py
└── gen1_engine_rs/
    ├── Cargo.toml
    ├── competitive_player_rust_patch.py
    └── src/
        ├── main.rs
        ├── data.rs
        ├── state.rs
        ├── calc.rs
        ├── eval.rs
        ├── sim.rs
        ├── inference.rs
        ├── minimax.rs
        └── mcts.rs
```

---

## Build & run

### Prerequisites

- Rust stable ≥ 1.75 — install from https://rustup.rs
- `cargo` on your PATH (comes with rustup)

### Build

```bash
cd gen1_engine_rs
cargo build --release
# produces: target/release/gen1_engine_rs
```

### Smoke-test

```bash
python rust_engine_bridge.py
```

Expected output (exact scores and timings will vary):
```
[minimax ] {"type":"move","id":"bodyslam"} | score=320  nodes=2100  time=18ms  | guaranteed KO with bodyslam
[mcts    ] {"type":"move","id":"bodyslam"} | score=280  nodes=800   time=45ms  | bodyslam → ~62% avg dmg
[auto    ] {"type":"move","id":"bodyslam"} | score=290  nodes=800   time=40ms  | guaranteed KO with bodyslam
```

---

## Integration with competitive_player.py

See `competitive_player_rust_patch.py` for the complete annotated diff.
The short version:

```python
# 1. Import at the top of competitive_player.py
from rust_engine_bridge import RustEngine, build_state, action_to_poke_env

# 2. In CompetitivePlayer.__init__
self._rust_engine = RustEngine(algorithm="auto", depth=4, time_ms=200)
self._rust_call_count = 0

# 3. In the AMBIGUOUS branch of choose_move(), before calling the LLM
state  = build_state(battle)
result = self._rust_engine.choose(state)
poke_obj, atype = action_to_poke_env(result, battle)
if poke_obj:
    self._rust_call_count += 1
    return self.create_order(poke_obj)
# ... LLM fallback below as before ...

# 4. After all battles are done
player._rust_engine.close()
```

---

## Wire protocol

### Python → Rust (one JSON object per line on stdin)

```json
{
  "algorithm":            "auto",
  "depth":                4,
  "iterations":           800,
  "time_ms":              200,
  "infer_opponent_moves": true,
  "state": {
    "turn": 5,
    "ours": {
      "active": {
        "species":        "tauros",
        "hp_frac":        0.85,
        "moves":          ["bodyslam", "earthquake", "blizzard", "hyperbeam"],
        "status":         "None",
        "boosts":         {},
        "fainted":        false,
        "sub_hp_frac":    0.0,
        "sleep_turns":    0,
        "recharging":     false,
        "toxic_counter":  0,
        "trapping_turns": 0,
        "confused":       false,
        "confusion_turns":0,
        "crit_stage":     0,
        "disabled_move":  "",
        "disable_turns":  0
      },
      "bench":             [...],
      "reflect":           false,
      "reflect_turns":     0,
      "light_screen":      false,
      "light_screen_turns":0
    },
    "theirs": { "..." }
  }
}
```

All `BattlePoke` fields except `species`, `hp_frac`, and `moves` are optional
and default to zero / false if omitted. `build_state()` in `rust_engine_bridge.py`
populates them all from poke-env automatically.

Send `{"quit": true}` to shut the process down cleanly.

### Rust → Python (one JSON object per line on stdout)

```json
{
  "action":         { "type": "move", "id": "bodyslam" },
  "score":          320.5,
  "nodes_searched": 2100,
  "algorithm":      "minimax",
  "reason":         "guaranteed KO with bodyslam"
}
```

The three possible `action` shapes:

| Shape | Meaning |
|---|---|
| `{"type":"move",    "id":"bodyslam"}` | Use this move |
| `{"type":"switch",  "species":"starmie"}` | Switch to this Pokémon |
| `{"type":"recharge"}` | Forced recharge turn after Hyper Beam |

---

## How each module works

### `inference.rs` — Opponent move inference (always-on)

When the opponent's Pokémon has fewer than 4 known moves, the engine fills the
remaining slots before running search. This means the tree reasons about the
opponent's *likely* capability, not just what has been revealed.

Three-tier resolution:
1. **Known moves** — whatever is already in `theirs.active.moves` — never touched
2. **Species template** — 25 OU Pokémon have curated moveset lists (Tauros, Starmie,
   Alakazam, Chansey, Snorlax, Exeggutor, Zapdos, Lapras, Gengar, Rhydon, and more).
   The template whose moves best overlap with what's been observed is chosen, then
   its remaining slots fill the unknown slots.
3. **Generic fallback** — for unlisted species, picks the highest-BP moves that
   cover distinct types (maximises type coverage diversity).

To disable: pass `"infer_opponent_moves": false` in the request, or set
`infer_opp=False` when constructing `RustEngine`.

### `sim.rs` — Expected-value turn simulator

Both players' actions are applied simultaneously (switches first, then moves in
speed order). All RNG is resolved as expected value so the search tree is
deterministic:

| Mechanic | How it's modelled |
|---|---|
| Damage roll (217–255/255) | Average of min and max |
| Critical hits | Blended: 17% chance normal mons, 63% high-crit moves (Slash etc.) |
| Paralysis full-immobilisation | 25% chance → damage multiplied by 0.75 |
| Sleep | `sleep_turns` counts down each tick; status clears at 0 |
| Freeze | Immobilises like sleep; no natural thaw modelled |
| Burn | 1/16 HP per turn; Attack halved in damage formula |
| Poison | 1/16 HP per turn |
| Toxic | `toxic_counter / 16` HP per turn; counter increments each turn, resets on switch |
| Confusion | 50% self-hit at 40 BP physical, expected-value applied each turn |
| Hyper Beam | Sets `recharging = true`; next turn's only legal action is `Recharge` |
| Explosion / Selfdestruct | User HP → 0; target Defence halved in formula |
| Trapping (Wrap/Bind etc.) | Locks target in for ~3 expected turns |
| Substitute | Damage absorbed by `sub_hp_frac` before main HP |
| Reflect / Light Screen | 5-turn counters; double relevant Defence while active |
| Volatile reset | All volatile state (boosts, confusion, trapping, sub) clears on switch-out |
| Priority | Quick Attack +1, Counter -1 |

### `eval.rs` — Static evaluator

Used at leaf nodes in minimax and to seed MCTS rollouts.
Score is positive = better for our side. Terminal win ≈ +10 000.

| Term | Weight |
|---|---|
| Pokémon count difference | 600 per mon |
| Total team HP difference | 250 per full mon's HP |
| Active matchup (damage output delta) | up to ±400 |
| Status conditions | −75 (PSN) to −380 (FRZ) |
| Recharge penalty | −280 (opponent gets free turn) |
| Screen turns remaining | 15 per turn |
| Speed advantage (ratio) | up to ±80 |
| KO threat (can we/they KO this turn) | ±220 |
| Substitute HP | 180 per full sub |
| Confusion | ±90 |
| Trapping | ±60 |
| Bench quality (HP × matchup) | 0.4× weight |

### `minimax.rs` — Alpha-beta minimax

- **Simultaneous-choice approximation**: both players choose secretly in Gen 1.
  The engine treats the opponent as responding optimally to our move — a sound
  and standard approximation used by all competitive Pokémon engines.
- **Move ordering**: guaranteed KO → probable KO → damage% → switch.
  This puts the best moves first so alpha-beta prunes most branches early.
- **Iterative deepening**: runs depth 1 → 2 → … → N until the `time_ms`
  budget is consumed. The deepest fully-completed search is returned.
- **Late-game depth boost**: +2 extra ply automatically when ≤ 2 Pokémon
  remain per side (smaller branching factor allows deeper exact search).

### `mcts.rs` — Monte Carlo Tree Search

- **UCB1** selection with exploration constant `c = √2`.
- **Guided rollouts**: 80% of the time picks the highest-damage move;
  20% picks randomly. Converges much faster than pure random for Pokémon.
- **Robust child**: the final choice is the most-visited child, not the
  highest win-rate child — more stable under noisy rollout estimates.
- Default: 800 iterations / 200 ms.

### Auto mode

| Total Pokémon alive | Algorithm chosen | Reason |
|---|---|---|
| ≤ 4 | Minimax | Tree fits in budget; exact search is stronger |
| > 4 | MCTS | Wide branching factor; sampling handles it better |

---

## Known limitations

| Item | Status |
|---|---|
| Simultaneous choice exactness | Approximated (opponent sees our move) |
| Freeze natural thaw | Not modelled — treated as indefinite until Fire move hits |
| Sleep exact RNG (1–7 turns) | Modelled as fixed countdown from `sleep_turns` field |
| Accuracy / evasion stages | Tracked in boosts but not applied to hit-chance in sim |
| Endgame tablebase | Not implemented — future work for perfect 1v1 endings |
| Rayon parallelism | Single-threaded; MCTS tree parallelism is a future addition |
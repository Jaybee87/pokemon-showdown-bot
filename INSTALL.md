# Installation

## Requirements

- Python 3.12+
- Rust (stable) — for building the search engine
- A registered Pokemon Showdown account for the bot

---

## 1. Clone the repo

```bash
git clone git@github.com:Jaybee87/pokemon-showdown-bot.git
cd pokemon-showdown-bot
```

---

## 2. Python dependencies

```bash
pip install poke-env --break-system-packages
```

> `--break-system-packages` is needed on Ubuntu 24 / Pop!_OS with system Python.
> Use a virtualenv if you prefer isolation.

---

## 3. Build the Rust engine

```bash
cd gen1_engine_rs
cargo build --release
cd ..
```

The compiled binary is used automatically by `rust_engine_bridge.py`. Requires Rust stable — install via [rustup.rs](https://rustup.rs) if needed:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

---

## 4. Credentials

Create `credentials.py` in the project root:

```python
username = "YourBotUsername"
password = "YourBotPassword"
```

**Important:** use lowercase `username` and `password` — uppercase will not work.

The bot account must be registered at [play.pokemonshowdown.com](https://play.pokemonshowdown.com). This file is gitignored.

---

## 5. Add a team

Place a team file in `teams/` with the naming convention:

```
teams/team_ou_iteration_1.txt
```

The bot uses the highest-numbered iteration. Format: one Pokémon per block, moves prefixed with `- `, blocks separated by blank lines.

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

## 6. Go live

### Ladder mode

```bash
python3 main.py --ladder 50
```

The bot queues for ranked matchmaking and plays 50 games. Progress is shown after each battle. Reconnects automatically on network drops.

### Accept mode (recommended for new accounts)

```bash
python3 main.py --accept
```

The bot logs in and waits. From your personal account in the browser, type:

```
/challenge BotUsername, gen1ou
```

This bypasses Showdown's IP-based restrictions on new accounts.

### Challenge mode

```bash
python3 main.py --opponent YourPersonalAccount
```

The bot sends the challenge. May be blocked for new bot accounts — use accept mode instead.

### Options

```bash
python3 main.py --ladder 100                   # 100 ranked games
python3 main.py --accept --battles 5           # accept 5 consecutive challenges
python3 main.py --accept --format gen1uu       # different format
```

---

## Troubleshooting

**`credentials.py` uses `USERNAME`** — use lowercase: `username = "Bot"` not `USERNAME = "Bot"`

**"spam from your internet provider"** — Showdown blocks challenges from new accounts on flagged IPs. Use `--accept` mode instead (you challenge the bot from your browser).

**No team found** — place a team file at `teams/team_ou_iteration_1.txt`. See section 5 above.

**Websocket disconnects** — the bot reconnects automatically (up to 5 retries, 10s backoff). If it persists, check your internet connection.

**Rust engine not found** — run `cargo build --release` inside `gen1_engine_rs/`. Check that the binary path in `rust_engine_bridge.py` matches your OS.

**Same-IP ladder blocking** — Showdown prevents players on the same connection from being matched on the ladder. Don't queue yourself while the bot is laddering. Direct challenges (`--accept` mode) are not affected.

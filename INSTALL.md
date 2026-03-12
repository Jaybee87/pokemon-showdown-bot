# Installation

## Requirements

- Python 3.12+
- Node.js 18+ (for local Pokemon Showdown server)
- [Ollama](https://ollama.com) with a downloaded model
- GPU recommended (12GB+ VRAM for deepseek-r1:7b)

---

## 1. Clone the repo

```bash
git clone git@github.com:Jaybee87/pokemon-showdown-bot.git
cd pokemon-showdown-bot
```

---

## 2. Python dependencies

```bash
pip install poke-env ollama --break-system-packages
```

> `--break-system-packages` is needed on Ubuntu 24 / Pop!_OS with system Python.
> Use a virtualenv if you prefer isolation.

---

## 3. Local Pokemon Showdown server

Required for team building (`main.py`) and local testing (`competitive_player.py`).
Not needed for live play (`live_challenge.py`) — that connects directly to Showdown's servers.

```bash
git clone https://github.com/smogon/pokemon-showdown.git ~/pokemon-showdown
cd ~/pokemon-showdown
npm install

# Start the server (leave running in a separate terminal)
node pokemon-showdown start --no-security
```

Server runs on `localhost:8000` by default.

---

## 4. Ollama setup

### Install

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Download a model

```bash
ollama pull deepseek-r1:7b
```

### Using a different model

Set in `config.py` or override with an environment variable:

```bash
LLM_MODEL=deepseek-r1:14b python3 main.py
```

Alternatives: `deepseek-r1:14b` (better reasoning, ~10GB VRAM), `llama3.1:8b`, `mistral:7b`.

> Larger models produce significantly better battle decisions. The 7b model occasionally hallucinates type matchups. 14b+ is recommended for live ladder play.

### Verify

```bash
ollama run deepseek-r1:7b "say hello"
```

---

## 5. Credentials (for live play only)

Create `credentials.py` in the project root:

```python
username = "YourBotUsername"
password = "YourBotPassword"
```

**Important:** use lowercase `username` and `password` — uppercase `USERNAME` will not work.

The bot account must be registered at [play.pokemonshowdown.com](https://play.pokemonshowdown.com). This file is gitignored.

---

## 6. Build a team

```bash
python3 main.py
```

This runs preflight checks, fetches Gen 1 data (first run only), then walks you through:

1. **Anchor selection** — pick your lead Pokemon from the OU roster or type any name
2. **Team composition** — Python analyses type coverage and selects complementary teammates
3. **Move selection** — LLM picks 4 moves per Pokemon from their legal move pool
4. **Stress testing** — runs battles vs random opponents and self-play
5. **Iteration** — feeds battle results back into the next team generation

Teams and feedback are saved to `teams/`:

```
teams/team_ou_iteration_1.txt
teams/feedback_ou_iteration_1.txt
teams/team_ou_iteration_2.txt
...
```

Options:

```bash
python3 main.py --anchor gengar        # skip interactive prompt
python3 main.py --iterations 10        # more improvement cycles
python3 main.py --battles 20           # more battles per test
python3 main.py --rebuild-data         # force refresh Pokemon data from pokered
```

---

## 7. Go live

### Accept mode (recommended)

```bash
python3 live_challenge.py --accept
```

The bot logs into Showdown and waits. From your personal account in the browser, type:

```
/challenge BotUsername, gen1ou
```

This bypasses Showdown's IP-based restrictions on new accounts.

### Challenge mode

```bash
python3 live_challenge.py --opponent YourPersonalAccount
```

The bot sends the challenge. May be blocked for new bot accounts — use accept mode instead.

### Options

```bash
python3 live_challenge.py --accept --battles 5      # accept 5 consecutive challenges
python3 live_challenge.py --accept --format gen1uu   # different format
```

### What you'll see

Compact one-liner per turn on the console:

```
  ⚡ T01 gengar(100%) vs alakazam(100%) → nightshade [py]
  🤖 T03 exeggutor(85%) vs alakazam(69%) → psychic [llm]
  ⚡ T06 rhydon(100%) vs zapdos(46%) → rockslide [py]
```

Full verbose reasoning is saved to `live_logs/live_log_NNN.txt`.

### Battle timer

The bot auto-starts Showdown's battle timer. If your opponent disconnects, they'll auto-forfeit after the timeout. No more hanging battles.

---

## 8. Local testing (optional)

Test the decision engine locally without connecting to Showdown:

```bash
python3 competitive_player.py --battles 5
```

Requires the local Showdown server running. Logs saved to `live_logs/competitive_log_NNN.txt`.

---

## File overview

| File | Purpose |
|------|---------|
| `main.py` | Single entry point — preflight, team build, stress test, iterate |
| `live_challenge.py` | Live Showdown — accept or send challenges |
| `competitive_player.py` | Hybrid Python/LLM decision engine |
| `gen1_engine.py` | Gen 1 type chart + effectiveness + move scoring |
| `gen1_data.py` | Pokemon/move data from pokered ASM + Showdown tiers |
| `team_generator.py` | LLM team builder with battle feedback loop |
| `battle_runner.py` | Local stress tester (random + self-play) |
| `llm_bridge.py` | LLM call wrapper (thread-safe timeout, parsing) |
| `config.py` | Central config — model, servers, format |
| `credentials.py` | Bot login for live Showdown (gitignored) |
| `teams/` | Generated teams + feedback per iteration (gitignored) |
| `live_logs/` | Battle logs from live + local play (gitignored) |

---

## Troubleshooting

**"credentials.py uses USERNAME"** — use lowercase: `username = "Bot"` not `USERNAME = "Bot"`

**"spam from your internet provider"** — Showdown blocks challenges from new accounts on flagged IPs. Use `--accept` mode instead (you challenge the bot from your browser).

**"signal only works in main thread"** — you're running an old version of `llm_bridge.py`. The current version uses thread-pool timeouts.

**LLM timeout errors** — increase the timeout in `config.py`: `LLM_TIMEOUT_SECONDS = 60`. Or use a faster model.

**"gen1_data.json not found"** — run `python3 main.py` first. It auto-builds from pokered on first run. Needs internet access and a local Showdown install.
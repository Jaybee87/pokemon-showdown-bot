# Installation

## Requirements

- Python 3.12+
- Node.js 18+ (for local Pokemon Showdown server)
- [Ollama](https://ollama.com) with a downloaded model
- GPU recommended (12GB+ VRAM for deepseek-r1:14b)

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

Required for team building (`main.py`) and local testing.
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
ollama pull deepseek-r1:14b
```

The 14b model is the recommended default. It fits in 12GB VRAM with `num_ctx: 2048` (configured automatically at runtime — no custom Modelfile needed).

### Alternative models

```bash
ollama pull deepseek-r1:7b     # lighter, faster, less accurate decisions
ollama pull qwen2.5:14b        # no thinking tokens, faster inference
ollama pull mistral:7b          # fast, good at structured output
```

Override the model with an environment variable:

```bash
LLM_MODEL=deepseek-r1:7b python3 live_challenge.py --ladder 20
```

Other configurable options:

```bash
LLM_CONTEXT=4096               # context length (default: 2048)
LLM_LIVE_TIMEOUT=20            # LLM response timeout in seconds (default: 25)
```

### Verify

```bash
ollama run deepseek-r1:14b "say hello"
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
4. **Stress testing** — runs battles vs random opponents locally
5. **Iteration** — feeds battle results back into the next team generation

Teams are saved to `teams/team_ou_iteration_N.txt`.

Options:

```bash
python3 main.py --anchor gengar        # skip interactive prompt
python3 main.py --iterations 10        # more improvement cycles
python3 main.py --battles 20           # more battles per test
python3 main.py --rebuild-data         # force refresh Pokemon data from pokered
```

Or create a team file manually in `teams/` — the bot uses the highest-numbered iteration.

---

## 7. Go live

### Ladder mode

```bash
python3 live_challenge.py --ladder 50
```

The bot queues for ranked matchmaking and plays 50 games. Progress is shown after each battle. Reconnects automatically on network drops.

### Accept mode (recommended for new accounts)

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
python3 live_challenge.py --ladder 100              # 100 ranked games
python3 live_challenge.py --accept --battles 5      # accept 5 consecutive challenges
python3 live_challenge.py --accept --format gen1uu   # different format
```

### What you'll see

```
⚡ T01 tauros(100%) vs starmie(100%) → bodyslam [py]
🎯 PYTHON GUARANTEED KO: surf finishes rhydon at 28%
🔄 PYTHON MATCHUP SWITCH: snorlax is a better matchup vs chansey (+96 points)
🤖 T08 alakazam(72%) vs exeggutor(50%) → seismictoss [llm]

============================================================
BATTLE OVER — WON ✓ in 25 turns
  Python decisions: 18
  LLM decisions:    7
  LLM involvement:  28% of turns
============================================================
📈 Progress: 14/50 (8W / 6L)
  Python decisions: 312
  LLM decisions:    89
  LLM involvement:  22% of turns
============================================================
```

`⚡` = Python fast-path, `🤖` = LLM decision, `🎯` = damage calc KO, `🔄` = matchup switch.

Full verbose reasoning is saved to `live_logs/live_log_NNN.txt`.

---

## Troubleshooting

**"credentials.py uses USERNAME"** — use lowercase: `username = "Bot"` not `USERNAME = "Bot"`

**"spam from your internet provider"** — Showdown blocks challenges from new accounts on flagged IPs. Use `--accept` mode (you challenge the bot from your browser).

**LLM timeout errors** — increase the timeout: `LLM_LIVE_TIMEOUT=30 python3 live_challenge.py --ladder 20`. Or use a faster/smaller model.

**"gen1_data.json not found"** — run `python3 main.py` first. It auto-builds from pokered on first run. Needs a local Showdown install.

**Websocket disconnects** — the bot reconnects automatically (up to 5 retries). If it persists, check your internet connection or increase ping tolerance in `live_challenge.py`.

**High VRAM usage** — the default `num_ctx: 2048` keeps the 14b model under 11GB. If you're hitting limits, try `LLM_CONTEXT=1024` or switch to the 7b model.

**Same-IP ladder blocking** — Showdown prevents players on the same connection from being matched on the ladder. Don't queue yourself while the bot is laddering. Direct challenges (`--accept` mode) are not affected.
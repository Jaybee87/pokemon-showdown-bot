# Installation

## Requirements

- Python 3.12+
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

## 3. Ollama setup

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
LLM_MODEL=deepseek-r1:7b python3 main.py --ladder 20
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

## 4. Credentials

Create `credentials.py` in the project root:

```python
username = "YourBotUsername"
password = "YourBotPassword"
```

**Important:** use lowercase `username` and `password` — uppercase `USERNAME` will not work.

The bot account must be registered at [play.pokemonshowdown.com](https://play.pokemonshowdown.com). This file is gitignored.

---

## 5. Add a team

Place a team file in `teams/` with the naming convention:

```
teams/team_ou_iteration_1.txt
```

The bot uses the highest-numbered iteration. Format: one Pokemon per block, moves prefixed with `- `, blocks separated by blank lines.

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

The bot logs into Showdown and waits. From your personal account in the browser, type:

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

**"credentials.py uses USERNAME"** — use lowercase: `username = "Bot"` not `USERNAME = "Bot"`

**"spam from your internet provider"** — Showdown blocks challenges from new accounts on flagged IPs. Use `--accept` mode (you challenge the bot from your browser).

**LLM timeout errors** — increase the timeout: `LLM_LIVE_TIMEOUT=30 python3 main.py --ladder 20`. Or use a faster/smaller model.

**No team found** — place a team file in `teams/team_ou_iteration_1.txt`. See section 5 above.

**Websocket disconnects** — the bot reconnects automatically (up to 5 retries). If it persists, check your internet connection or increase ping tolerance in `live_challenge.py`.

**High VRAM usage** — the default `num_ctx: 2048` keeps the 14b model under 11GB. If you're hitting limits, try `LLM_CONTEXT=1024` or switch to the 7b model.

**Same-IP ladder blocking** — Showdown prevents players on the same connection from being matched on the ladder. Don't queue yourself while the bot is laddering. Direct challenges (`--accept` mode) are not affected.
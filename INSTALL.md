# Installation

## Requirements

- Python 3.12+
- Node.js 18+ (for local Pokemon Showdown server)
- [Ollama](https://ollama.com) with a downloaded model
- GPU recommended (RTX 5070 or similar — 12GB VRAM runs deepseek-r1:7b at ~40% utilisation)

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

> **Note:** `--break-system-packages` is required on Pop!_OS / Ubuntu 24 with system Python.
> Use a virtualenv if you prefer to keep things isolated.

---

## 3. Local Pokemon Showdown server

The bot runs against a local Showdown instance with security disabled.

```bash
# Clone Showdown if you don't have it
git clone https://github.com/smogon/pokemon-showdown.git
cd pokemon-showdown
npm install

# Start the server (leave running in a separate terminal)
node pokemon-showdown start --no-security
```

Server runs on `localhost:8000` by default. The bot is configured for this — no changes needed.

---

## 4. Ollama and model setup

This project uses [Ollama](https://ollama.com) as the local LLM backend. The decision engine calls Ollama's API to resolve ambiguous battle situations.

### Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Download a model

The bot is currently configured for `deepseek-r1:7b`:

```bash
ollama pull deepseek-r1:7b
```

### Using a different model

The model name is set in `competitive_player.py` and `team_generator.py`. Search for:

```python
model="deepseek-r1:7b"
```

Replace with any model available in your Ollama installation, for example:

```python
model="deepseek-r1:14b"     # Better reasoning, needs ~10GB VRAM
model="llama3.1:8b"         # Alternative if deepseek unavailable
model="mistral:7b"           # Lighter option
```

List your locally available models:

```bash
ollama list
```

> **Model quality note:** Larger models produce significantly better battle decisions.
> The 7b model occasionally hallucinates type matchups and move properties.
> A 14b+ model is recommended once you're ready to go live on the Showdown ladder.

### Verify Ollama is running

```bash
ollama run deepseek-r1:7b "say hello"
```

Ollama must be running as a service before launching the bot. On most systems it starts automatically after install. If not:

```bash
ollama serve
```

---

## 5. Run the bot

```bash
# Make sure Showdown server is running first (step 3)

# Single battle
python3 competitive_player.py

# Multiple battles
python3 competitive_player.py --battles 10

# Run the team builder iteration loop
python3 main.py
```

---

## Switching to a different LLM backend

The bot currently uses Ollama exclusively. All LLM calls go through two functions:

- `call_llm_for_decision()` in `competitive_player.py`
- The team generation prompts in `team_generator.py`

Both use the `ollama` Python library:

```python
import ollama
response = ollama.chat(
    model="deepseek-r1:7b",
    messages=[{"role": "user", "content": prompt}]
)
raw = response['message']['content']
```

To swap in a different backend (e.g. Anthropic API, OpenAI, local llama.cpp), replace these calls with your preferred client. The prompt strings and response parsing are backend-agnostic — only the `ollama.chat(...)` call needs changing.

---

## File locations (defaults)

| Path | Purpose |
|------|---------|
| `~/pokemon-showdown/` | Local Showdown server |
| `~/pokemon-showdown-bot/` | This repo |
| `team_ou_iteration_N.txt` | Generated teams (auto-loaded by bot) |
| `competitive_log_NNN.txt` | Battle logs (auto-numbered) |
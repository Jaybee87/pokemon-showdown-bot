# Pokemon Showdown Bot

A Gen 1 OU Pokemon AI system with two components:

## showdown.py
Live battle advisor — connects to Pokemon Showdown via websocket and uses a local Ollama LLM to suggest moves in real time.

## Team Builder Pipeline
Automated team generation and testing loop:
- `gen1_data.py` — parses Showdown's own data files to build a validated Gen 1 OU move pool
- `team_generator.py` — uses a local LLM to generate teams from the legal pool
- `battle_runner.py` — runs teams against opponents via poke-env and collects performance data
- `main.py` — orchestrates the full generate → battle → feedback loop

## Requirements
- Local Pokemon Showdown server
- Ollama with qwen2.5:7b
- poke-env

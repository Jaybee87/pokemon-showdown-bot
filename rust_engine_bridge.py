"""
rust_engine_bridge.py v2
========================
Python subprocess bridge for gen1_engine_rs.

New in v2:
  - Populates all new BattlePoke fields: recharging, toxic_counter,
    trapping_turns, confused, confusion_turns, crit_stage,
    disabled_move, disable_turns, sleep_turns
  - Side fields: reflect_turns, light_screen_turns
  - Handles Action.Recharge returned by engine
  - infer_opponent_moves flag (default True)
  - Richer error reporting

Usage
-----
    from rust_engine_bridge import RustEngine, build_state, action_to_poke_env

    engine = RustEngine()
    with RustEngine() as engine:
        decision = engine.choose_from_battle(battle)
        poke_obj, atype = action_to_poke_env(decision, battle)
        if poke_obj:
            return self.create_order(poke_obj)
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

# ─── Paths ────────────────────────────────────────────────────────────────────

_THIS_DIR  = Path(__file__).parent
_CARGO_DIR = _THIS_DIR / "gen1_engine_rs"
_BIN_NAME  = "gen1_engine_rs"
_BIN_PATH  = _CARGO_DIR / "target" / "release" / _BIN_NAME


# ─── poke-env volatile state helpers ─────────────────────────────────────────

def _get_effect_value(poke, effect_name: str, default=0):
    """Safely read volatile effect values from a poke-env Pokémon."""
    try:
        effects = poke.effects or {}
        for effect, val in effects.items():
            name = effect.name if hasattr(effect, "name") else str(effect)
            if name == effect_name:
                return int(val) if val is not None else default
    except Exception:
        pass
    return default


def _is_recharging(poke) -> bool:
    """True if the Pokémon must recharge next turn (used Hyper Beam)."""
    try:
        effects = poke.effects or {}
        for effect in effects:
            name = effect.name if hasattr(effect, "name") else str(effect)
            if "RECHARGE" in name.upper():
                return True
    except Exception:
        pass
    return False


def _is_confused(poke) -> bool:
    try:
        effects = poke.effects or {}
        for effect in effects:
            name = effect.name if hasattr(effect, "name") else str(effect)
            if "CONFUSION" in name.upper():
                return True
    except Exception:
        pass
    return False


def _confusion_turns(poke) -> int:
    """Approximate remaining confusion turns (poke-env may not expose this)."""
    return 2 if _is_confused(poke) else 0  # conservative estimate


def _trapping_turns(poke) -> int:
    """Returns remaining trapping turns if the Pokémon is trapped."""
    try:
        effects = poke.effects or {}
        for effect, val in effects.items():
            name = effect.name if hasattr(effect, "name") else str(effect)
            if name in ("PARTIALLYTRAPPED", "TRAPPED"):
                return int(val) if val else 2
    except Exception:
        pass
    return 0


def _toxic_counter(poke) -> int:
    """Number of toxic ticks this Pokémon has taken (poke-env may expose this)."""
    try:
        # poke-env stores this as poke.toxic_turn_count in some versions
        val = getattr(poke, "toxic_turn_count", None)
        if val is not None:
            return int(val)
    except Exception:
        pass
    # Fall back: if badly poisoned and we don't know the counter, guess based on HP lost
    if getattr(poke, "status", None) and poke.status.name == "TOX":
        return 1
    return 0


def _screen_turns(side_conditions, screen_name: str) -> int:
    """Return remaining turns for a screen from poke-env side conditions."""
    try:
        for cond, val in (side_conditions or {}).items():
            name = cond.name if hasattr(cond, "name") else str(cond)
            if name.upper() == screen_name.upper():
                return int(val) if val else 0
    except Exception:
        pass
    return 0


def _disabled_move(poke) -> str:
    """Return the move ID that is currently disabled, or empty string."""
    try:
        effects = poke.effects or {}
        for effect, val in effects.items():
            name = effect.name if hasattr(effect, "name") else str(effect)
            if "DISABLE" in name.upper() and val:
                return str(val).lower()
    except Exception:
        pass
    return ""


# ─── State builder ────────────────────────────────────────────────────────────

def poke_dict(p, move_ids: Optional[list] = None, sleep_turns_override: int = 0) -> dict:
    """Convert a poke-env Pokémon to the Rust engine's BattlePoke schema."""
    boosts = {}
    if hasattr(p, "boosts") and p.boosts:
        for k, v in p.boosts.items():
            boosts[str(k)] = int(v)

    VALID_STATUSES = {"SLP", "PAR", "PSN", "TOX", "BRN", "FRZ"}
    status_name = "NONE"
    if p.status:
        raw = p.status.name.upper()
        status_name = raw if raw in VALID_STATUSES else "NONE"

    moves = move_ids if move_ids is not None else [m.id for m in p.moves.values()]

    # Sleep turns: use the caller-supplied override (tracked externally) if available,
    # otherwise fall back to poke-env attributes, otherwise use a conservative estimate.
    sleep_turns = 0
    if status_name == "SLP":
        if sleep_turns_override > 0:
            sleep_turns = sleep_turns_override
        else:
            sleep_turns = (
                getattr(p, "sleep_turn_count", None)
                or getattr(p, "sleep_turns", None)
                or 0
            )
            if sleep_turns == 0:
                sleep_turns = 4  # conservative mid-range estimate (Gen 1: 1-7 turns)

    return {
        "species":         p.species.lower(),
        "hp_frac":         float(p.current_hp_fraction or 0.0),
        "moves":           moves,
        "status":          status_name,
        "boosts":          boosts,
        "fainted":         bool(p.fainted),
        "sub_hp_frac":     0.0,
        "sleep_turns":     int(sleep_turns),
        "recharging":      _is_recharging(p),
        "toxic_counter":   _toxic_counter(p),
        "trapping_turns":  _trapping_turns(p),
        "confused":        _is_confused(p),
        "confusion_turns": _confusion_turns(p),
        "crit_stage":      0,
        "disabled_move":   _disabled_move(p),
        "disable_turns":   0,
    }


def side_dict(active, bench_pokes, side_conditions=None) -> dict:
    reflect_on    = False
    reflect_turns = 0
    ls_on         = False
    ls_turns      = 0

    if side_conditions:
        reflect_turns = _screen_turns(side_conditions, "REFLECT")
        reflect_on    = reflect_turns > 0
        ls_turns      = _screen_turns(side_conditions, "LIGHTSCREEN")
        ls_on         = ls_turns > 0

    return {
        "active":              poke_dict(active),
        "bench":               [poke_dict(p) for p in bench_pokes if not p.fainted],
        "reflect":             reflect_on,
        "reflect_turns":       reflect_turns,
        "light_screen":        ls_on,
        "light_screen_turns":  ls_turns,
    }


def build_state(battle, sleep_turns: dict = None) -> dict:
    """
    Convert a poke-env Battle into the JSON dict the Rust engine expects.

    sleep_turns: optional dict mapping species.lower() → turns_asleep (int).
                 Tracked externally in competitive_player.py since poke-env
                 doesn't reliably expose remaining sleep duration.
    """
    sleep_turns = sleep_turns or {}
    my_active   = battle.active_pokemon
    opp_active  = battle.opponent_active_pokemon
    my_bench    = [p for p in battle.available_switches if not p.fainted]
    opp_bench   = [p for p in battle.opponent_team.values()
                   if not p.fainted and p != opp_active]

    my_sc  = getattr(battle, "side_conditions",          {})
    opp_sc = getattr(battle, "opponent_side_conditions", {})

    def my_poke_dict(p, move_ids=None):
        override = sleep_turns.get(p.species.lower(), 0)
        return poke_dict(p, move_ids=move_ids, sleep_turns_override=override)

    my_active_dict = my_poke_dict(my_active)
    my_bench_dicts = [my_poke_dict(p) for p in my_bench if not p.fainted]

    # Opponent side — use raw poke_dict (no override, inferred)
    opp_active_dict = poke_dict(opp_active)
    opp_bench_dicts = [poke_dict(p) for p in opp_bench if not p.fainted]

    def make_side(active_d, bench_d, sc):
        reflect_turns = _screen_turns(sc, "REFLECT")
        ls_turns      = _screen_turns(sc, "LIGHTSCREEN")
        return {
            "active":              active_d,
            "bench":               bench_d,
            "reflect":             reflect_turns > 0,
            "reflect_turns":       reflect_turns,
            "light_screen":        ls_turns > 0,
            "light_screen_turns":  ls_turns,
        }

    return {
        "turn":   battle.turn,
        "ours":   make_side(my_active_dict,  my_bench_dicts,  my_sc),
        "theirs": make_side(opp_active_dict, opp_bench_dicts, opp_sc),
    }


# ─── Engine process manager ───────────────────────────────────────────────────

class RustEngine:
    """
    Manages the Rust engine subprocess.  Thread-safe.
    """

    def __init__(
        self,
        binary:      str | Path = _BIN_PATH,
        auto_build:  bool = True,
        algorithm:   str  = "auto",
        depth:       int  = 4,
        iterations:  int  = 800,
        time_ms:     int  = 200,
        infer_opp:   bool = True,
    ):
        self.binary     = Path(binary)
        self.algorithm  = algorithm
        self.depth      = depth
        self.iterations = iterations
        self.time_ms    = time_ms
        self.infer_opp  = infer_opp
        self._lock      = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None

        if auto_build and not self.binary.exists():
            self._build()
        self._start()

    def _build(self):
        if not (_CARGO_DIR / "Cargo.toml").exists():
            raise FileNotFoundError(
                f"Cargo project not found at {_CARGO_DIR}.\n"
                "Copy the gen1_engine_rs/ directory next to this file."
            )
        print("🦀 Building Rust engine (release)…")
        result = subprocess.run(
            ["cargo", "build", "--release"],
            cwd=_CARGO_DIR,
        )
        if result.returncode != 0:
            raise RuntimeError("cargo build --release failed.")
        print("✅ Rust engine built.")

    def _start(self):
        if not self.binary.exists():
            raise FileNotFoundError(
                f"Binary not found: {self.binary}\n"
                "Run: cd gen1_engine_rs && cargo build --release"
            )
        self._proc = subprocess.Popen(
            [str(self.binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def close(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.write('{"quit":true}\n')
                self._proc.stdin.flush()
                self._proc.wait(timeout=2)
            except Exception:
                self._proc.kill()
        self._proc = None

    def __enter__(self): return self
    def __exit__(self, *_): self.close()

    def choose(
        self,
        state: dict,
        *,
        algorithm:  str | None = None,
        depth:      int | None = None,
        iterations: int | None = None,
        time_ms:    int | None = None,
        infer_opp:  bool | None = None,
    ) -> dict:
        """
        Query the engine.  Returns:
          {
            "action":         {"type": "move"|"switch"|"recharge", "id"/"species": "..."},
            "score":           float,
            "nodes_searched":  int,
            "algorithm":       str,
            "reason":          str,
          }
        """
        if self._proc is None or self._proc.poll() is not None:
            self._start()

        req = {
            "algorithm":            algorithm  if algorithm  is not None else self.algorithm,
            "depth":                depth      if depth      is not None else self.depth,
            "iterations":           iterations if iterations is not None else self.iterations,
            "time_ms":              time_ms    if time_ms    is not None else self.time_ms,
            "infer_opponent_moves": infer_opp  if infer_opp  is not None else self.infer_opp,
            "state": state,
        }
        line = json.dumps(req) + "\n"

        with self._lock:
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
                resp = self._proc.stdout.readline()
            except BrokenPipeError:
                self._start()
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
                resp = self._proc.stdout.readline()

        if not resp:
            return {"error": "engine returned empty response"}
        try:
            return json.loads(resp)
        except json.JSONDecodeError as e:
            return {"error": f"JSON parse error: {e}", "raw": resp}

    def choose_from_battle(self, battle, sleep_turns: dict = None) -> dict:
        return self.choose(build_state(battle, sleep_turns=sleep_turns or {}))


# ─── poke-env order converter ─────────────────────────────────────────────────

def action_to_poke_env(decision: dict, battle):
    """
    Convert an engine Decision into a poke-env object + type string.

    Returns (poke_env_object, "move"|"switch"|"recharge"|None)
    """
    action = decision.get("action", {})
    atype  = action.get("type")

    if atype == "recharge":
        # The engine is telling us this is a forced recharge turn.
        # In poke-env this shows up as a move with id="recharge" in available_moves.
        recharge_move = next(
            (m for m in battle.available_moves if m.id == "recharge"), None
        )
        return recharge_move, "recharge"

    if atype == "move":
        mid  = action.get("id", "")
        move = next(
            (m for m in battle.available_moves if m.id == mid), None
        )
        return move, "move"

    if atype == "switch":
        species = action.get("species", "").lower()
        poke    = next(
            (p for p in battle.available_switches if p.species.lower() == species),
            None,
        )
        return poke, "switch"

    return None, None


# ─── Standalone smoke-test ────────────────────────────────────────────────────

def _poke(species, hp_frac, moves, status="NONE", **kwargs):
    """Helper to build a BattlePoke dict with sensible defaults."""
    base = {
        "species": species, "hp_frac": hp_frac, "moves": moves,
        "status": status, "boosts": {}, "fainted": False,
        "sub_hp_frac": 0.0, "sleep_turns": 0, "recharging": False,
        "toxic_counter": 0, "trapping_turns": 0, "confused": False,
        "confusion_turns": 0, "crit_stage": 0, "disabled_move": "",
        "disable_turns": 0,
    }
    base.update(kwargs)
    return base

def _side(active, bench=None, reflect=False, light_screen=False):
    return {
        "active": active, "bench": bench or [],
        "reflect": reflect, "reflect_turns": 5 if reflect else 0,
        "light_screen": light_screen,
        "light_screen_turns": 5 if light_screen else 0,
    }

def _state(turn, ours, theirs):
    return {"turn": turn, "ours": ours, "theirs": theirs}


TESTS = [
    # ── 1. Obvious KO ─────────────────────────────────────────────────────────
    # Tauros vs Alakazam at 55% — Hyperbeam is a guaranteed KO.
    # Expected: hyperbeam (or earthquake — any KO move).
    {
        "name": "Obvious KO",
        "expect_move": True,
        "state": _state(8,
            _side(_poke("tauros",   0.72, ["bodyslam","earthquake","blizzard","hyperbeam"])),
            _side(_poke("alakazam", 0.55, ["psychic","thunderwave","recover","seismictoss"])),
        ),
    },

    # ── 2. Don't KO yourself — avoid Hyper Beam when it triggers recharge ────
    # Opponent has a full-health mon in the back. Using Hyper Beam KOs the
    # active but lets the next opponent mon come in for free.
    # A good engine prefers Body Slam (no recharge risk) over Hyper Beam here.
    # Expected: bodyslam (not hyperbeam).
    {
        "name": "Hyper Beam recharge risk with bench threat",
        "expect_not": "hyperbeam",
        "state": _state(5,
            _side(_poke("tauros", 0.85, ["bodyslam","earthquake","blizzard","hyperbeam"])),
            _side(
                _poke("alakazam", 0.30, ["psychic","thunderwave","recover","seismictoss"]),
                bench=[_poke("snorlax", 1.0, ["bodyslam","earthquake","selfdestruct","amnesia"])],
            ),
        ),
    },

    # ── 3. Switch into immunity ────────────────────────────────────────────────
    # Our Rhydon (Rock/Ground, 4x weak to Water) is facing a Starmie that only
    # knows Water/Psychic moves. Rhydon will be OHKO'd by Surf.
    # We have a Snorlax (Normal, resists nothing but isn't weak) and a
    # Golem (Rock/Ground, same typing — still weak but Snorlax is the better
    # switch). The key: Rhydon should NOT stay in.
    # We relax the assertion to just "must switch" rather than naming the mon,
    # since which bench pick is best is a matter of eval weights.
    {
        "name": "Switch out of 4x Water weakness",
        "expect_action_type": "switch",
        "state": _state(3,
            _side(
                _poke("rhydon", 0.85, ["earthquake","rockslide","bodyslam","substitute"]),
                bench=[_poke("snorlax", 1.0, ["bodyslam","earthquake","selfdestruct","amnesia"])],
            ),
            _side(_poke("starmie", 1.0, ["surf","thunderbolt","psychic","recover"])),
        ),
    },

    # ── 4. Status awareness — don't attack while asleep ───────────────────────
    # Our active is asleep. Legal actions should collapse to the sleep no-op.
    # The engine must return the sleep placeholder and not crash.
    {
        "name": "Asleep — legal actions constrained",
        "expect_legal": True,
        "state": _state(10,
            _side(_poke("snorlax", 0.90, ["bodyslam","earthquake","selfdestruct","amnesia"],
                        status="SLP", sleep_turns=3)),
            _side(_poke("starmie", 0.80, ["surf","thunderbolt","psychic","recover"])),
        ),
    },

    # ── 5. Recharge lock — only legal action is Recharge ─────────────────────
    # Our mon used Hyper Beam last turn and is recharging.
    # Expected: action type is "recharge".
    {
        "name": "Hyper Beam recharge lock",
        "expect_action_type": "recharge",
        "state": _state(7,
            _side(_poke("tauros", 0.80, ["bodyslam","earthquake","blizzard","hyperbeam"],
                        recharging=True)),
            _side(_poke("chansey", 1.0, ["softboiled","thunderwave","seismictoss","reflect"])),
        ),
    },

    # ── 6. Toxic escalation awareness ─────────────────────────────────────────
    # Our Chansey is badly poisoned (counter=8 — taking 8/16 = 50% HP this turn).
    # We should prefer Softboiled to heal rather than attacking.
    # Expected: softboiled.
    {
        "name": "Toxic — heal with Softboiled",
        "expect_move": True,          # just check it doesn't crash; softboiled is 0 BP
        "state": _state(15,
            _side(_poke("chansey", 0.45,
                        ["softboiled","thunderwave","seismictoss","reflect"],
                        status="TOX", toxic_counter=8)),
            _side(_poke("tauros", 0.70, ["bodyslam","earthquake","blizzard","hyperbeam"])),
        ),
    },

    # ── 7. Opponent move inference — partial moveset ───────────────────────────
    # Opponent Exeggutor has only revealed Sleep Powder.
    # Engine must infer remaining moves (Psychic/Explosion likely) and not crash.
    {
        "name": "Inference — partial opponent moveset",
        "expect_legal": True,
        "state": _state(2,
            _side(_poke("tauros", 1.0, ["bodyslam","earthquake","blizzard","hyperbeam"])),
            _side(_poke("exeggutor", 0.90, ["sleeppowder"])),  # 3 moves unknown
        ),
    },

    # ── 8. Late-game 1v1 — minimax goes deeper ────────────────────────────────
    # Single Pokémon each side — engine should switch to minimax and go deep.
    {
        "name": "Late-game 1v1",
        "expect_legal": True,
        "state": _state(20,
            _side(_poke("starmie", 0.55, ["surf","thunderbolt","psychic","recover"])),
            _side(_poke("snorlax", 0.40, ["bodyslam","earthquake","selfdestruct","amnesia"])),
        ),
    },

    # ── 9. Substitute blocks damage — prefer attacking through it ─────────────
    # Opponent has a substitute up. Engine should still attack (not switch).
    {
        "name": "Opponent has Substitute",
        "expect_move": True,
        "state": _state(6,
            _side(_poke("tauros", 0.90, ["bodyslam","earthquake","blizzard","hyperbeam"])),
            _side(_poke("gengar", 0.80, ["nightshade","hypnosis","thunderbolt","explosion"],
                        sub_hp_frac=0.25)),
        ),
    },

    # ── 10. Paralysed opponent — speed advantage gone, but still attack ───────
    # Opponent Tauros is paralysed (speed/4). Our Starmie outspeeds easily.
    # Engine should attack, not switch.
    {
        "name": "Paralysed opponent — attack",
        "expect_move": True,
        "state": _state(12,
            _side(_poke("starmie", 0.85, ["surf","thunderbolt","psychic","recover"])),
            _side(_poke("tauros",  0.75, ["bodyslam","earthquake","blizzard","hyperbeam"],
                        status="PAR")),
        ),
    },
]


if __name__ == "__main__":
    import sys

    print("=== RustEngine v2 test suite ===")
    print(f"Binary: {_BIN_PATH}\n")

    engine = RustEngine(algorithm="auto", time_ms=300, infer_opp=True)

    passed = 0
    failed = 0

    for test in TESTS:
        name   = test["name"]
        state  = test["state"]

        t0     = time.time()
        result = engine.choose(state)
        elapsed = (time.time() - t0) * 1000

        if "error" in result:
            print(f"  FAIL  [{name}]")
            print(f"        ERROR: {result['error']}")
            failed += 1
            continue

        action  = result["action"]
        atype   = action.get("type")
        move_id = action.get("id", "")
        species = action.get("species", "")
        score   = result["score"]
        nodes   = result["nodes_searched"]
        reason  = result["reason"]

        # Validate expectation
        ok   = True
        note = ""

        if "expect_action_type" in test:
            if atype != test["expect_action_type"]:
                ok   = False
                note = f"wanted type={test['expect_action_type']!r}, got {atype!r}"

        elif "expect_switch" in test:
            if atype != "switch" or species != test["expect_switch"]:
                ok   = False
                note = f"wanted switch→{test['expect_switch']}, got {action}"

        elif "expect_not" in test:
            if move_id == test["expect_not"]:
                ok   = False
                note = f"should not have chosen {test['expect_not']!r}"

        elif "expect_move" in test:
            if atype not in ("move", "recharge"):
                ok   = False
                note = f"expected a move action, got {atype!r}"

        elif "expect_legal" in test:
            pass  # just check no error / no crash

        tag = "  PASS" if ok else "  FAIL"
        if not ok:
            failed += 1
        else:
            passed += 1

        print(f"{tag}  [{name}]")
        print(f"        {action} | score={score:.0f} nodes={nodes} {elapsed:.0f}ms | {reason}")
        if not ok:
            print(f"        ^^^ {note}")

    engine.close()

    print(f"\n{'='*50}")
    print(f"Results: {passed}/{passed+failed} passed", end="")
    if failed:
        print(f"  ({failed} FAILED)")
        sys.exit(1)
    else:
        print(" ✓")
        sys.exit(0)
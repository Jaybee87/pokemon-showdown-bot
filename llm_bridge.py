"""
llm_bridge.py
=============
All LLM interaction in one place. Every module that needs Ollama goes through here.

Key design decisions:
- call_llm() uses threading for timeout (works from any thread, not just main)
- call_llm_async() wraps the blocking call in a thread pool for async contexts
- Response parsing is centralised — DECISION, LEAD, and move pick formats
- If the LLM fails or times out, callers get (None, error) and use Python fallback

Switching LLM backend:
  Replace _raw_llm_call(). Everything above it is backend-agnostic.
"""

import re
import asyncio
import subprocess
import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from config import LLM_MODEL, LLM_TIMEOUT_SECONDS, LLM_CONTEXT_LENGTH

# Thread pool for running blocking LLM calls
_executor = ThreadPoolExecutor(max_workers=2)


# =============================================================================
# LOW-LEVEL LLM CALL (replace this function to swap backends)
# =============================================================================

def _raw_llm_call(prompt, model=None):
    """
    Blocking call to Ollama. Returns the raw response string.
    This is the ONLY function that touches the ollama library.
    """
    import ollama
    response = ollama.chat(
        model=model or LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a Gen 1 Pokemon battle AI. "
                    "Do NOT use <think> tags or chain-of-thought reasoning. "
                    "Respond IMMEDIATELY with your decision. "
                    "No preamble, no explanation, no thinking out loud. "
                    "Just the DECISION line."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        options={
            "num_ctx": LLM_CONTEXT_LENGTH,
        },
    )
    return response['message']['content'].strip()


# =============================================================================
# OLLAMA LIFECYCLE
# =============================================================================

def ensure_ollama_running():
    """Check if Ollama is running, start it if not. Returns True on success."""
    try:
        import ollama
        ollama.list()
        print(f"  Ollama is running (model: {LLM_MODEL})")
        return True
    except Exception:
        print("  Starting Ollama...")
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(3)
            import ollama
            ollama.list()
            print(f"  Ollama started (model: {LLM_MODEL})")
            return True
        except Exception as e:
            print(f"  Failed to start Ollama: {e}")
            return False


# =============================================================================
# SYNCHRONOUS CALL WITH TIMEOUT (thread-safe — works from any thread)
# =============================================================================

def call_llm(prompt, timeout=None):
    """
    Blocking LLM call with timeout. Safe to call from any thread.

    Uses a thread pool + futures timeout instead of signal.SIGALRM,
    which only works on the main thread and crashes when called from
    poke-env's async event loop.

    Returns:
        (raw_response, None) on success
        (None, error_message) on failure/timeout
    """
    timeout = timeout or LLM_TIMEOUT_SECONDS
    try:
        future = _executor.submit(_raw_llm_call, prompt, None)
        raw = future.result(timeout=timeout)
        return raw, None
    except FuturesTimeoutError:
        return None, f"LLM call exceeded {timeout}s timeout"
    except Exception as e:
        return None, f"LLM error: {e}"


# =============================================================================
# ASYNC CALL (for explicit async contexts)
# =============================================================================

async def call_llm_async(prompt, timeout=None):
    """
    Non-blocking LLM call for async contexts.
    Runs the blocking Ollama call in a thread pool.

    Returns:
        (raw_response, None) on success
        (None, error_message) on failure/timeout
    """
    timeout = timeout or LLM_TIMEOUT_SECONDS
    loop = asyncio.get_event_loop()
    try:
        raw = await asyncio.wait_for(
            loop.run_in_executor(_executor, _raw_llm_call, prompt, None),
            timeout=timeout
        )
        return raw, None
    except asyncio.TimeoutError:
        return None, f"LLM call exceeded {timeout}s timeout"
    except Exception as e:
        return None, f"LLM error: {e}"


# =============================================================================
# RESPONSE PARSING
# =============================================================================

def strip_think_tags(raw):
    """Remove <think>...</think> blocks from deepseek-r1 responses."""
    return re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()


def parse_battle_decision(raw, valid_move_ids, valid_switch_ids):
    """
    Parse a DECISION line from LLM battle response.

    Handles variants:
      DECISION: move thunderbolt
      DECISION: switch chansey
      DECISION: use thunderbolt
      DECISION: thunderbolt

    Returns:
        (action_type, action_id) or (None, None) if parsing fails
    """
    raw = strip_think_tags(raw)

    move_set = {m.lower() for m in valid_move_ids}
    switch_set = {s.lower() for s in valid_switch_ids}

    for line in raw.split('\n'):
        line = line.strip()
        if not line.upper().startswith('DECISION:'):
            continue

        # Try "DECISION: move/switch X" (normalize spaces in move names)
        m = re.search(r'DECISION:\s*(move|switch)\s+<?>?([\w\s]+?)<?>?\s*$', line, re.IGNORECASE)
        if m:
            at = m.group(1).lower()
            ai = re.sub(r'[^a-z0-9]', '', m.group(2).lower())  # "body slam" → "bodyslam"
            if at == 'move' and ai in move_set:
                return 'move', ai
            if at == 'switch' and ai in switch_set:
                return 'switch', ai

        # Try "DECISION: use X" or "DECISION: X"
        m = re.search(r'DECISION:\s+(?:use\s+)?<?([\w]+)>?', line, re.IGNORECASE)
        if m:
            candidate = m.group(1).lower()
            if candidate in move_set:
                return 'move', candidate
            if candidate in switch_set:
                return 'switch', candidate

    # ── Fallback parsers for non-standard LLM output formats ────────────
    norm = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
    norm_move_set = {norm(m): m for m in move_set}
    norm_switch_set = {norm(s): s for s in switch_set}

    # Try \boxed{...} or \boxed{\text{...}}
    boxed = re.search(r'\\boxed\{(?:\\text\{)?([^}]+)', raw)
    if boxed:
        candidate = norm(boxed.group(1))
        if candidate in norm_move_set:
            return 'move', norm_move_set[candidate]
        if candidate in norm_switch_set:
            return 'switch', norm_switch_set[candidate]

    # Try "Answer:" or "Final Answer:" patterns — move/switch may be on same
    # line or next line, possibly in **bold** markdown
    # Strip markdown bold first for easier matching
    clean_raw = re.sub(r'\*\*', '', raw)
    answer = re.search(
        r'(?:Final\s+)?Answer:\s*(?:Use\s+|Switch\s+(?:to\s+)?)?(\w+)',
        clean_raw, re.IGNORECASE
    )
    if answer:
        candidate = norm(answer.group(1))
        if candidate in norm_move_set:
            return 'move', norm_move_set[candidate]
        if candidate in norm_switch_set:
            return 'switch', norm_switch_set[candidate]

    # Try "switch in X" / "switch to X" / "send in X" anywhere in text
    sw_match = re.search(r'(?:switch|send)\s+(?:in|to)\s+(\w+)', raw, re.IGNORECASE)
    if sw_match:
        candidate = norm(sw_match.group(1))
        if candidate in norm_switch_set:
            return 'switch', norm_switch_set[candidate]

    # Try "use X" anywhere in text (last resort)
    use_match = re.search(r'\buse\s+(\w+)', raw, re.IGNORECASE)
    if use_match:
        candidate = norm(use_match.group(1))
        if candidate in norm_move_set:
            return 'move', norm_move_set[candidate]

    return None, None


def parse_lead_choice(raw, valid_species):
    """Parse a LEAD line from LLM team preview response."""
    raw = strip_think_tags(raw)
    valid_set = {re.sub(r'[^a-z0-9]', '', s.lower()) for s in valid_species}

    m = re.search(r'LEAD:\s*<?(\w+)>?', raw, re.IGNORECASE)
    if m:
        chosen = re.sub(r'[^a-z0-9]', '', m.group(1).lower())
        if chosen in valid_set:
            return chosen
    return None


def parse_move_picks(raw, legal_moves, expected_count=4):
    """Parse move selection from LLM team builder response."""
    raw = strip_think_tags(raw)
    raw = re.sub(r'\*\*|\*|#{1,6}', '', raw)
    raw = re.sub(r'^[-\d]+[\.\)]\s*', '', raw, flags=re.MULTILINE)

    legal_set = set(legal_moves)
    picked = []
    for line in raw.strip().split('\n'):
        move = line.strip().lower().replace(' ', '').replace('-', '')
        if move in legal_set and move not in picked:
            picked.append(move)

    if len(picked) >= expected_count:
        return picked[:expected_count]
    return picked
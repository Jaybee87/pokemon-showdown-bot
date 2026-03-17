/// main.rs v3 — IPC bridge using zero-allocation internal state.
///
/// JSON deserialization uses JsonBattleState (strings), which converts
/// once to BattleState (integer IDs) at the boundary. Search runs entirely
/// on the compact integer representation.

mod data;
mod ids;
mod state;
mod calc;
mod eval;
mod sim;
mod minimax;
mod mcts;
mod inference;

use std::io::{self, BufRead, Write};
use serde::Deserialize;

use state::{BattleState, JsonBattleState, Decision, Action, ActionJson};
use ids::id_to_move;
use calc::{can_ko, guaranteed_ko, avg_damage_pct};

#[derive(Deserialize, Debug)]
struct Request {
    #[serde(default = "default_algo")]  algorithm:            String,
    #[serde(default = "default_depth")] depth:                u8,
    #[serde(default = "default_iters")] iterations:           u32,
    #[serde(default = "default_time")]  time_ms:              u64,
    #[serde(default = "default_true")]  infer_opponent_moves: bool,
    state: Option<JsonBattleState>,
    #[serde(default)]                   quit:                 bool,
}
fn default_algo()  -> String { "auto".into() }
fn default_depth() -> u8     { 6 }
fn default_iters() -> u32    { 3000 }
fn default_time()  -> u64    { 500 }
fn default_true()  -> bool   { true }

fn main() {
    let stdin  = io::stdin();
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());

    for line in stdin.lock().lines() {
        let line = match line { Ok(l) => l, Err(_) => break };
        let line = line.trim();
        if line.is_empty() { continue; }

        let req: Request = match serde_json::from_str(line) {
            Ok(r) => r,
            Err(e) => {
                let msg = e.to_string().replace('"', "'");
                writeln!(out, "{{\"error\":\"{}\"}}", msg).ok();
                out.flush().ok();
                continue;
            }
        };

        if req.quit { break; }

        let Request { algorithm, depth, iterations, time_ms, infer_opponent_moves, state, .. } = req;

        let json_state = match state {
            Some(s) => s,
            None => {
                writeln!(out, "{{\"error\":\"missing state\"}}").ok();
                out.flush().ok();
                continue;
            }
        };

        // Single conversion at the boundary — all search is on compact types
        let mut state = BattleState::from(json_state);

        if infer_opponent_moves {
            inference::infer_moves(&mut state.theirs.active, 4);
            for i in 0..state.theirs.bench_count as usize {
                inference::infer_moves(&mut state.theirs.bench[i], 4);
            }
        }

        let decision = run_search(&state, &algorithm, depth, iterations, time_ms);
        writeln!(out, "{}", serde_json::to_string(&decision).unwrap_or_default()).ok();
        out.flush().ok();
    }
}

fn run_search(
    state:      &BattleState,
    algorithm:  &str,
    depth:      u8,
    iterations: u32,
    time_ms:    u64,
) -> Decision {
    let alive_total = state.ours.alive_count() + state.theirs.alive_count();
    let algo = match algorithm {
        "minimax" => "minimax",
        "mcts"    => "mcts",
        _         => if alive_total <= 4 { "minimax" } else { "mcts" },
    };

    match algo {
        "minimax" => {
            let r = minimax::iterative_deepening(state, depth, time_ms);
            let reason = action_reason(&r.best_action, state);
            Decision {
                action:         ActionJson::from(r.best_action),
                score:          r.score,
                nodes_searched: r.nodes,
                algorithm:      "minimax".into(),
                reason,
            }
        }
        _ => {
            let r = mcts::mcts_search(state, iterations, time_ms);
            let reason = action_reason(&r.best_action, state);
            Decision {
                action:         ActionJson::from(r.best_action),
                score:          r.score,
                nodes_searched: r.simulations,
                algorithm:      format!("mcts({}alive)", alive_total),
                reason,
            }
        }
    }
}

fn action_reason(action: &Action, state: &BattleState) -> String {
    match action {
        Action::Recharge => "forced recharge turn".into(),
        Action::Move { id } => {
            let mid    = id_to_move(*id);
            let ours   = &state.ours.active;
            let theirs = &state.theirs.active;
            if guaranteed_ko(ours, mid, theirs) {
                format!("guaranteed KO with {mid}")
            } else if can_ko(ours, mid, theirs) {
                format!("likely KO with {mid}")
            } else {
                let dmg = avg_damage_pct(ours, mid, theirs, false, false);
                format!("{mid} → ~{:.0}% avg dmg", dmg * 100.0)
            }
        }
        Action::Switch { species } => {
            let name = ids::id_to_species(*species);
            format!("switch to {name} for better matchup")
        }
    }
}
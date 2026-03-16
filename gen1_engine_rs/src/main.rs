/// main.rs v2 — JSON stdin/stdout IPC bridge with opponent inference.

mod data;
mod state;
mod calc;
mod eval;
mod sim;
mod minimax;
mod mcts;
mod inference;

use std::io::{self, BufRead, Write};
use serde::Deserialize;

use state::{BattleState, Decision, Action};

#[derive(Deserialize, Debug)]
struct Request {
    #[serde(default = "default_algo")]  algorithm:            String,
    #[serde(default = "default_depth")] depth:                u8,
    #[serde(default = "default_iters")] iterations:           u32,
    #[serde(default = "default_time")]  time_ms:              u64,
    #[serde(default = "default_true")]  infer_opponent_moves: bool,
    state: Option<BattleState>,
    #[serde(default)]                   quit:                 bool,
}
fn default_algo()  -> String { "auto".into() }
fn default_depth() -> u8     { 4 }
fn default_iters() -> u32    { 800 }
fn default_time()  -> u64    { 200 }
fn default_true()  -> bool   { true }

fn main() {
    let stdin  = io::stdin();
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());

    for line in stdin.lock().lines() {
        let line = match line { Ok(l) => l, Err(_) => break };
        let line = line.trim().to_string();
        if line.is_empty() { continue; }

        let req: Request = match serde_json::from_str(&line) {
            Ok(r) => r,
            Err(e) => {
                writeln!(out, "{{\"error\":\"{}\"}}", e).ok();
                out.flush().ok();
                continue;
            }
        };

        if req.quit { break; }

        // Destructure req so `state` (Option<BattleState>) is owned separately
        // from the search parameters, avoiding a partial-move borrow conflict.
        let Request { algorithm, depth, iterations, time_ms, infer_opponent_moves, state, .. } = req;

        let mut state = match state {
            Some(s) => s,
            None => {
                writeln!(out, "{{\"error\":\"missing state\"}}").ok();
                out.flush().ok();
                continue;
            }
        };

        if infer_opponent_moves {
            inference::infer_moves(&mut state.theirs.active, 4);
            for p in state.theirs.bench.iter_mut() {
                inference::infer_moves(p, 4);
            }
        }
        if state.ours.active.moves.is_empty() {
            inference::infer_moves(&mut state.ours.active, 4);
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
            Decision {
                reason:         action_reason(&r.best_action, state),
                action:         r.best_action,
                score:          r.score,
                nodes_searched: r.nodes,
                algorithm:      "minimax".into(),
            }
        }
        _ => {
            let r = mcts::mcts_search(state, iterations, time_ms);
            Decision {
                reason:         action_reason(&r.best_action, state),
                action:         r.best_action,
                score:          r.score,
                nodes_searched: r.simulations,
                algorithm:      format!("mcts({}alive)", alive_total),
            }
        }
    }
}

fn action_reason(action: &Action, state: &BattleState) -> String {
    use crate::calc::{can_ko, guaranteed_ko, avg_damage_pct};
    match action {
        Action::Recharge => "forced recharge turn".into(),
        Action::Move { id } => {
            let ours   = &state.ours.active;
            let theirs = &state.theirs.active;
            if guaranteed_ko(ours, id, theirs) {
                format!("guaranteed KO with {id}")
            } else if can_ko(ours, id, theirs) {
                format!("likely KO with {id}")
            } else {
                let dmg = avg_damage_pct(ours, id, theirs, false, false);
                format!("{id} → ~{:.0}% avg dmg", dmg * 100.0)
            }
        }
        Action::Switch { species } => format!("switch to {species} for better matchup"),
    }
}
/// minimax.rs v2 — Alpha-beta minimax with improved move ordering.
///
/// Changes from v1:
///   - Handles Action::Recharge in legal action lists
///   - Move ordering: KO moves first, then high-damage, then switches
///   - Late-game detection: increase depth when few mons remain

use crate::state::*;
use crate::eval::*;
use crate::sim::apply_turn;
use crate::calc::{can_ko, guaranteed_ko, sim_expected_damage_pct};

pub struct MinimaxResult {
    pub best_action: Action,
    pub score: f64,
    pub nodes: u64,
}

pub fn minimax_search(state: &BattleState, max_depth: u8) -> MinimaxResult {
    let mut our_actions = legal_actions(&state.ours);
    order_our_moves(&mut our_actions, &state.ours, &state.theirs);

    // Prune switch actions with clearly negative scores — same logic as MCTS.
    // Only at the root and only when the active mon is healthy enough that
    // an emergency switch isn't warranted.
    let active_hp = state.ours.active.hp_frac();
    let has_moves = our_actions.iter().any(|a| matches!(a, Action::Move { .. }));
    if has_moves && active_hp > 0.25 {
        let best_move_score = our_actions.iter()
            .filter(|a| matches!(a, Action::Move { .. }))
            .map(|a| score_our_action(a, &state.ours, &state.theirs))
            .fold(f64::NEG_INFINITY, f64::max);
        let pruned: Vec<Action> = our_actions.iter().filter(|a| match a {
            Action::Switch { .. } => {
                let s = score_our_action(a, &state.ours, &state.theirs);
                s > -10.0 || s >= best_move_score * 0.5
            }
            _ => true,
        }).copied().collect();
        if !pruned.is_empty() { our_actions = pruned; }
    }

    let mut nodes       = 0u64;
    let mut best_action = our_actions[0].clone();
    let mut best_score  = f64::NEG_INFINITY;

    for action in &our_actions {
        let score = alpha_beta_our(
            state, action, max_depth,
            f64::NEG_INFINITY, f64::INFINITY,
            &mut nodes,
        );
        if score > best_score {
            best_score  = score;
            best_action = action.clone();
        }
    }
    MinimaxResult { best_action, score: best_score, nodes }
}

/// Returns score for US after we play `our_action` and the opponent responds optimally.
fn alpha_beta_our(
    state:      &BattleState,
    our_action: &Action,
    depth:      u8,
    alpha:      f64,
    beta:       f64,
    nodes:      &mut u64,
) -> f64 {
    *nodes += 1;

    let mut opp_actions = legal_actions(&state.theirs);
    order_opp_moves(&mut opp_actions, &state.theirs, &state.ours);

    if depth == 0 || is_terminal(state) {
        return worst_case(state, our_action, &opp_actions);
    }

    let alpha = alpha;
    let mut worst = f64::INFINITY;

    for opp_action in &opp_actions {
        let child = apply_turn(state, our_action, opp_action);
        let v = alpha_beta_max(&child, depth - 1, alpha, beta, nodes);
        worst = worst.min(v);
        if worst <= alpha { break; } // α-cutoff (opponent found a killer)
        // Tighten the beta window: we've found a new upper bound
        #[allow(unused_variables)]
        let beta = beta.min(worst);
    }
    worst
}

/// Returns the best score WE can achieve from `state` (our turn to pick).
fn alpha_beta_max(
    state: &BattleState,
    depth: u8,
    alpha: f64,
    beta:  f64,
    nodes: &mut u64,
) -> f64 {
    *nodes += 1;
    if depth == 0 || is_terminal(state) { return evaluate(state); }

    let mut our_actions = legal_actions(&state.ours);
    order_our_moves(&mut our_actions, &state.ours, &state.theirs);

    let mut alpha = alpha;
    let mut best  = f64::NEG_INFINITY;

    for action in &our_actions {
        let v = alpha_beta_our(state, action, depth - 1, alpha, beta, nodes);
        if v > best { best = v; }
        if best >= beta { break; } // β-cutoff
        if best > alpha { alpha = best; }
    }
    best
}

fn worst_case(state: &BattleState, our_action: &Action, opp_actions: &[Action]) -> f64 {
    opp_actions.iter().map(|opp| {
        let child = apply_turn(state, our_action, opp);
        evaluate(&child)
    }).fold(f64::INFINITY, f64::min)
}

// ─── Move ordering ────────────────────────────────────────────────────────────

fn order_our_moves(actions: &mut Vec<Action>, ours: &Side, theirs: &Side) {
    actions.sort_by(|a, b| {
        score_our_action(b, ours, theirs)
            .partial_cmp(&score_our_action(a, ours, theirs))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
}

fn order_opp_moves(actions: &mut Vec<Action>, theirs: &Side, ours: &Side) {
    actions.sort_by(|a, b| {
        score_our_action(b, theirs, ours)
            .partial_cmp(&score_our_action(a, theirs, ours))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
}

fn score_our_action(action: &Action, attacker: &Side, defender: &Side) -> f64 {
    match action {
        Action::Recharge => -1000.0,
        Action::Move { id } => {
            if guaranteed_ko(&attacker.active, *id, &defender.active) { return 10000.0; }
            if can_ko(&attacker.active, *id, &defender.active)         { return  5000.0; }
            sim_expected_damage_pct(&attacker.active, *id, &defender.active, false, false) * 100.0
        }
        Action::Switch { species } => {
            // Look up incoming mon in ATTACKER's bench (was incorrectly using defender).
            let count = attacker.bench_count as usize;
            let incoming = attacker.bench[..count].iter()
                .find(|p| p.species == *species);
            let Some(inc) = incoming else { return -100.0 };

            if inc.status == crate::state::Status::Slp
            || inc.status == crate::state::Status::Frz {
                return -200.0;
            }

            let explosion_id    = crate::ids::move_to_id("explosion");
            let selfdestruct_id = crate::ids::move_to_id("selfdestruct");

            let opp_best_in_vs_incoming = if defender.active.status == crate::state::Status::Slp
                || defender.active.status == crate::state::Status::Frz {
                0.0
            } else {
                defender.active.move_ids().iter()
                    .filter(|&&mid| mid != explosion_id && mid != selfdestruct_id)
                    .map(|&mid| sim_expected_damage_pct(&defender.active, mid, inc, false, false))
                    .fold(0.0f64, f64::max)
            };

            let inc_best_out = inc.move_ids().iter()
                .map(|&mid| sim_expected_damage_pct(inc, mid, &defender.active, false, false))
                .fold(0.0f64, f64::max);

            let cur_best_out = attacker.active.move_ids().iter()
                .map(|&mid| sim_expected_damage_pct(&attacker.active, mid, &defender.active, false, false))
                .fold(0.0f64, f64::max);

            let cur_best_in = if defender.active.status == crate::state::Status::Slp
                || defender.active.status == crate::state::Status::Frz {
                0.0
            } else {
                defender.active.move_ids().iter()
                    .filter(|&&mid| mid != explosion_id && mid != selfdestruct_id)
                    .map(|&mid| sim_expected_damage_pct(&defender.active, mid, &attacker.active, false, false))
                    .fold(0.0f64, f64::max)
            };

            let incoming_net = (inc_best_out - opp_best_in_vs_incoming) * (inc.hp_frac() as f64);
            let current_net  =  cur_best_out - cur_best_in;
            let improvement  = incoming_net - current_net;
            let free_hit_cost = opp_best_in_vs_incoming;

            (improvement - free_hit_cost) * 100.0
        }
    }
}

// ─── Iterative deepening ──────────────────────────────────────────────────────

pub fn iterative_deepening(
    state:       &BattleState,
    max_depth:   u8,
    time_limit_ms: u64,
) -> MinimaxResult {
    use std::time::Instant;
    let start = Instant::now();

    // In late-game, go deeper automatically
    let alive = (state.ours.alive_count() + state.theirs.alive_count()) as u8;
    let effective_max = if alive <= 4 {
        max_depth + 3   // was +2 at <=2; now +3 at <=4 — catches endgame stalls
    } else {
        max_depth
    };

    let mut best = minimax_search(state, 1);
    for depth in 2..=effective_max {
        if start.elapsed().as_millis() as u64 >= time_limit_ms { break; }
        best = minimax_search(state, depth);
    }
    best
}
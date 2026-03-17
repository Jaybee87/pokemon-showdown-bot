/// mcts.rs v3 — Parallel root MCTS using rayon.
///
/// Changes from v2:
///   - Parallel root parallelisation: spawn N independent trees on rayon
///     threads, merge visit counts at the root action level.
///   - Each thread gets its own RNG so there is no contention.
///   - Rollout depth increased 14→20 to catch multi-turn heal/stall cycles.
///   - Time budget per thread = wall_time_ms (they all start simultaneously).
///   - Iterations per thread = total_iterations / num_threads (ceiling).

use std::time::Instant;
use rand::Rng;
use rayon::prelude::*;

use crate::state::*;
use crate::eval::*;
use crate::sim::apply_turn;
use crate::calc::{avg_damage_pct, can_ko, guaranteed_ko};

const EXPLORATION_C:     f64 = 1.414;
const MAX_ROLLOUT_DEPTH: u8  = 20;   // up from 14 — sees heal/recharge cycles
const NUM_THREADS:       u32 = 6;    // leave 2 cores for OS + Python

struct Node {
    action:   Option<Action>,
    parent:   Option<usize>,
    children: Vec<usize>,
    wins:     f64,
    visits:   u32,
    state:    BattleState,
    untried:  Vec<Action>,
}

impl Node {
    fn new(state: BattleState, action: Option<Action>, parent: Option<usize>) -> Self {
        let mut untried = legal_actions(&state.ours);
        order_actions(&mut untried, &state.ours, &state.theirs);
        Node { action, parent, children: Vec::new(), wins: 0.0, visits: 0, state, untried }
    }
    fn ucb1(&self, parent_v: u32) -> f64 {
        if self.visits == 0 { return f64::INFINITY; }
        self.wins / self.visits as f64
            + EXPLORATION_C * ((parent_v as f64).ln() / self.visits as f64).sqrt()
    }
    fn fully_expanded(&self) -> bool { self.untried.is_empty() }
}

struct Tree { nodes: Vec<Node> }

impl Tree {
    fn new(root: BattleState) -> Self {
        Tree { nodes: vec![Node::new(root, None, None)] }
    }

    fn select(&self) -> usize {
        let mut n = 0usize;
        loop {
            if is_terminal(&self.nodes[n].state) || !self.nodes[n].fully_expanded() {
                return n;
            }
            let pv = self.nodes[n].visits;
            n = *self.nodes[n].children.iter()
                .max_by(|&&a, &&b| {
                    self.nodes[a].ucb1(pv)
                        .partial_cmp(&self.nodes[b].ucb1(pv))
                        .unwrap_or(std::cmp::Ordering::Equal)
                })
                .unwrap();
        }
    }

    fn expand(&mut self, idx: usize) -> usize {
        if is_terminal(&self.nodes[idx].state) { return idx; }
        let action     = self.nodes[idx].untried.pop().unwrap();
        let opp_action = best_opponent_action(&self.nodes[idx].state);
        let child_state = apply_turn(&self.nodes[idx].state, &action, &opp_action);
        let child_idx   = self.nodes.len();
        self.nodes.push(Node::new(child_state, Some(action), Some(idx)));
        self.nodes[idx].children.push(child_idx);
        child_idx
    }

    fn simulate(&self, idx: usize) -> f64 {
        let mut state = self.nodes[idx].state.clone();
        let mut rng   = rand::thread_rng();
        for _ in 0..MAX_ROLLOUT_DEPTH {
            if is_terminal(&state) { break; }
            let mut oa = legal_actions(&state.ours);
            let mut ta = legal_actions(&state.theirs);
            order_actions(&mut oa, &state.ours, &state.theirs);
            order_actions(&mut ta, &state.theirs, &state.ours);
            let our_a = if rng.gen::<f64>() < 0.80 { oa[0].clone() }
                        else { oa[rng.gen_range(0..oa.len())].clone() };
            let opp_a = if rng.gen::<f64>() < 0.80 { ta[0].clone() }
                        else { ta[rng.gen_range(0..ta.len())].clone() };
            state = apply_turn(&state, &our_a, &opp_a);
        }
        evaluate(&state) / 10_000.0
    }

    fn backprop(&mut self, mut idx: usize, result: f64) {
        loop {
            self.nodes[idx].visits += 1;
            self.nodes[idx].wins   += result;
            match self.nodes[idx].parent { Some(p) => idx = p, None => break }
        }
    }

    /// Run iterations until time_ms elapsed or iteration cap reached.
    /// Returns (action_label → (total_wins, total_visits)) for root children.
    fn run(&mut self, iterations: u32, time_ms: u64) -> u64 {
        let start = Instant::now();
        let mut sims = 0u64;
        for _ in 0..iterations {
            if start.elapsed().as_millis() as u64 >= time_ms { break; }
            let sel    = self.select();
            let exp    = self.expand(sel);
            let result = self.simulate(exp);
            self.backprop(exp, result);
            sims += 1;
        }
        sims
    }

    /// Extract (action, wins, visits) for each root child.
    fn root_stats(&self) -> Vec<(Action, f64, u32)> {
        self.nodes[0].children.iter().filter_map(|&ci| {
            let n = &self.nodes[ci];
            n.action.as_ref().map(|a| (a.clone(), n.wins, n.visits))
        }).collect()
    }
}

pub struct MctsResult {
    pub best_action: Action,
    pub score: f64,
    pub simulations: u64,
}

pub fn mcts_search(state: &BattleState, iterations: u32, time_ms: u64) -> MctsResult {
    let iters_per_thread = (iterations + NUM_THREADS - 1) / NUM_THREADS;

    // Run NUM_THREADS independent trees in parallel.
    // Each tree is fully independent (no shared state), so no locking needed.
    let per_thread: Vec<(Vec<(Action, f64, u32)>, u64)> = (0..NUM_THREADS)
        .into_par_iter()
        .map(|_| {
            let mut tree = Tree::new(state.clone());
            let sims     = tree.run(iters_per_thread, time_ms);
            (tree.root_stats(), sims)
        })
        .collect();

    // Merge: accumulate wins+visits per action identity across all trees.
    use std::collections::HashMap;
    let mut merged: HashMap<String, (Action, f64, u32)> = HashMap::new();
    let mut total_sims = 0u64;

    for (stats, sims) in per_thread {
        total_sims += sims;
        for (action, wins, visits) in stats {
            let key = action_key(&action);
            let entry = merged.entry(key).or_insert((action, 0.0, 0));
            entry.1 += wins;
            entry.2 += visits;
        }
    }

    // Pick action with most visits (standard MCTS selection).
    let best = merged.values()
        .max_by_key(|(_, _, v)| *v)
        .expect("no actions explored");

    let score = if best.2 > 0 { best.1 / best.2 as f64 } else { 0.0 };

    MctsResult {
        best_action: best.0.clone(),
        score: score * 10_000.0,
        simulations: total_sims,
    }
}

fn action_key(a: &Action) -> String {
    match a {
        Action::Move    { id }      => format!("move:{}", id),
        Action::Switch  { species } => format!("switch:{}", species),
        Action::Recharge            => "recharge".into(),
    }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn best_opponent_action(state: &BattleState) -> Action {
    let mut acts = legal_actions(&state.theirs);
    order_actions(&mut acts, &state.theirs, &state.ours);
    acts.into_iter().next().unwrap_or(Action::Move { id: "struggle".into() })
}

fn order_actions(actions: &mut Vec<Action>, attacker: &Side, defender: &Side) {
    actions.sort_by(|a, b| {
        action_score(b, attacker, defender)
            .partial_cmp(&action_score(a, attacker, defender))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
}

fn action_score(action: &Action, attacker: &Side, defender: &Side) -> f64 {
    match action {
        Action::Recharge => -500.0,
        Action::Move { id } => {
            if guaranteed_ko(&attacker.active, id, &defender.active) { return 10000.0; }
            if can_ko(&attacker.active, id, &defender.active)         { return  5000.0; }
            avg_damage_pct(&attacker.active, id, &defender.active, false, false) * 100.0
        }
        Action::Switch { species } => {
            attacker.bench.iter()
                .find(|p| &p.species == species)
                .map(|p| p.hp_frac as f64 * 25.0)
                .unwrap_or(10.0)
        }
    }
}
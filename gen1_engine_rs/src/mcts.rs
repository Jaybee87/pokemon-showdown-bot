/// mcts.rs v4 — Parallel root DUCT (Decoupled UCT).
///
/// Changes from v3 (sequential-move MCTS):
///
/// DUCT treats Pokémon correctly as a simultaneous-move game. Both players
/// select actions independently using their own UCB statistics, without
/// seeing the other's choice. This converges to a Nash-equilibrium mixed
/// strategy rather than a best-response to the assumed opponent policy.
///
/// Key structural changes:
///   - Node no longer stores a single (wins, visits) pair.
///     Instead it stores two independent stat tables:
///       our_stats:  Vec<ActionStat>  — one entry per our legal action
///       opp_stats:  Vec<ActionStat>  — one entry per opponent legal action
///   - Children are keyed by (our_action_idx, opp_action_idx) joint pairs.
///   - Selection: each player picks via UCB over their own table, independently.
///   - Expansion: apply the selected joint action, create child node.
///   - Backprop: update BOTH players' stat tables at every ancestor.
///   - Rollout: unchanged — 80/20 heuristic simulation to depth 20.
///   - Parallel root: unchanged — 6 independent trees, merge at root.
///
/// References:
///   Shafiei et al. (2009) "Combining TD-learning with Monte Carlo Tree Search
///   for Simultaneous Games" — DUCT algorithm.
///   Lanctot et al. (2013) "Monte Carlo Tree Search in Simultaneous Move Games"

use std::time::Instant;
use rand::Rng;
use rayon::prelude::*;

use crate::state::*;
use crate::eval::*;
use crate::sim::apply_turn;
use crate::calc::{avg_damage_pct, can_ko, guaranteed_ko};

// ─── Constants ────────────────────────────────────────────────────────────────

/// UCB exploration constant. sqrt(2) ≈ 1.414 is standard; DUCT often benefits
/// from a slightly higher value since simultaneous-move games have more
/// variance per node visit.
const EXPLORATION_C:     f64 = 1.5;

/// Maximum rollout depth — sees heal/recharge cycles and stall endgames.
const MAX_ROLLOUT_DEPTH: u8  = 20;

/// Number of parallel root trees. Leave 2 cores for OS + Python process.
const NUM_THREADS:       u32 = 6;

// ─── Per-action statistics ────────────────────────────────────────────────────

/// Statistics for one action in one player's table at a given node.
#[derive(Clone)]
struct ActionStat {
    action: Action,
    wins:   f64,
    visits: u32,
}

impl ActionStat {
    fn new(action: Action) -> Self {
        ActionStat { action, wins: 0.0, visits: 0 }
    }

    /// UCB1 score. Unvisited actions always get explored first (infinity).
    fn ucb(&self, total_visits: u32) -> f64 {
        if self.visits == 0 { return f64::INFINITY; }
        self.wins / self.visits as f64
            + EXPLORATION_C * ((total_visits as f64).ln() / self.visits as f64).sqrt()
    }
}

// ─── Node ─────────────────────────────────────────────────────────────────────

struct Node {
    state:     BattleState,
    parent:    Option<usize>,

    /// Our legal actions at this node, each with independent UCB stats.
    our_stats:  Vec<ActionStat>,
    /// Opponent's legal actions at this node, each with independent UCB stats.
    opp_stats:  Vec<ActionStat>,

    /// Total visits to this node (sum of any player's action visits).
    /// Used as the parent_visits denominator in UCB calculations.
    visits:    u32,

    /// Children indexed by encoded joint action: our_idx * MAX_OPP + opp_idx.
    /// Sparse — None means that joint pair hasn't been expanded yet.
    children:  Vec<Option<usize>>,
}

impl Node {
    fn new(state: BattleState, parent: Option<usize>) -> Self {
        let our_actions = {
            let mut v = legal_actions(&state.ours);
            order_actions(&mut v, &state.ours, &state.theirs);
            v.into_iter().map(ActionStat::new).collect::<Vec<_>>()
        };
        let opp_actions = {
            let mut v = legal_actions(&state.theirs);
            order_actions(&mut v, &state.theirs, &state.ours);
            v.into_iter().map(ActionStat::new).collect::<Vec<_>>()
        };

        // Children table: n_our_actions × n_opp_actions, all initially None.
        let n_children = our_actions.len() * opp_actions.len().max(1);
        let children = vec![None; n_children];

        Node {
            state,
            parent,
            our_stats:  our_actions,
            opp_stats:  opp_actions,
            visits:     0,
            children,
        }
    }

    fn n_opp(&self) -> usize { self.opp_stats.len().max(1) }

    /// Encode (our_idx, opp_idx) → flat children index.
    fn child_idx(&self, our_i: usize, opp_i: usize) -> usize {
        our_i * self.n_opp() + opp_i
    }

    /// Select our action index using UCB over our_stats.
    fn select_our(&self) -> usize {
        let total = self.visits.max(1);
        self.our_stats.iter().enumerate()
            .max_by(|(_, a), (_, b)| {
                a.ucb(total).partial_cmp(&b.ucb(total))
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .map(|(i, _)| i)
            .unwrap_or(0)
    }

    /// Select opponent action index using UCB over opp_stats.
    /// Opponent maximises their own score (minimises ours) — we invert the
    /// win value so UCB points toward the opponent's best responses.
    fn select_opp(&self) -> usize {
        if self.opp_stats.is_empty() { return 0; }
        let total = self.visits.max(1);
        self.opp_stats.iter().enumerate()
            .max_by(|(_, a), (_, b)| {
                // Opponent's "win" = 1 - our win (zero-sum)
                let ua = opp_ucb(a, total);
                let ub = opp_ucb(b, total);
                ua.partial_cmp(&ub).unwrap_or(std::cmp::Ordering::Equal)
            })
            .map(|(i, _)| i)
            .unwrap_or(0)
    }
}

/// UCB score from the opponent's perspective (maximise opponent wins = minimise ours).
fn opp_ucb(stat: &ActionStat, total: u32) -> f64 {
    if stat.visits == 0 { return f64::INFINITY; }
    // Opponent "win rate" = 1 - our_win_rate
    let opp_win_rate = 1.0 - (stat.wins / stat.visits as f64);
    opp_win_rate + EXPLORATION_C * ((total as f64).ln() / stat.visits as f64).sqrt()
}

// ─── Tree ─────────────────────────────────────────────────────────────────────

struct Tree {
    nodes: Vec<Node>,
}

impl Tree {
    fn new(root: BattleState) -> Self {
        Tree { nodes: vec![Node::new(root, None)] }
    }

    /// Select path to a node that has unvisited joint actions, or a terminal.
    /// Returns (node_idx, our_action_idx, opp_action_idx).
    fn select(&self) -> (usize, usize, usize) {
        let mut n = 0usize;
        loop {
            let node = &self.nodes[n];

            if is_terminal(&node.state) {
                return (n, 0, 0);
            }

            // Pick actions for both players via their independent UCB tables.
            let our_i = node.select_our();
            let opp_i = node.select_opp();
            let ci    = node.child_idx(our_i, opp_i);

            // If this joint action hasn't been expanded yet → expand here.
            if node.children.get(ci).copied().flatten().is_none() {
                return (n, our_i, opp_i);
            }

            // Otherwise descend to the existing child.
            n = node.children[ci].unwrap();
        }
    }

    /// Expand node `n` by applying joint action (our_i, opp_i).
    /// Creates a new child node; returns its index.
    fn expand(&mut self, n: usize, our_i: usize, opp_i: usize) -> usize {
        if is_terminal(&self.nodes[n].state) { return n; }

        // Extract everything we need from node[n] before any mutation.
        // Action and BattleState are both Copy so this is zero-cost.
        let our_action: Action = self.nodes[n].our_stats[our_i].action;
        let opp_action: Action = if self.nodes[n].opp_stats.is_empty() {
            Action::Move { id: crate::ids::MOVE_STRUGGLE }
        } else {
            let opp_clamped = opp_i.min(self.nodes[n].opp_stats.len() - 1);
            self.nodes[n].opp_stats[opp_clamped].action
        };
        let parent_state: BattleState = self.nodes[n].state;
        let n_opp: usize              = self.nodes[n].n_opp();
        let ci: usize                 = our_i * n_opp + opp_i;
        let children_len: usize       = self.nodes[n].children.len();

        // All borrows on self.nodes[n] are now released.
        let child_state = apply_turn(&parent_state, &our_action, &opp_action);
        let child_idx   = self.nodes.len();
        self.nodes.push(Node::new(child_state, Some(n)));

        // Register child in parent's table.
        if ci < children_len {
            self.nodes[n].children[ci] = Some(child_idx);
        }

        child_idx
    }

    /// Simulate from node `leaf` to depth MAX_ROLLOUT_DEPTH using heuristic play.
    fn simulate(&self, leaf: usize) -> f64 {
        let mut state = self.nodes[leaf].state;
        let mut rng   = rand::thread_rng();

        for _ in 0..MAX_ROLLOUT_DEPTH {
            if is_terminal(&state) { break; }

            let mut oa = legal_actions(&state.ours);
            let mut ta = legal_actions(&state.theirs);
            order_actions(&mut oa, &state.ours,   &state.theirs);
            order_actions(&mut ta, &state.theirs, &state.ours);

            // Both players select simultaneously and independently.
            // 80% best-move, 20% random — same as before.
            let our_a = if rng.gen::<f64>() < 0.80 { oa[0] }
                        else { oa[rng.gen_range(0..oa.len())] };
            let opp_a = if rng.gen::<f64>() < 0.80 { ta[0] }
                        else { ta[rng.gen_range(0..ta.len())] };

            state = apply_turn(&state, &our_a, &opp_a);
        }

        evaluate(&state) / 10_000.0
    }

    /// Backpropagate result up the tree.
    /// At each ancestor, update BOTH players' action stat tables.
    /// We track which joint action was taken at each step by re-deriving it
    /// from the child's parent link and the child index within the parent's
    /// children table.
    fn backprop(&mut self, mut idx: usize, result: f64) {
        loop {
            self.nodes[idx].visits += 1;

            let parent_opt = self.nodes[idx].parent;
            let Some(parent_idx) = parent_opt else { break };

            // Find which (our_i, opp_i) led to `idx` in the parent's children.
            let n_opp = self.nodes[parent_idx].n_opp();
            let (our_i, opp_i) = self.nodes[parent_idx].children.iter()
                .enumerate()
                .find(|(_, &c)| c == Some(idx))
                .map(|(ci, _)| (ci / n_opp, ci % n_opp))
                .unwrap_or((0, 0));

            // Update our action stat with result (our win rate).
            if our_i < self.nodes[parent_idx].our_stats.len() {
                self.nodes[parent_idx].our_stats[our_i].wins   += result;
                self.nodes[parent_idx].our_stats[our_i].visits += 1;
            }
            // Update opponent action stat with result (they use 1-result internally).
            if opp_i < self.nodes[parent_idx].opp_stats.len() {
                self.nodes[parent_idx].opp_stats[opp_i].wins   += result;
                self.nodes[parent_idx].opp_stats[opp_i].visits += 1;
            }

            idx = parent_idx;
        }
    }

    /// Run DUCT iterations until time budget or iteration cap.
    fn run(&mut self, iterations: u32, time_ms: u64) -> u64 {
        let start = Instant::now();
        let mut sims = 0u64;

        for _ in 0..iterations {
            if start.elapsed().as_millis() as u64 >= time_ms { break; }
            let (sel_node, our_i, opp_i) = self.select();
            let leaf   = self.expand(sel_node, our_i, opp_i);
            let result = self.simulate(leaf);
            self.backprop(leaf, result);
            sims += 1;
        }
        sims
    }

    /// Extract root-level action statistics for merging across parallel trees.
    /// Returns (action, total_wins, total_visits) per our root action.
    fn root_stats(&self) -> Vec<(Action, f64, u32)> {
        let root = &self.nodes[0];
        root.our_stats.iter().map(|s| (s.action, s.wins, s.visits)).collect()
    }
}

// ─── Public interface ─────────────────────────────────────────────────────────

pub struct MctsResult {
    pub best_action: Action,
    pub score:       f64,
    pub simulations: u64,
}

pub fn mcts_search(state: &BattleState, iterations: u32, time_ms: u64) -> MctsResult {
    let iters_per_thread = (iterations + NUM_THREADS - 1) / NUM_THREADS;

    // Run NUM_THREADS independent DUCT trees in parallel.
    let per_thread: Vec<(Vec<(Action, f64, u32)>, u64)> = (0..NUM_THREADS)
        .into_par_iter()
        .map(|_| {
            let mut tree = Tree::new(*state);
            let sims     = tree.run(iters_per_thread, time_ms);
            (tree.root_stats(), sims)
        })
        .collect();

    // Merge: sum wins+visits per action across all trees.
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

    // Best action = most visited (robust child selection — standard in MCTS).
    let best = merged.values()
        .max_by_key(|(_, _, v)| *v)
        .expect("DUCT: no root actions explored");

    let score = if best.2 > 0 { best.1 / best.2 as f64 } else { 0.0 };

    MctsResult {
        best_action: best.0,
        score:       score * 10_000.0,
        simulations: total_sims,
    }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn action_key(a: &Action) -> String {
    match a {
        Action::Move    { id }      => format!("move:{}", id),
        Action::Switch  { species } => format!("switch:{}", species),
        Action::Recharge            => "recharge".into(),
    }
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
            if guaranteed_ko(&attacker.active, *id, &defender.active) { return 10000.0; }
            if can_ko(&attacker.active, *id, &defender.active)         { return  5000.0; }
            avg_damage_pct(&attacker.active, *id, &defender.active, false, false) * 100.0
        }
        Action::Switch { species } => {
            let count = attacker.bench_count as usize;
            attacker.bench[..count].iter()
                .find(|p| p.species == *species)
                .map(|p| p.hp_frac() as f64 * 25.0)
                .unwrap_or(10.0)
        }
    }
}

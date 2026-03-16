/// state.rs — Serialisable battle state that Python passes to the engine.
///
/// Python sends a JSON blob (BattleState) every turn; the engine runs
/// Minimax/MCTS and returns a Decision.  No poke-env objects cross the
/// boundary — only plain data.
///
/// Changelog (v2):
///   - BattlePoke: added recharging, toxic_counter, trapping_turns,
///     confused, crit_stage, disabled_move fields
///   - Side: added screen_turns (reflect/light_screen turn counters)
///   - legal_actions: respects recharge lock, disabled moves
///   - BattleState: added weather (unused in Gen 1 but forward-compat slot)

use serde::{Deserialize, Serialize};

// ─── Status ───────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "UPPERCASE")]
pub enum Status {
    #[default]
    None,
    Slp,
    Par,
    Psn,
    Tox,
    Brn,
    Frz,
}

impl Status {
    pub fn is_immobilising(self) -> bool {
        matches!(self, Status::Slp | Status::Frz)
    }
}

// ─── Individual Pokémon in battle ─────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BattlePoke {
    /// Canonical species name (e.g. "tauros")
    pub species: String,
    /// Current HP as 0.0–1.0 fraction
    pub hp_frac: f32,
    /// Known / inferred moves available (move IDs, e.g. "bodyslam")
    pub moves: Vec<String>,
    pub status: Status,
    /// Stat-stage boosts  (keys: "atk","def","spc","spe","acc","eva")
    #[serde(default)]
    pub boosts: std::collections::HashMap<String, i8>,
    /// Is this slot fainted?
    #[serde(default)]
    pub fainted: bool,

    // ── Volatile state (resets on switch) ────────────────────────────────

    /// Substitute HP fraction (0.0 = no sub active)
    #[serde(default)]
    pub sub_hp_frac: f32,

    /// Sleep counter: turns of sleep remaining (Gen 1: 1-7 turns, random).
    /// We store the *expected* remaining turns (avg = 3.5 on start, counts down).
    #[serde(default)]
    pub sleep_turns: u8,

    /// True when the Pokémon used Hyper Beam last turn and must recharge.
    /// While true the only legal action is the implicit "recharge" turn.
    #[serde(default)]
    pub recharging: bool,

    /// Toxic counter: number of turns since Toxic was applied (0 = not toxic).
    /// Damage per turn = (toxic_counter / 16) × max_hp.
    /// Resets to 0 on switch-out in Gen 1.
    #[serde(default)]
    pub toxic_counter: u8,

    /// Trapping turns remaining (Wrap / Bind / Clamp / Fire Spin).
    /// 0 = not trapped.  Gen 1: 2–5 turns, expected ~3.5.
    #[serde(default)]
    pub trapping_turns: u8,

    /// True if the Pokémon is confused this turn.
    #[serde(default)]
    pub confused: bool,

    /// Confusion turns remaining (for simulation; 0 if not confused).
    #[serde(default)]
    pub confusion_turns: u8,

    /// Focus Energy / crit-rate stage (Gen 1 bug: raises crit stage but
    /// lowers effective crit rate — we track it to penalise the move).
    #[serde(default)]
    pub crit_stage: u8,

    /// Move ID currently disabled by Disable (empty string = none).
    #[serde(default)]
    pub disabled_move: String,

    /// Turns remaining on Disable (0 = not disabled).
    #[serde(default)]
    pub disable_turns: u8,
}

impl BattlePoke {
    pub fn boost(&self, key: &str) -> i8 {
        self.boosts.get(key).copied().unwrap_or(0)
    }
    pub fn has_sub(&self) -> bool { self.sub_hp_frac > 0.0 }
    pub fn is_trapped(&self) -> bool { self.trapping_turns > 0 }
}

// ─── Side of the field ────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Side {
    pub active: BattlePoke,
    /// Benched Pokémon (not fainted, not active)
    pub bench: Vec<BattlePoke>,

    /// Reflect active on this side.
    #[serde(default)]
    pub reflect: bool,
    /// Turns remaining on Reflect (0 when inactive).
    #[serde(default)]
    pub reflect_turns: u8,

    /// Light Screen active on this side.
    #[serde(default)]
    pub light_screen: bool,
    /// Turns remaining on Light Screen (0 when inactive).
    #[serde(default)]
    pub light_screen_turns: u8,
}

impl Side {
    pub fn all_pokes(&self) -> impl Iterator<Item = &BattlePoke> {
        std::iter::once(&self.active).chain(self.bench.iter())
    }
    pub fn alive_count(&self) -> usize {
        self.all_pokes().filter(|p| !p.fainted).count()
    }
    /// Returns available switch targets (alive bench members).
    /// Blocked while the active Pokémon is trapped.
    pub fn switches(&self) -> Vec<&BattlePoke> {
        if self.active.is_trapped() {
            return Vec::new();
        }
        self.bench.iter().filter(|p| !p.fainted).collect()
    }
}

// ─── Top-level battle state ───────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BattleState {
    pub turn: u32,
    pub ours: Side,
    pub theirs: Side,
}

// ─── Actions ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum Action {
    Move    { id: String },
    Switch  { species: String },
    Recharge,   // forced recharge turn after Hyper Beam
}

impl Action {
    pub fn move_id(&self) -> Option<&str> {
        if let Action::Move { id } = self { Some(id.as_str()) } else { None }
    }
}

// ─── Legal-action generator ───────────────────────────────────────────────────

pub fn legal_actions(side: &Side) -> Vec<Action> {
    // Recharge lock: only option is to waste the turn
    if side.active.recharging {
        return vec![Action::Recharge];
    }

    // Immobilised by sleep or freeze: only option is to try to move (fail)
    // We model this as a single "no-op move" so the tree still branches correctly.
    if side.active.status.is_immobilising() {
        return vec![Action::Move { id: "__sleep_frz__".to_string() }];
    }

    let mut out = Vec::new();

    // Available moves (excluding disabled)
    for m in &side.active.moves {
        if !m.is_empty()
            && m != "struggle"
            && m != "recharge"
            && *m != side.active.disabled_move
        {
            out.push(Action::Move { id: m.clone() });
        }
    }

    // If all moves are disabled/gone → Struggle
    if out.is_empty() {
        out.push(Action::Move { id: "struggle".to_string() });
    }

    // Switches (blocked by trapping moves)
    for p in side.switches() {
        out.push(Action::Switch { species: p.species.clone() });
    }

    out
}

// ─── Decision (returned to Python) ───────────────────────────────────────────

#[derive(Debug, Serialize, Deserialize)]
pub struct Decision {
    pub action: Action,
    pub score: f64,
    pub nodes_searched: u64,
    pub algorithm: String,
    /// Human-readable reason
    pub reason: String,
}
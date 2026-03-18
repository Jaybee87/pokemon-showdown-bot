/// state.rs v3 — Zero-allocation battle state.
///
/// All String fields replaced with u16 integer IDs (see ids.rs).
/// Moves stored as [u16; 4] fixed array — no Vec, no heap.
/// Boosts stored as [i8; 6] fixed array — no HashMap, no heap.
/// BattleState::clone() is now a plain stack memcpy.
///
/// JSON deserialization still accepts the string format from Python.
/// Conversion happens once at the boundary in main.rs (from_json_state).

use serde::{Deserialize, Serialize};
use crate::ids::*;

// ─── Status ───────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "UPPERCASE")]
pub enum Status {
    #[default] None,
    Slp, Par, Psn, Tox, Brn, Frz,
}

impl Status {
    pub fn is_immobilising(self) -> bool {
        matches!(self, Status::Slp | Status::Frz)
    }
}

// ─── Compact Pokémon struct — zero heap allocation ────────────────────────────
// v5: hp stored as u16 integer (actual HP, not fraction).
//     Computed stats (atk/def/spc/spe/max_hp/types) cached directly in struct.
//     Zero runtime table lookups during search — all data is inline.

#[derive(Debug, Clone, Copy)]
pub struct BattlePoke {
    pub species:         u16,
    // HP stored as integer — eliminates f32<->f64 conversions and max_hp lookups
    pub hp:              u16,
    pub max_hp:          u16,
    // Pre-computed battle stats at L100/DV15/maxStatExp — set at construction,
    // never recalculated. apply_stage() modifies effective stats transiently.
    pub base_atk:        u16,
    pub base_def:        u16,
    pub base_spc:        u16,
    pub base_spe:        u16,
    pub type1:           crate::data::Type,
    pub type2:           Option<crate::data::Type>,
    pub moves:           [u16; 4],
    pub move_count:      u8,
    pub status:          Status,
    pub boosts:          [i8; 6],
    pub fainted:         bool,
    pub sub_hp:          u16,      // substitute HP in actual HP units
    pub sleep_turns:     u8,
    pub recharging:      bool,
    pub toxic_counter:   u8,
    pub trapping_turns:  u8,
    pub confused:        bool,
    pub confusion_turns: u8,
    pub crit_stage:      u8,
    pub disabled_move:   u16,
    pub disable_turns:   u8,
}

impl Default for BattlePoke {
    fn default() -> Self {
        Self {
            species:        SPECIES_UNKNOWN,
            hp:             100,
            max_hp:         100,
            base_atk:       100,
            base_def:       100,
            base_spc:       100,
            base_spe:       100,
            type1:          crate::data::Type::Normal,
            type2:          None,
            moves:          [MOVE_NONE; 4],
            move_count:     0,
            status:         Status::None,
            boosts:         [0i8; 6],
            fainted:        false,
            sub_hp:         0,
            sleep_turns:    0,
            recharging:     false,
            toxic_counter:  0,
            trapping_turns: 0,
            confused:       false,
            confusion_turns:0,
            crit_stage:     0,
            disabled_move:  MOVE_NONE,
            disable_turns:  0,
        }
    }
}

impl BattlePoke {
    pub fn boost(&self, slot: usize) -> i8 { self.boosts[slot] }
    pub fn has_sub(&self)    -> bool { self.sub_hp > 0 }
    pub fn is_trapped(&self) -> bool { self.trapping_turns > 0 }

    /// HP as a 0.0–1.0 fraction — for eval comparisons and Python output.
    #[inline]
    pub fn hp_frac(&self) -> f32 {
        if self.max_hp == 0 { return 0.0; }
        self.hp as f32 / self.max_hp as f32
    }

    /// Iterate over non-empty move IDs.
    pub fn move_ids(&self) -> &[u16] {
        &self.moves[..self.move_count as usize]
    }

    pub fn has_move_str(&self, name: &str) -> bool {
        let id = move_to_id(name);
        if id == MOVE_NONE { return false; }
        self.move_ids().contains(&id)
    }

    pub fn species_str(&self) -> &'static str {
        id_to_species(self.species)
    }
}

// ─── Side ─────────────────────────────────────────────────────────────────────

// Max bench size: 5 (6 total - 1 active)
pub const MAX_BENCH: usize = 5;

#[derive(Debug, Clone, Copy)]
pub struct Side {
    pub active:              BattlePoke,
    pub bench:               [BattlePoke; MAX_BENCH],
    pub bench_count:         u8,
    pub reflect:             bool,
    pub reflect_turns:       u8,
    pub light_screen:        bool,
    pub light_screen_turns:  u8,
}

impl Default for Side {
    fn default() -> Self {
        Self {
            active: BattlePoke::default(),
            bench: [BattlePoke::default(); MAX_BENCH],
            bench_count: 0,
            reflect: false,
            reflect_turns: 0,
            light_screen: false,
            light_screen_turns: 0,
        }
    }
}

impl Side {
    pub fn all_pokes(&self) -> impl Iterator<Item = &BattlePoke> {
        std::iter::once(&self.active)
            .chain(self.bench[..self.bench_count as usize].iter())
    }

    pub fn alive_count(&self) -> usize {
        self.all_pokes().filter(|p| !p.fainted).count()
    }

    pub fn switches(&self) -> impl Iterator<Item = &BattlePoke> {
        let trapped = self.active.is_trapped();
        self.bench[..self.bench_count as usize].iter().filter(move |p| {
            !p.fainted
            && !trapped
            && p.status != Status::Slp
            && p.status != Status::Frz
        })
    }
}

// ─── Top-level state — now a plain Copy struct ────────────────────────────────

#[derive(Debug, Clone, Copy, Default)]
pub struct BattleState {
    pub turn:   u32,
    pub ours:   Side,
    pub theirs: Side,
}

// ─── Actions — also string-free ───────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Move     { id: u16 },      // MOVE_* constants or real move ID
    Switch   { species: u16 }, // species ID
    Recharge,
}

impl Action {
    pub fn move_id_u16(&self) -> Option<u16> {
        if let Action::Move { id } = self { Some(*id) } else { None }
    }
    /// String name of the move (for sim/calc lookups that still use &str).
    pub fn move_str(&self) -> Option<&'static str> {
        self.move_id_u16().map(id_to_move)
    }
}

// ─── Legal-action generator ───────────────────────────────────────────────────

pub fn legal_actions(side: &Side) -> Vec<Action> {
    if side.active.recharging {
        return vec![Action::Recharge];
    }
    if side.active.status.is_immobilising() {
        return vec![Action::Move { id: MOVE_SLEEP_FRZ }];
    }

    let mut out = Vec::with_capacity(8);

    for &mid in side.active.move_ids() {
        if mid == MOVE_NONE || mid == MOVE_STRUGGLE || mid == MOVE_RECHARGE {
            continue;
        }
        if mid == side.active.disabled_move { continue; }
        out.push(Action::Move { id: mid });
    }

    for p in side.switches() {
        out.push(Action::Switch { species: p.species });
    }

    if out.is_empty() {
        out.push(Action::Move { id: MOVE_STRUGGLE });
    }
    out
}

// ─── JSON wire format (Python still sends strings) ────────────────────────────
// These types are only used once per turn at the deserialization boundary.
// They are never cloned during search.

#[derive(Debug, Deserialize)]
pub struct JsonBattlePoke {
    pub species:         String,
    #[serde(default = "one_f32")]
    pub hp_frac:         f32,   // poke-env gives us a fraction; we convert to integer
    #[serde(default)]
    pub moves:           Vec<String>,
    #[serde(default)]
    pub status:          Status,
    #[serde(default)]
    pub boosts:          std::collections::HashMap<String, i8>,
    #[serde(default)]
    pub fainted:         bool,
    #[serde(default)]
    pub sub_hp_frac:     f32,
    #[serde(default)]
    pub sleep_turns:     u8,
    #[serde(default)]
    pub recharging:      bool,
    #[serde(default)]
    pub toxic_counter:   u8,
    #[serde(default)]
    pub trapping_turns:  u8,
    #[serde(default)]
    pub confused:        bool,
    #[serde(default)]
    pub confusion_turns: u8,
    #[serde(default)]
    pub crit_stage:      u8,
    #[serde(default)]
    pub disabled_move:   String,
    #[serde(default)]
    pub disable_turns:   u8,
}

fn one_f32() -> f32 { 1.0 }

#[derive(Debug, Deserialize)]
pub struct JsonSide {
    pub active: JsonBattlePoke,
    #[serde(default)]
    pub bench: Vec<JsonBattlePoke>,
    #[serde(default)]
    pub reflect: bool,
    #[serde(default)]
    pub reflect_turns: u8,
    #[serde(default)]
    pub light_screen: bool,
    #[serde(default)]
    pub light_screen_turns: u8,
}

#[derive(Debug, Deserialize)]
pub struct JsonBattleState {
    #[serde(default)]
    pub turn: u32,
    pub ours:   JsonSide,
    pub theirs: JsonSide,
}

// Conversion: JsonBattlePoke → BattlePoke
impl From<JsonBattlePoke> for BattlePoke {
    fn from(j: JsonBattlePoke) -> Self {
        let mut moves = [MOVE_NONE; 4];
        let count = j.moves.len().min(4);
        for (i, m) in j.moves.iter().take(4).enumerate() {
            moves[i] = move_to_id(m);
        }
        let mut boosts = [0i8; 6];
        for (k, v) in &j.boosts {
            if let Some(idx) = boost_key_to_idx(k) {
                boosts[idx] = *v;
            }
        }
        let species_id = species_to_id(&j.species);

        // Pre-compute battle stats from the table — happens once at JSON boundary,
        // never again during search.
        let (max_hp, base_atk, base_def, base_spc, base_spe, type1, type2) =
            if let Some(bs) = crate::data::get_battle_stats(species_id) {
                (bs.hp, bs.atk, bs.def, bs.spc, bs.spe, bs.t1, bs.t2)
            } else {
                (100, 100, 100, 100, 100, crate::data::Type::Normal, None)
            };

        // Convert hp_frac from poke-env to integer HP
        let hp = ((j.hp_frac.clamp(0.0, 1.0) * max_hp as f32).round() as u16).min(max_hp);
        let sub_hp = ((j.sub_hp_frac.clamp(0.0, 1.0) * max_hp as f32).round() as u16);

        BattlePoke {
            species:        species_id,
            hp,
            max_hp,
            base_atk,
            base_def,
            base_spc,
            base_spe,
            type1,
            type2,
            moves,
            move_count:     count as u8,
            status:         j.status,
            boosts,
            fainted:        j.fainted,
            sub_hp,
            sleep_turns:    j.sleep_turns,
            recharging:     j.recharging,
            toxic_counter:  j.toxic_counter,
            trapping_turns: j.trapping_turns,
            confused:       j.confused,
            confusion_turns:j.confusion_turns,
            crit_stage:     j.crit_stage,
            disabled_move:  move_to_id(&j.disabled_move),
            disable_turns:  j.disable_turns,
        }
    }
}

impl From<JsonSide> for Side {
    fn from(j: JsonSide) -> Self {
        let bench_count = j.bench.len().min(MAX_BENCH);
        let mut bench = [BattlePoke::default(); MAX_BENCH];
        for (i, b) in j.bench.into_iter().take(MAX_BENCH).enumerate() {
            bench[i] = BattlePoke::from(b);
        }
        Side {
            active:              BattlePoke::from(j.active),
            bench,
            bench_count:         bench_count as u8,
            reflect:             j.reflect,
            reflect_turns:       j.reflect_turns,
            light_screen:        j.light_screen,
            light_screen_turns:  j.light_screen_turns,
        }
    }
}

impl From<JsonBattleState> for BattleState {
    fn from(j: JsonBattleState) -> Self {
        BattleState {
            turn:   j.turn,
            ours:   Side::from(j.ours),
            theirs: Side::from(j.theirs),
        }
    }
}

// ─── Decision (returned to Python) ───────────────────────────────────────────

#[derive(Debug, Serialize)]
pub struct Decision {
    pub action:         ActionJson,
    pub score:          f64,
    pub nodes_searched: u64,
    pub algorithm:      String,
    pub reason:         String,
}

/// Python-facing action — convert back to strings for JSON output.
#[derive(Debug, Serialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum ActionJson {
    Move    { id: String },
    Switch  { species: String },
    Recharge,
}

impl From<Action> for ActionJson {
    fn from(a: Action) -> Self {
        match a {
            Action::Move { id }      => ActionJson::Move    { id: id_to_move(id).to_string() },
            Action::Switch { species }=> ActionJson::Switch  { species: id_to_species(species).to_string() },
            Action::Recharge          => ActionJson::Recharge,
        }
    }
}

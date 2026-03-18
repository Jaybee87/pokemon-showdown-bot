/// calc.rs v5 — Zero-lookup damage calculator.
///
/// All stat data is read directly from BattlePoke fields, which are
/// pre-computed at JSON parse time. No get_pokemon() calls in the hot path.
/// get_move_by_id() replaces the linear MOVE_TABLE scan with an O(1) array lookup.

use crate::data::*;
use crate::state::*;

// ─── Effective stats — reads directly from BattlePoke, zero table lookups ─────

pub struct EffectiveStats {
    pub atk: i32,
    pub def: i32,
    pub spc: i32,
    pub spe: i32,
}

#[inline]
pub fn effective_stats(p: &BattlePoke) -> EffectiveStats {
    let atk = apply_stage(p.base_atk as i32, p.boost(crate::ids::BOOST_ATK));
    let def = apply_stage(p.base_def as i32, p.boost(crate::ids::BOOST_DEF));
    let spc = apply_stage(p.base_spc as i32, p.boost(crate::ids::BOOST_SPC));
    let spe_raw = p.base_spe as i32;
    let spe_raw = if p.status == Status::Par { (spe_raw / 4).max(1) } else { spe_raw };
    let spe = apply_stage(spe_raw, p.boost(crate::ids::BOOST_SPE));
    EffectiveStats { atk, def, spc, spe }
}

// ─── Pre-computed move data table keyed by move u16 ID ───────────────────────

use std::sync::OnceLock;

static MOVE_DATA_BY_ID: OnceLock<Vec<Option<MoveData>>> = OnceLock::new();

fn get_move_by_id(id: u16) -> Option<&'static MoveData> {
    if id < crate::ids::MOVE_ID_OFFSET { return None; }
    let table = MOVE_DATA_BY_ID.get_or_init(|| {
        let n = crate::ids::MOVE_NAMES.len();
        let mut v: Vec<Option<MoveData>> = vec![None; n];
        for name in crate::ids::MOVE_NAMES.iter() {
            if let Some(md) = get_move(name) {
                let mid = crate::ids::move_to_id(name);
                if mid >= crate::ids::MOVE_ID_OFFSET {
                    let idx = (mid - crate::ids::MOVE_ID_OFFSET) as usize;
                    if idx < n { v[idx] = Some(md); }
                }
            }
        }
        v
    });
    let idx = (id - crate::ids::MOVE_ID_OFFSET) as usize;
    table.get(idx).and_then(|opt| opt.as_ref())
}

// ─── Explosion/Selfdestruct IDs cached after first call ───────────────────────

fn explosion_id()    -> u16 { crate::ids::move_to_id("explosion") }
fn selfdestruct_id() -> u16 { crate::ids::move_to_id("selfdestruct") }

// ─── Core damage formula — pure arithmetic, zero table lookups ────────────────

pub fn damage_range(
    attacker:     &BattlePoke,
    move_id_u16:  u16,
    defender:     &BattlePoke,
    reflect:      bool,
    light_screen: bool,
) -> (u32, u32) {
    let mid_str = crate::ids::id_to_move(move_id_u16);

    if is_ohko(mid_str) {
        let our_spe   = effective_stats(attacker).spe;
        let their_spe = effective_stats(defender).spe;
        return if our_spe >= their_spe {
            (defender.max_hp as u32, defender.max_hp as u32)
        } else { (0, 0) };
    }

    if is_fixed_damage(mid_str) {
        let eff = get_move(mid_str)
            .map(|m| type_effectiveness(m.move_type, defender.type1, defender.type2))
            .unwrap_or(1.0);
        return if eff == 0.0 { (0, 0) } else { (100, 100) };
    }

    let move_data = match get_move_by_id(move_id_u16) {
        Some(m) => m,
        None    => return (0, 0),
    };
    if move_data.bp == 0 { return (0, 0); }

    let atk_eff = effective_stats(attacker);
    let def_eff = effective_stats(defender);

    let is_special = move_data.move_type.is_special();
    let (mut attack, mut defense) = if is_special {
        (atk_eff.spc, def_eff.spc)
    } else {
        (atk_eff.atk, def_eff.def)
    };

    if attacker.status == Status::Brn && !is_special { attack = (attack / 2).max(1); }
    if move_id_u16 == explosion_id() || move_id_u16 == selfdestruct_id() {
        defense = (defense / 2).max(1);
    }
    if !is_special && reflect      { defense = (defense * 2).min(999); }
    if  is_special && light_screen { defense = (defense * 2).min(999); }

    let eff = type_effectiveness(move_data.move_type, defender.type1, defender.type2);
    if eff == 0.0 { return (0, 0); }

    let stab = if move_data.move_type == attacker.type1
                || attacker.type2.map_or(false, |t| t == move_data.move_type)
               { 1.5f64 } else { 1.0 };

    let base = ((42.0 * move_data.bp as f64 * attack as f64) / (defense as f64 * 50.0)) + 2.0;
    let base = (base * stab) as u32;
    let base = (base as f64 * eff) as u32;

    let hits = if move_data.min_hits == move_data.max_hits {
        move_data.min_hits as f64
    } else {
        (move_data.min_hits as f64 + move_data.max_hits as f64) / 2.0
    };

    let min_dmg = ((base as f64 * 217.0 / 255.0) * hits) as u32;
    let max_dmg = (base as f64 * hits) as u32;
    (min_dmg.max(1), max_dmg.max(1))
}

pub fn damage_range_crit(attacker: &BattlePoke, move_id_u16: u16, defender: &BattlePoke) -> (u32, u32) {
    let mid_str = crate::ids::id_to_move(move_id_u16);
    if is_ohko(mid_str) || is_fixed_damage(mid_str) {
        return damage_range(attacker, move_id_u16, defender, false, false);
    }
    let move_data = match get_move_by_id(move_id_u16) {
        Some(m) => m,
        None    => return (0, 0),
    };
    if move_data.bp == 0 { return (0, 0); }

    let is_special = move_data.move_type.is_special();
    let mut attack  = if is_special { attacker.base_spc as i32 } else { attacker.base_atk as i32 };
    let mut defense = if is_special { defender.base_spc as i32 } else { defender.base_def as i32 };

    if attacker.status == Status::Brn && !is_special { attack = (attack / 2).max(1); }
    if move_id_u16 == explosion_id() || move_id_u16 == selfdestruct_id() {
        defense = (defense / 2).max(1);
    }

    let eff = type_effectiveness(move_data.move_type, defender.type1, defender.type2);
    if eff == 0.0 { return (0, 0); }

    let stab = if move_data.move_type == attacker.type1
                || attacker.type2.map_or(false, |t| t == move_data.move_type)
               { 1.5f64 } else { 1.0 };

    let hits = if move_data.min_hits == move_data.max_hits {
        move_data.min_hits as f64
    } else {
        (move_data.min_hits as f64 + move_data.max_hits as f64) / 2.0
    };

    let base = ((42.0 * move_data.bp as f64 * attack as f64) / (defense as f64 * 50.0)) + 2.0;
    let base = (base * stab) as u32;
    let base = (base as f64 * eff) as u32;
    let lo   = ((base as f64 * 217.0 / 255.0) * hits) as u32;
    let hi   = (base as f64 * hits) as u32;
    (lo.max(1), hi.max(1))
}

// ─── Damage as fraction of max HP ─────────────────────────────────────────────

#[inline]
pub fn damage_pct(attacker: &BattlePoke, move_id_u16: u16, defender: &BattlePoke, reflect: bool, light_screen: bool) -> (f64, f64) {
    let (lo, hi) = damage_range(attacker, move_id_u16, defender, reflect, light_screen);
    let mhp = defender.max_hp as f64;
    (lo as f64 / mhp, hi as f64 / mhp)
}

#[inline]
pub fn avg_damage_pct(attacker: &BattlePoke, move_id_u16: u16, defender: &BattlePoke, reflect: bool, light_screen: bool) -> f64 {
    let (lo, hi) = damage_pct(attacker, move_id_u16, defender, reflect, light_screen);
    (lo + hi) / 2.0
}

// ─── KO checks — integer HP comparison, no float ──────────────────────────────

#[inline]
pub fn can_ko(attacker: &BattlePoke, move_id_u16: u16, defender: &BattlePoke) -> bool {
    let (lo, hi) = damage_range(attacker, move_id_u16, defender, false, false);
    (lo + hi) / 2 >= defender.hp as u32
}

#[inline]
pub fn guaranteed_ko(attacker: &BattlePoke, move_id_u16: u16, defender: &BattlePoke) -> bool {
    let (lo, _) = damage_range(attacker, move_id_u16, defender, false, false);
    lo >= defender.hp as u32
}

// ─── String-based wrappers (Python bridge only, not in search hot path) ────────

pub fn can_ko_str(attacker: &BattlePoke, move_id: &str, defender: &BattlePoke) -> bool {
    can_ko(attacker, crate::ids::move_to_id(move_id), defender)
}
pub fn guaranteed_ko_str(attacker: &BattlePoke, move_id: &str, defender: &BattlePoke) -> bool {
    guaranteed_ko(attacker, crate::ids::move_to_id(move_id), defender)
}
pub fn avg_damage_pct_str(attacker: &BattlePoke, move_id: &str, defender: &BattlePoke, reflect: bool, light_screen: bool) -> f64 {
    avg_damage_pct(attacker, crate::ids::move_to_id(move_id), defender, reflect, light_screen)
}

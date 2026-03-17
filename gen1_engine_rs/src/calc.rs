/// calc.rs — Gen 1 damage calculator and turn simulator.
///
/// All calculations operate on pure data (species strings + move IDs) so they
/// can be called from both the search algorithms and from unit tests without
/// needing any poke-env objects.

use crate::data::*;
use crate::state::*;

// ─── Stat helpers ─────────────────────────────────────────────────────────────

pub struct EffectiveStats {
    pub atk: i32,
    pub def: i32,
    pub spc: i32,
    pub spe: i32,
}

pub fn effective_stats(p: &BattlePoke) -> Option<EffectiveStats> {
    let base = get_pokemon(p.species_str())?;
    let atk = apply_stage(calc_stat(base.atk) as i32, p.boost(crate::ids::BOOST_ATK));
    let def = apply_stage(calc_stat(base.def) as i32, p.boost(crate::ids::BOOST_DEF));
    let spc = apply_stage(calc_stat(base.spc) as i32, p.boost(crate::ids::BOOST_SPC));
    let spe_raw = calc_stat(base.spe) as i32;
    let spe = if p.status == Status::Par {
        (spe_raw / 4).max(1)
    } else {
        spe_raw
    };
    let spe = apply_stage(spe, p.boost(crate::ids::BOOST_SPE));
    Some(EffectiveStats { atk, def, spc, spe })
}

// ─── Core damage formula ──────────────────────────────────────────────────────
/// Returns (min_dmg, max_dmg) as raw HP values.
pub fn damage_range(
    attacker: &BattlePoke,
    move_id:  &str,
    defender: &BattlePoke,
    reflect:  bool,
    light_screen: bool,
) -> (u32, u32) {
    let mid = move_id.to_lowercase();
    let mid = mid.as_str();

    if is_ohko(mid) {
        // OHKO: 0 if slower, else OHKO (simplified — assume hits)
        let atk_spe = effective_stats(attacker).map(|s| s.spe).unwrap_or(0);
        let def_spe = effective_stats(defender).map(|s| s.spe).unwrap_or(0);
        return if atk_spe >= def_spe {
            let def_base = get_pokemon(defender.species_str())
                .map(|b| calc_stat_hp(b.hp) as u32).unwrap_or(1);
            (def_base, def_base)
        } else {
            (0, 0)
        };
    }

    if is_fixed_damage(mid) {
        // Fixed-damage moves: approximate as level 100 (= 100 HP)
        let def_types = get_pokemon(defender.species_str())
            .map(|b| (b.t1, b.t2)).unwrap_or((Type::Normal, None));
        let move_data = get_move(mid);
        let eff = move_data.map(|m| {
            type_effectiveness(m.move_type, def_types.0, def_types.1)
        }).unwrap_or(1.0);
        return if eff == 0.0 { (0, 0) } else { (100, 100) };
    }

    let move_data = match get_move(mid) {
        Some(m) => m,
        None => return (0, 0),
    };
    if move_data.bp == 0 { return (0, 0); }

    let atk_stats = match effective_stats(attacker) { Some(s) => s, None => return (0, 0) };
    let def_stats = match effective_stats(defender)  { Some(s) => s, None => return (0, 0) };
    let atk_base  = match get_pokemon(attacker.species_str()) { Some(b) => b, None => return (0, 0) };
    let def_base  = match get_pokemon(defender.species_str()) { Some(b) => b, None => return (0, 0) };

    let is_special = move_data.move_type.is_special();
    let (mut attack, mut defense) = if is_special {
        (atk_stats.spc, def_stats.spc)
    } else {
        (atk_stats.atk, def_stats.def)
    };

    // Burn halves physical Attack
    if attacker.status == Status::Brn && !is_special {
        attack = (attack / 2).max(1);
    }

    // Explosion/Selfdestruct halve target Defense
    if mid == "explosion" || mid == "selfdestruct" {
        defense = (defense / 2).max(1);
    }

    // Screens (not on crits — we use non-crit path for search)
    if !is_special && reflect    { defense = (defense * 2).min(999); }
    if  is_special && light_screen { defense = (defense * 2).min(999); }

    let eff = type_effectiveness(move_data.move_type, def_base.t1, def_base.t2);
    if eff == 0.0 { return (0, 0); }

    let stab = if move_data.move_type == atk_base.t1
                || Some(move_data.move_type) == atk_base.t2
               { 1.5f64 } else { 1.0 };

    // Gen 1 formula: ((2*Lv/5+2) * BP * Atk / Def) / 50 + 2
    let base = ((42.0 * move_data.bp as f64 * attack as f64) / (defense as f64 * 50.0)) + 2.0;
    let base = (base * stab) as u32;
    let base = (base as f64 * eff) as u32;

    // Multi-hit: use expected hits
    let hits = if move_data.min_hits == move_data.max_hits {
        move_data.min_hits as f64
    } else {
        // 2-5 hits, uniform distribution → 3.17
        let lo = move_data.min_hits as f64;
        let hi = move_data.max_hits as f64;
        (lo + hi) / 2.0
    };

    let min_dmg = ((base as f64 * 217.0 / 255.0) * hits) as u32;
    let max_dmg = (base as f64 * hits) as u32;
    (min_dmg.max(1), max_dmg.max(1))
}

/// Damage as fraction of defender's max HP  (0.0–1.0+).
pub fn damage_pct(
    attacker: &BattlePoke, move_id: &str, defender: &BattlePoke,
    reflect: bool, light_screen: bool,
) -> (f64, f64) {
    let (lo, hi) = damage_range(attacker, move_id, defender, reflect, light_screen);
    let max_hp = get_pokemon(defender.species_str())
        .map(|b| calc_stat_hp(b.hp) as f64)
        .unwrap_or(100.0);
    (lo as f64 / max_hp, hi as f64 / max_hp)
}

/// Average damage percentage — convenience.
pub fn avg_damage_pct(
    attacker: &BattlePoke, move_id: &str, defender: &BattlePoke,
    reflect: bool, light_screen: bool,
) -> f64 {
    let (lo, hi) = damage_pct(attacker, move_id, defender, reflect, light_screen);
    (lo + hi) / 2.0
}

// ─── KO checks ───────────────────────────────────────────────────────────────

pub fn can_ko(
    attacker: &BattlePoke, move_id: &str, defender: &BattlePoke,
) -> bool {
    let (lo, hi) = damage_pct(attacker, move_id, defender, false, false);
    (lo + hi) / 2.0 >= defender.hp_frac as f64
}

pub fn guaranteed_ko(
    attacker: &BattlePoke, move_id: &str, defender: &BattlePoke,
) -> bool {
    let (lo, _) = damage_pct(attacker, move_id, defender, false, false);
    lo >= defender.hp_frac as f64
}
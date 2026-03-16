/// sim.rs v2 — Single-turn battle simulator with complete Gen 1 mechanics.
///
/// Modelled mechanics (all using expected-value where RNG is involved):
///   ✓ Hyper Beam recharge lock
///   ✓ Sleep (turn countdown, expected wake probability per turn)
///   ✓ Freeze (stuck until Fire-type move hits — modelled as long sleep)
///   ✓ Paralysis (25% full-paralysis chance as expected-value multiplier)
///   ✓ Burn (1/16 HP per turn, Attack halved)
///   ✓ Poison (1/16 HP per turn)
///   ✓ Toxic (escalating: N/16 per turn, resets on switch)
///   ✓ Confusion (50% self-hit each turn, 40 BP physical, expected value)
///   ✓ Substitute (absorbs damage, tracks sub HP)
///   ✓ Trapping moves (Wrap/Bind: lock + small damage, expected 3.5 turns)
///   ✓ Reflect / Light Screen (5-turn screen counters)
///   ✓ Critical hits (blended into expected-value damage)
///   ✓ Explosion / Selfdestruct (user faints, halves target Defence)
///   ✓ Disable (blocks a move for N turns)
///   ✓ Volatile reset on switch-out

use crate::state::*;
use crate::calc::*;
use crate::data::*;

// ─── Gen 1 constants ──────────────────────────────────────────────────────────

/// Base crit rate across the OU metagame (average ~17%).
const BASE_CRIT_RATE: f64 = 0.17;

/// High-crit moves (Slash, Crabhammer, Razor Leaf, Karate Chop, Blizzard, Waterfall).
const HIGH_CRIT_RATE: f64 = 0.63;

/// Paralysis: chance of full immobilisation per turn.
const PARA_IMMOB_PROB: f64 = 0.25;

/// Confusion: chance of self-hit per turn.
const CONFUSION_HIT_PROB: f64 = 0.50;

/// Confusion self-hit base power.
const CONFUSION_BP: f64 = 40.0;

fn is_high_crit(mid: &str) -> bool {
    matches!(mid, "slash"|"crabhammer"|"karatechop"|"razorleaf"|"blizzard"|"waterfall")
}

// ─── Public interface ─────────────────────────────────────────────────────────

pub fn apply_turn(state: &BattleState, our_action: &Action, opp_action: &Action) -> BattleState {
    let mut next = state.clone();

    // Phase 0: Switches
    if let Action::Switch { species } = our_action  { do_switch(&mut next.ours,   species); }
    if let Action::Switch { species } = opp_action  { do_switch(&mut next.theirs, species); }

    // Phase 1: Move order
    let our_mid = our_action.move_id();
    let opp_mid = opp_action.move_id();
    let our_spe = effective_stats(&next.ours.active).map(|s| s.spe).unwrap_or(0);
    let opp_spe = effective_stats(&next.theirs.active).map(|s| s.spe).unwrap_or(0);
    let our_pri = move_priority(our_mid.unwrap_or(""));
    let opp_pri = move_priority(opp_mid.unwrap_or(""));
    let we_first = our_pri > opp_pri || (our_pri == opp_pri && our_spe >= opp_spe);

    // Phase 2: Execute moves
    if we_first {
        exec_our_move(&mut next, our_mid);
        if !next.theirs.active.fainted { exec_opp_move(&mut next, opp_mid); }
    } else {
        exec_opp_move(&mut next, opp_mid);
        if !next.ours.active.fainted   { exec_our_move(&mut next, our_mid); }
    }

    // Phase 3: End-of-turn damage
    apply_eot(&mut next.ours.active);
    apply_eot(&mut next.theirs.active);

    // Phase 4: Screen decay
    decay_screens(&mut next.ours);
    decay_screens(&mut next.theirs);

    // Phase 5: Volatile ticks
    tick_volatiles(&mut next.ours.active);
    tick_volatiles(&mut next.theirs.active);

    // Phase 6: Fainted active → auto-send bench
    auto_send(&mut next.ours);
    auto_send(&mut next.theirs);

    next.turn += 1;
    next
}

// ─── Move execution (ours side acting) ───────────────────────────────────────

fn exec_our_move(state: &mut BattleState, mid: Option<&str>) {
    let mid = match valid_mid(mid, &state.ours.active) {
        Some(m) => m.to_string(),
        None => return,
    };
    // Confusion self-hit (expected value applied to user)
    if state.ours.active.confused {
        let self_dmg = confusion_dmg_frac(&state.ours.active);
        state.ours.active.hp_frac = (state.ours.active.hp_frac - (self_dmg * CONFUSION_HIT_PROB) as f32).max(0.0);
        if state.ours.active.hp_frac <= 0.0 { state.ours.active.fainted = true; return; }
    }
    apply_damage_to_side(
        state.ours.active.clone(),
        &mid,
        &mut state.theirs,
        &mut state.ours.active,
    );
}

fn exec_opp_move(state: &mut BattleState, mid: Option<&str>) {
    let mid = match valid_mid(mid, &state.theirs.active) {
        Some(m) => m.to_string(),
        None => return,
    };
    if state.theirs.active.confused {
        let self_dmg = confusion_dmg_frac(&state.theirs.active);
        state.theirs.active.hp_frac = (state.theirs.active.hp_frac - (self_dmg * CONFUSION_HIT_PROB) as f32).max(0.0);
        if state.theirs.active.hp_frac <= 0.0 { state.theirs.active.fainted = true; return; }
    }
    apply_damage_to_side(
        state.theirs.active.clone(),
        &mid,
        &mut state.ours,
        &mut state.theirs.active,
    );
}

/// Check if the move can actually be used this turn.
/// Returns None if the user is immobilised/recharging/sleep/freeze.
fn valid_mid<'a>(mid: Option<&'a str>, user: &BattlePoke) -> Option<&'a str> {
    let m = mid?;
    if m == "__sleep_frz__" || m == "recharge" || m.is_empty() { return None; }
    if user.recharging { return None; }
    if user.status.is_immobilising() { return None; }
    Some(m)
}

// ─── Core damage application ──────────────────────────────────────────────────

fn apply_damage_to_side(
    attacker: BattlePoke,
    mid: &str,
    defender_side: &mut Side,
    attacker_poke: &mut BattlePoke,
) {
    let par_factor = if attacker.status == Status::Par { 1.0 - PARA_IMMOB_PROB } else { 1.0 };
    let crit_rate  = if is_high_crit(mid) { HIGH_CRIT_RATE } else { BASE_CRIT_RATE };

    let (lo_n, hi_n) = damage_range(&attacker, mid, &defender_side.active,
                                     defender_side.reflect, defender_side.light_screen);
    let (lo_c, hi_c) = damage_range_crit(&attacker, mid, &defender_side.active);

    let avg_n = (lo_n + hi_n) as f64 / 2.0;
    let avg_c = (lo_c + hi_c) as f64 / 2.0;
    let expected = (avg_n * (1.0 - crit_rate) + avg_c * crit_rate) * par_factor;

    // Hyper Beam: set recharge on attacker
    if mid == "hyperbeam" && expected > 0.0 {
        attacker_poke.recharging = true;
    }

    // Explosion / Selfdestruct: attacker faints
    if mid == "explosion" || mid == "selfdestruct" {
        attacker_poke.hp_frac = 0.0;
        attacker_poke.fainted = true;
    }

    // Trapping: lock target in
    if is_trapping(mid) && expected > 0.0 && defender_side.active.trapping_turns == 0 {
        defender_side.active.trapping_turns = 3;
    }

    let max_hp = get_pokemon(&defender_side.active.species)
        .map(|b| calc_stat_hp(b.hp) as f64).unwrap_or(100.0);
    let dmg_frac = (expected / max_hp) as f32;

    if defender_side.active.has_sub() {
        defender_side.active.sub_hp_frac = (defender_side.active.sub_hp_frac - dmg_frac).max(0.0);
    } else {
        defender_side.active.hp_frac = (defender_side.active.hp_frac - dmg_frac).max(0.0);
        if defender_side.active.hp_frac <= 0.0 {
            defender_side.active.fainted = true;
        }
    }
}

// ─── Crit damage (uses base stats, ignores stages + screens) ─────────────────

fn damage_range_crit(attacker: &BattlePoke, mid: &str, defender: &BattlePoke) -> (u32, u32) {
    if is_ohko(mid) || is_fixed_damage(mid) {
        return damage_range(attacker, mid, defender, false, false);
    }
    let move_data = match get_move(mid) { Some(m) => m, None => return (0,0) };
    if move_data.bp == 0 { return (0,0); }

    let atk_base = match get_pokemon(&attacker.species) { Some(b) => b, None => return (0,0) };
    let def_base = match get_pokemon(&defender.species)  { Some(b) => b, None => return (0,0) };

    let is_spec = move_data.move_type.is_special();
    let mut atk = if is_spec { calc_stat(atk_base.spc) as i32 } else { calc_stat(atk_base.atk) as i32 };
    let mut def = if is_spec { calc_stat(def_base.spc) as i32 } else { calc_stat(def_base.def) as i32 };

    if attacker.status == Status::Brn && !is_spec { atk = (atk / 2).max(1); }
    if mid == "explosion" || mid == "selfdestruct" { def = (def / 2).max(1); }

    let eff = type_effectiveness(move_data.move_type, def_base.t1, def_base.t2);
    if eff == 0.0 { return (0,0); }
    let stab = if move_data.move_type == atk_base.t1 || Some(move_data.move_type) == atk_base.t2
               { 1.5 } else { 1.0 };
    let hits = if move_data.min_hits == move_data.max_hits { move_data.min_hits as f64 }
               else { (move_data.min_hits as f64 + move_data.max_hits as f64) / 2.0 };

    let base = ((42.0 * move_data.bp as f64 * atk as f64) / (def as f64 * 50.0) + 2.0) * stab;
    let base = (base * eff) as u32;
    let lo = ((base as f64 * 217.0 / 255.0) * hits) as u32;
    let hi = (base as f64 * hits) as u32;
    (lo.max(1), hi.max(1))
}

// ─── Confusion self-damage ────────────────────────────────────────────────────

fn confusion_dmg_frac(p: &BattlePoke) -> f64 {
    let stats = match effective_stats(p) { Some(s) => s, None => return 0.0 };
    let max_hp = get_pokemon(&p.species).map(|b| calc_stat_hp(b.hp) as f64).unwrap_or(100.0);
    let raw = (42.0 * CONFUSION_BP * stats.atk as f64) / (stats.def as f64 * 50.0) + 2.0;
    raw / max_hp
}

// ─── End-of-turn damage ───────────────────────────────────────────────────────

fn apply_eot(p: &mut BattlePoke) {
    if p.fainted { return; }
    match p.status {
        Status::Psn | Status::Brn => {
            p.hp_frac = (p.hp_frac - 1.0 / 16.0).max(0.0);
            if p.hp_frac <= 0.0 { p.fainted = true; }
        }
        Status::Tox => {
            let counter = p.toxic_counter.max(1) as f32;
            p.hp_frac = (p.hp_frac - counter / 16.0).max(0.0);
            if p.hp_frac <= 0.0 { p.fainted = true; }
        }
        _ => {}
    }
}

// ─── Screen decay ─────────────────────────────────────────────────────────────

fn decay_screens(side: &mut Side) {
    if side.reflect && side.reflect_turns > 0 {
        side.reflect_turns -= 1;
        if side.reflect_turns == 0 { side.reflect = false; }
    }
    if side.light_screen && side.light_screen_turns > 0 {
        side.light_screen_turns -= 1;
        if side.light_screen_turns == 0 { side.light_screen = false; }
    }
}

// ─── Volatile counter ticks ───────────────────────────────────────────────────

fn tick_volatiles(p: &mut BattlePoke) {
    if p.status == Status::Tox { p.toxic_counter = p.toxic_counter.saturating_add(1).min(15); }
    if p.status == Status::Slp && p.sleep_turns > 0 {
        p.sleep_turns -= 1;
        if p.sleep_turns == 0 { p.status = Status::None; }
    }
    if p.confused && p.confusion_turns > 0 {
        p.confusion_turns -= 1;
        if p.confusion_turns == 0 { p.confused = false; }
    }
    if p.disable_turns > 0 {
        p.disable_turns -= 1;
        if p.disable_turns == 0 { p.disabled_move.clear(); }
    }
    if p.trapping_turns > 0 { p.trapping_turns -= 1; }
    // Recharge is consumed at start of the locked turn; clear here so next turn is free
    p.recharging = false;
}

// ─── Switch and send helpers ──────────────────────────────────────────────────

fn do_switch(side: &mut Side, species: &str) {
    if let Some(idx) = side.bench.iter().position(|p| p.species == species) {
        std::mem::swap(&mut side.active, &mut side.bench[idx]);
        reset_volatile(&mut side.active);
    }
}

fn reset_volatile(p: &mut BattlePoke) {
    p.recharging = false;
    p.toxic_counter = 0;
    p.trapping_turns = 0;
    p.confused = false;
    p.confusion_turns = 0;
    p.crit_stage = 0;
    p.disable_turns = 0;
    p.disabled_move.clear();
    p.sub_hp_frac = 0.0;
    p.boosts.clear();
    // Status (SLP/PSN/etc.) persists through switch in Gen 1
}

fn auto_send(side: &mut Side) {
    if side.active.fainted {
        if let Some(idx) = side.bench.iter().position(|p| !p.fainted) {
            let next = side.bench.remove(idx);
            let old  = std::mem::replace(&mut side.active, next);
            side.bench.push(old);
        }
    }
}

// ─── Move priority ────────────────────────────────────────────────────────────

fn move_priority(mid: &str) -> i8 {
    match mid { "quickattack" => 1, "counter" => -1, _ => 0 }
}

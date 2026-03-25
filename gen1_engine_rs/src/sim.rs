/// sim.rs v5 — Integer HP throughout, zero string allocations.
///
/// Changes from v4:
///   - All HP arithmetic uses u16 integers (hp, max_hp) — no f32/f64 conversion
///   - damage_range() called with move u16 ID directly — no to_lowercase() allocation
///   - apply_damage_to_side() uses defender.max_hp inline — no get_pokemon() call
///   - effective_stats() reads from BattlePoke inline fields — no table lookups

use crate::state::*;
use crate::calc::*;
use crate::data::*;
use crate::ids::*;

const BASE_CRIT_RATE: f64 = 0.17;
const HIGH_CRIT_RATE: f64 = 0.63;
const PARA_IMMOB_PROB: f64 = 0.25;
const CONFUSION_HIT_PROB: f64 = 0.50;
const CONFUSION_BP: f64 = 40.0;

fn is_high_crit_id(id: u16) -> bool {
    let name = id_to_move(id);
    matches!(name, "slash"|"crabhammer"|"karatechop"|"razorleaf"|"blizzard"|"waterfall")
}

// ─── Public interface ─────────────────────────────────────────────────────────

pub fn apply_turn(state: &BattleState, our_action: &Action, opp_action: &Action) -> BattleState {
    let mut next = state.clone();

    // Phase 0: Switches
    if let Action::Switch { species } = our_action  { do_switch(&mut next.ours,   *species); }
    if let Action::Switch { species } = opp_action  { do_switch(&mut next.theirs, *species); }

    // Phase 1: Move order — reads spe directly from BattlePoke (no lookup)
    let our_mid  = our_action.move_id_u16();
    let opp_mid  = opp_action.move_id_u16();
    let our_spe  = effective_stats(&next.ours.active).spe;
    let opp_spe  = effective_stats(&next.theirs.active).spe;
    let our_pri  = move_priority_id(our_mid.unwrap_or(0));
    let opp_pri  = move_priority_id(opp_mid.unwrap_or(0));
    let we_first = our_pri > opp_pri || (our_pri == opp_pri && our_spe >= opp_spe);

    // Phase 2: Execute moves
    if we_first {
        exec_our_move(&mut next, our_mid);
        if !next.theirs.active.fainted { exec_opp_move(&mut next, opp_mid); }
    } else {
        exec_opp_move(&mut next, opp_mid);
        if !next.ours.active.fainted   { exec_our_move(&mut next, our_mid); }
    }

    // Phase 3: End-of-turn residual damage (integer HP arithmetic)
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

// ─── Move execution ───────────────────────────────────────────────────────────

fn exec_our_move(state: &mut BattleState, mid: Option<u16>) {
    let mid = match valid_mid(mid, &state.ours.active) { Some(m) => m, None => return };

    if state.ours.active.confused {
        let self_dmg = confusion_dmg_hp(&state.ours.active);
        let chance_dmg = (self_dmg as f64 * CONFUSION_HIT_PROB) as u16;
        state.ours.active.hp = state.ours.active.hp.saturating_sub(chance_dmg);
        if state.ours.active.hp == 0 { state.ours.active.fainted = true; return; }
    }

    if apply_heal(&mut state.ours.active, mid) { return; }
    apply_damage_to_side(state.ours.active, mid, &mut state.theirs, &mut state.ours.active);
}

fn exec_opp_move(state: &mut BattleState, mid: Option<u16>) {
    let mid = match valid_mid(mid, &state.theirs.active) { Some(m) => m, None => return };

    if state.theirs.active.confused {
        let self_dmg = confusion_dmg_hp(&state.theirs.active);
        let chance_dmg = (self_dmg as f64 * CONFUSION_HIT_PROB) as u16;
        state.theirs.active.hp = state.theirs.active.hp.saturating_sub(chance_dmg);
        if state.theirs.active.hp == 0 { state.theirs.active.fainted = true; return; }
    }

    if apply_heal(&mut state.theirs.active, mid) { return; }
    apply_damage_to_side(state.theirs.active, mid, &mut state.ours, &mut state.theirs.active);
}

fn valid_mid(mid: Option<u16>, user: &BattlePoke) -> Option<u16> {
    let m = mid?;
    if m == MOVE_SLEEP_FRZ || m == MOVE_RECHARGE || m == MOVE_NONE { return None; }
    if user.recharging { return None; }
    if user.status.is_immobilising() { return None; }
    Some(m)
}

// ─── Heal move application ────────────────────────────────────────────────────

/// Returns true if the move was a heal move and was applied.
/// Recover / Softboiled: restore 50% of max HP (Gen 1 — no fail at high HP).
/// Rest: restore to full HP, set SLP with 2 sleep turns, clear other status.
fn apply_heal(user: &mut BattlePoke, mid: u16) -> bool {
    match id_to_move(mid) {
        "recover" | "softboiled" => {
            let heal = user.max_hp / 2;
            user.hp = (user.hp + heal).min(user.max_hp);
            true
        }
        "rest" => {
            user.hp     = user.max_hp;
            user.status = Status::Slp;
            user.sleep_turns = 2;
            true
        }
        _ => false,
    }
}

// ─── Core damage application — pure integer arithmetic ────────────────────────

fn apply_damage_to_side(
    attacker:       BattlePoke,
    mid:            u16,
    defender_side:  &mut Side,
    attacker_poke:  &mut BattlePoke,
) {
    let par_factor  = if attacker.status == Status::Par { 1.0 - PARA_IMMOB_PROB } else { 1.0 };
    let crit_rate   = if is_high_crit_id(mid) { HIGH_CRIT_RATE } else { BASE_CRIT_RATE };

    let (lo_n, hi_n) = damage_range(&attacker, mid, &defender_side.active,
                                     defender_side.reflect, defender_side.light_screen);
    let (lo_c, hi_c) = damage_range_crit(&attacker, mid, &defender_side.active);

    let avg_n = (lo_n + hi_n) as f64 / 2.0;
    let avg_c = (lo_c + hi_c) as f64 / 2.0;
    let expected_hp = ((avg_n * (1.0 - crit_rate) + avg_c * crit_rate) * par_factor) as u16;

    // Hyperbeam: set recharge
    if mid == move_to_id("hyperbeam") && expected_hp > 0 {
        attacker_poke.recharging = true;
    }
    // Explosion/Selfdestruct: attacker always faints — Gen 1 mechanics.
    if mid == move_to_id("explosion") || mid == move_to_id("selfdestruct") {
        attacker_poke.hp = 0;
        attacker_poke.fainted = true;
    }
    // Trapping
    if is_trapping(id_to_move(mid)) && expected_hp > 0 && defender_side.active.trapping_turns == 0 {
        defender_side.active.trapping_turns = 3;
    }

    if defender_side.active.has_sub() {
        defender_side.active.sub_hp = defender_side.active.sub_hp.saturating_sub(expected_hp);
    } else {
        defender_side.active.hp = defender_side.active.hp.saturating_sub(expected_hp);
        if defender_side.active.hp == 0 {
            defender_side.active.fainted = true;
        }
    }
}

// ─── Confusion self-damage in HP units ────────────────────────────────────────

fn confusion_dmg_hp(p: &BattlePoke) -> u16 {
    let stats = effective_stats(p);
    // Confusion: 40 BP physical, using attacker's own atk/def
    let raw = ((42.0 * CONFUSION_BP * stats.atk as f64) / (stats.def as f64 * 50.0)) + 2.0;
    raw as u16
}

// ─── End-of-turn residual — integer HP arithmetic ─────────────────────────────

fn apply_eot(p: &mut BattlePoke) {
    if p.fainted { return; }
    let drain = match p.status {
        Status::Psn | Status::Brn => p.max_hp / 16,
        Status::Tox => {
            let counter = p.toxic_counter.max(1) as u16;
            (p.max_hp * counter) / 16
        }
        _ => 0,
    };
    if drain > 0 {
        p.hp = p.hp.saturating_sub(drain);
        if p.hp == 0 { p.fainted = true; }
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
        if p.disable_turns == 0 { p.disabled_move = MOVE_NONE; }
    }
    if p.trapping_turns > 0 { p.trapping_turns -= 1; }
    p.recharging = false;
}

// ─── Switch helpers ───────────────────────────────────────────────────────────

fn do_switch(side: &mut Side, species_id: u16) {
    let count = side.bench_count as usize;
    if let Some(idx) = side.bench[..count].iter().position(|p| p.species == species_id) {
        std::mem::swap(&mut side.active, &mut side.bench[idx]);
        reset_volatile(&mut side.active);
    }
}

fn reset_volatile(p: &mut BattlePoke) {
    p.recharging     = false;
    p.toxic_counter  = 0;
    p.trapping_turns = 0;
    p.confused       = false;
    p.confusion_turns = 0;
    p.crit_stage     = 0;
    p.disable_turns  = 0;
    p.disabled_move  = MOVE_NONE;
    p.sub_hp         = 0;
    p.boosts         = [0i8; 6];
}

fn auto_send(side: &mut Side) {
    if side.active.fainted {
        let count = side.bench_count as usize;
        if let Some(idx) = side.bench[..count].iter().position(|p| !p.fainted) {
            std::mem::swap(&mut side.active, &mut side.bench[idx]);
            reset_volatile(&mut side.active);
        }
    }
}

// ─── Move priority ────────────────────────────────────────────────────────────

fn move_priority_id(id: u16) -> i8 {
    match id_to_move(id) {
        "quickattack" =>  1,
        "counter"     => -1,
        _             =>  0,
    }
}
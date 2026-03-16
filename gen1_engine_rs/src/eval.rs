/// eval.rs v2 — Richer static evaluator.
///
/// New in v2:
///   - Toxic turn-count penalty (escalating value of opponent being toxiced)
///   - Recharge penalty (opponent free turn = strong advantage)
///   - Confusion bonus
///   - Substitute value (mini-shield for the holder)
///   - Trapping bonus (free damage turns)
///   - Sleep turns remaining (proportional penalty)
///   - Screens — proper turn-count value (not just on/off)
///   - Speed tier scoring (not just binary first/second)
///   - Bench quality: accounts for hp and matchup of non-active mons

use crate::state::*;
use crate::calc::*;

// ─── Terminal detection ───────────────────────────────────────────────────────

pub fn is_terminal(state: &BattleState) -> bool {
    state.ours.alive_count() == 0 || state.theirs.alive_count() == 0
}

pub fn terminal_score(state: &BattleState) -> f64 {
    match (state.ours.alive_count(), state.theirs.alive_count()) {
        (0, 0) => 0.0,
        (0, _) => -10_000.0,
        (_, 0) =>  10_000.0,
        _      => 0.0,
    }
}

// ─── Main evaluator ───────────────────────────────────────────────────────────

pub fn evaluate(state: &BattleState) -> f64 {
    if is_terminal(state) { return terminal_score(state); }

    let mut score = 0.0f64;

    // ── 1. Pokémon count advantage (huge weight — losing a mon is catastrophic) ─
    let our_n   = state.ours.alive_count()   as f64;
    let their_n = state.theirs.alive_count() as f64;
    score += (our_n - their_n) * 600.0;

    // ── 2. Total HP remaining across the whole team ───────────────────────
    let our_hp: f64 = state.ours.all_pokes()
        .filter(|p| !p.fainted).map(|p| p.hp_frac as f64).sum();
    let their_hp: f64 = state.theirs.all_pokes()
        .filter(|p| !p.fainted).map(|p| p.hp_frac as f64).sum();
    score += (our_hp - their_hp) * 250.0;

    // ── 3. Active matchup (type effectiveness + damage output) ────────────
    score += matchup_score(&state.ours.active, &state.theirs.active) * 2.5;

    // ── 4. Status conditions ─────────────────────────────────────────────
    score += status_score(&state.ours.active)    *  1.0;
    score += status_score(&state.theirs.active)  * -1.0;

    // ── 5. Recharge penalty (opponent gets a completely free turn) ────────
    if state.ours.active.recharging   { score -=  280.0; }
    if state.theirs.active.recharging { score +=  280.0; }

    // ── 6. Screens (value proportional to turns remaining) ───────────────
    if state.ours.reflect      { score += state.ours.reflect_turns       as f64 * 15.0; }
    if state.ours.light_screen { score += state.ours.light_screen_turns  as f64 * 15.0; }
    if state.theirs.reflect      { score -= state.theirs.reflect_turns      as f64 * 15.0; }
    if state.theirs.light_screen { score -= state.theirs.light_screen_turns as f64 * 15.0; }

    // ── 7. Speed advantage (percentage lead, not just binary) ────────────
    let our_spe  = effective_stats(&state.ours.active).map(|s| s.spe).unwrap_or(1) as f64;
    let their_spe = effective_stats(&state.theirs.active).map(|s| s.spe).unwrap_or(1) as f64;
    let spe_ratio = (our_spe - their_spe) / their_spe.max(1.0);
    score += spe_ratio.clamp(-1.0, 1.0) * 80.0;

    // ── 8. KO threat (can we KO them this turn? can they KO us?) ─────────
    let we_threaten = state.ours.active.moves.iter()
        .any(|mid| can_ko(&state.ours.active, mid, &state.theirs.active));
    let they_threaten = state.theirs.active.moves.iter()
        .any(|mid| can_ko(&state.theirs.active, mid, &state.ours.active));
    if we_threaten   { score += 220.0; }
    if they_threaten { score -= 220.0; }

    // ── 9. Substitute value ───────────────────────────────────────────────
    score += state.ours.active.sub_hp_frac   as f64 * 180.0;
    score -= state.theirs.active.sub_hp_frac as f64 * 180.0;

    // ── 10. Confusion bonus (expected ~50% turns wasted) ─────────────────
    if state.ours.active.confused   { score -=  90.0; }
    if state.theirs.active.confused { score +=  90.0; }

    // ── 11. Trapping (free damage each turn) ─────────────────────────────
    if state.ours.active.is_trapped()   { score -=  60.0; }
    if state.theirs.active.is_trapped() { score +=  60.0; }

    // ── 12. Bench quality (hp-weighted matchup potential) ─────────────────
    score += bench_quality(&state.ours.bench,   &state.theirs.active) * 0.4;
    score -= bench_quality(&state.theirs.bench, &state.ours.active)   * 0.4;

    score
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/// Offensive matchup: (our best dmg output) – (their best dmg output), scaled.
pub fn matchup_score(ours: &BattlePoke, theirs: &BattlePoke) -> f64 {
    let our_out   = best_expected_damage_pct(ours,   theirs);
    let their_out = best_expected_damage_pct(theirs, ours);
    (our_out - their_out) * 400.0
}

/// Best average damage% any of this attacker's moves can do vs the defender.
fn best_expected_damage_pct(attacker: &BattlePoke, defender: &BattlePoke) -> f64 {
    attacker.moves.iter().map(|mid| {
        avg_damage_pct(attacker, mid, defender, false, false)
    }).fold(0.0f64, f64::max)
}

/// Comprehensive status score from the perspective of the mon holding the status.
/// Negative = bad for the mon (and bad for our score if it's us).
fn status_score(p: &BattlePoke) -> f64 {
    let mut s = match p.status {
        Status::Frz => -380.0,  // essentially a dead mon
        Status::Slp => {
            // Proportional to sleep turns remaining
            let turns_left = p.sleep_turns as f64;
            -300.0 - turns_left * 30.0
        }
        Status::Par => -160.0,  // huge: speed crippled + 25% skip
        Status::Brn => -110.0,
        Status::Tox => {
            // Escalating: 1/16, 2/16, ... per turn — model the remaining total
            let remaining = (16 - p.toxic_counter.min(15)) as f64;
            -80.0 - (p.toxic_counter as f64 * 20.0) - remaining * 5.0
        }
        Status::Psn => -75.0,
        Status::None => 0.0,
    };
    // Recharging is handled separately above but add a small synergy note
    if p.recharging { s -= 30.0; }
    s
}

/// Bench quality: sum of (hp × matchup_score) for all alive benched mons.
fn bench_quality(bench: &[BattlePoke], opponent_active: &BattlePoke) -> f64 {
    bench.iter().filter(|p| !p.fainted).map(|p| {
        let hp_w  = p.hp_frac as f64;
        let dmg   = best_expected_damage_pct(p, opponent_active);
        hp_w * dmg * 200.0
    }).sum()
}
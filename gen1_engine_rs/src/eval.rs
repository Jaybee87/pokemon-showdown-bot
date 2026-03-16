/// eval.rs v3 — Fixes from live battle analysis.
///
/// Key changes:
///   - Hyperbeam penalised when user is paralysed (recharge + 25% para = disaster)
///   - Hyperbeam penalised on turn 1 (opponent gets free recharge turn early)
///   - Sleep status penalty now uses sleep_turns correctly (was always 3 before)
///   - Bench quality excludes sleeping/frozen mons (they can't contribute)
///   - Recharge penalty increased (280 → 350) — free turns are more costly than estimated

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

    // ── 1. Pokémon count advantage ────────────────────────────────────────
    let our_n   = state.ours.alive_count()   as f64;
    let their_n = state.theirs.alive_count() as f64;
    score += (our_n - their_n) * 600.0;

    // ── 2. Total HP remaining across the whole team ───────────────────────
    let our_hp: f64 = state.ours.all_pokes()
        .filter(|p| !p.fainted).map(|p| p.hp_frac as f64).sum();
    let their_hp: f64 = state.theirs.all_pokes()
        .filter(|p| !p.fainted).map(|p| p.hp_frac as f64).sum();
    score += (our_hp - their_hp) * 250.0;

    // ── 3. Active matchup ─────────────────────────────────────────────────
    score += matchup_score(&state.ours.active, &state.theirs.active) * 2.5;

    // ── 4. Status conditions ─────────────────────────────────────────────
    score += status_score(&state.ours.active)   *  1.0;
    score += status_score(&state.theirs.active) * -1.0;

    // ── 5. Recharge penalty ───────────────────────────────────────────────
    // Increased from 280 to 350: a free turn in Gen 1 is enormously valuable.
    if state.ours.active.recharging   { score -= 350.0; }
    if state.theirs.active.recharging { score += 350.0; }

    // ── 6. Screens ────────────────────────────────────────────────────────
    if state.ours.reflect        { score += state.ours.reflect_turns       as f64 * 15.0; }
    if state.ours.light_screen   { score += state.ours.light_screen_turns  as f64 * 15.0; }
    if state.theirs.reflect      { score -= state.theirs.reflect_turns     as f64 * 15.0; }
    if state.theirs.light_screen { score -= state.theirs.light_screen_turns as f64 * 15.0; }

    // ── 7. Speed advantage ────────────────────────────────────────────────
    let our_spe   = effective_stats(&state.ours.active).map(|s| s.spe).unwrap_or(1) as f64;
    let their_spe = effective_stats(&state.theirs.active).map(|s| s.spe).unwrap_or(1) as f64;
    let spe_ratio = (our_spe - their_spe) / their_spe.max(1.0);
    score += spe_ratio.clamp(-1.0, 1.0) * 80.0;

    // ── 8. KO threat ──────────────────────────────────────────────────────
    // Don't count Hyperbeam as a KO threat when paralysed — too risky.
    let we_threaten = state.ours.active.moves.iter().any(|mid| {
        if mid == "hyperbeam" && state.ours.active.status == Status::Par { return false; }
        can_ko(&state.ours.active, mid, &state.theirs.active)
    });
    let they_threaten = state.theirs.active.moves.iter()
        .any(|mid| can_ko(&state.theirs.active, mid, &state.ours.active));
    if we_threaten   { score += 220.0; }
    if they_threaten { score -= 220.0; }

    // ── 9. Hyperbeam-specific penalties ───────────────────────────────────
    // A) Paralysed user + recharge = catastrophic.
    if state.ours.active.recharging && state.ours.active.status == Status::Par {
        score -= 300.0;  // increased from 200
    }
    // B) Turn 1-3 recharge: opponent exploits the free turn early.
    if state.ours.active.recharging && state.turn <= 3 {
        score -= 150.0;
    }
    // C) Opponent has a healing move (Softboiled / Recover / Rest) AND we are
    //    recharging — they will heal on our free turn, making HB a net-zero
    //    or negative trade. This is the core Chansey/Starmie stall trap.
    let opp_has_heal = state.theirs.active.moves.iter().any(|m| {
        matches!(m.as_str(), "softboiled" | "recover" | "rest")
    });
    if state.ours.active.recharging && opp_has_heal {
        score -= 400.0;
    }
    // D) We are paralysed AND opponent has a healing move — even without
    //    recharge, Hyperbeam is very risky because para + recharge means
    //    two consecutive wasted turns while they heal. Penalise the move
    //    being in our kit by reducing KO-threat bonus for HB specifically.
    if state.ours.active.status == Status::Par && opp_has_heal {
        score -= 120.0;
    }

    // ── 10. Substitute value ──────────────────────────────────────────────
    score += state.ours.active.sub_hp_frac   as f64 * 180.0;
    score -= state.theirs.active.sub_hp_frac as f64 * 180.0;

    // ── 11. Confusion ─────────────────────────────────────────────────────
    if state.ours.active.confused   { score -=  90.0; }
    if state.theirs.active.confused { score +=  90.0; }

    // ── 12. Trapping ──────────────────────────────────────────────────────
    if state.ours.active.is_trapped()   { score -=  60.0; }
    if state.theirs.active.is_trapped() { score +=  60.0; }

    // ── 13. Bench quality ─────────────────────────────────────────────────
    score += bench_quality(&state.ours.bench,   &state.theirs.active) * 0.4;
    score -= bench_quality(&state.theirs.bench, &state.ours.active)   * 0.4;

    score
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

pub fn matchup_score(ours: &BattlePoke, theirs: &BattlePoke) -> f64 {
    let our_out   = best_expected_damage_pct(ours,   theirs);
    let their_out = best_expected_damage_pct(theirs, ours);
    (our_out - their_out) * 400.0
}

fn best_expected_damage_pct(attacker: &BattlePoke, defender: &BattlePoke) -> f64 {
    attacker.moves.iter().map(|mid| {
        avg_damage_pct(attacker, mid, defender, false, false)
    }).fold(0.0f64, f64::max)
}

fn status_score(p: &BattlePoke) -> f64 {
    let mut s = match p.status {
        Status::Frz => -500.0,  // effectively dead — can only thaw from a Fire move
        Status::Slp => {
            // Each turn asleep = one completely wasted turn.
            // sleep_turns tracks turns already slept; penalty grows with time.
            // -270 minimum (unknown duration) scaling up to -630 (7 turns max).
            let turns = p.sleep_turns as f64;
            // More turns slept = more turns remaining in expected value.
            // We penalise proportionally: base -270, +90 per turn already slept.
            -(270.0 + turns * 90.0)
        }
        Status::Par => -160.0,
        Status::Brn => -110.0,
        Status::Tox => {
            let remaining = (16 - p.toxic_counter.min(15)) as f64;
            -80.0 - (p.toxic_counter as f64 * 20.0) - remaining * 5.0
        }
        Status::Psn  => -75.0,
        Status::None => 0.0,
    };
    if p.recharging { s -= 30.0; }
    s
}

fn bench_quality(bench: &[BattlePoke], opponent_active: &BattlePoke) -> f64 {
    bench.iter().filter(|p| !p.fainted).map(|p| {
        // Sleeping or frozen mons contribute zero bench quality — they cannot
        // be switched in productively (switches() already blocks them, but
        // the evaluator should also not inflate our score because of them).
        if p.status == Status::Slp || p.status == Status::Frz {
            return 0.0;
        }
        let hp_w = p.hp_frac as f64;
        let dmg  = best_expected_damage_pct(p, opponent_active);
        hp_w * dmg * 200.0
    }).sum()
}
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

    // ── 4b. Healing move availability and urgency ─────────────────────────
    let our_has_heal = state.ours.active.moves.iter().any(|m| {
        matches!(m.as_str(), "recover" | "softboiled")
    });
    let their_has_heal = state.theirs.active.moves.iter().any(|m| {
        matches!(m.as_str(), "recover" | "softboiled")
    });
    // Low HP + heal available = more valuable than current HP suggests
    if our_has_heal && state.ours.active.hp_frac < 0.55 {
        let heal_value = (0.50 - (0.55 - state.ours.active.hp_frac as f64)).max(0.0) * 250.0;
        score += heal_value;
    }
    if their_has_heal && state.theirs.active.hp_frac < 0.55 {
        let heal_value = (0.50 - (0.55 - state.theirs.active.hp_frac as f64)).max(0.0) * 250.0;
        score -= heal_value;
    }
    // Toxic urgency: a Toxiced mon with a heal should use it NOW — escalating
    // damage will kill it faster than the heal bonus above captures.
    if our_has_heal && state.ours.active.status == Status::Tox {
        score += 200.0;  // bonus for being in a state where healing is correct
    }
    if their_has_heal && state.theirs.active.status == Status::Tox {
        score -= 200.0;
    }

    // ── 5. Recharge penalty ───────────────────────────────────────────────
    if state.ours.active.recharging   { score -= 350.0; }
    if state.theirs.active.recharging { score += 350.0; }

    // ── 6. Screens ────────────────────────────────────────────────────────
    if state.ours.reflect        { score += state.ours.reflect_turns        as f64 * 15.0; }
    if state.ours.light_screen   { score += state.ours.light_screen_turns   as f64 * 15.0; }
    if state.theirs.reflect      { score -= state.theirs.reflect_turns      as f64 * 15.0; }
    if state.theirs.light_screen { score -= state.theirs.light_screen_turns as f64 * 15.0; }

    // ── 7. Speed advantage ────────────────────────────────────────────────
    let our_spe   = effective_stats(&state.ours.active).map(|s| s.spe).unwrap_or(1) as f64;
    let their_spe = effective_stats(&state.theirs.active).map(|s| s.spe).unwrap_or(1) as f64;
    let spe_ratio = (our_spe - their_spe) / their_spe.max(1.0);
    score += spe_ratio.clamp(-1.0, 1.0) * 80.0;

    // ── 8. KO threat ──────────────────────────────────────────────────────
    let we_threaten = state.ours.active.moves.iter().any(|mid| {
        if mid == "hyperbeam" && state.ours.active.status == Status::Par { return false; }
        // Don't count HB as a threat if opponent has a heal — they recover on the free turn
        if mid == "hyperbeam" && their_has_heal { return false; }
        can_ko(&state.ours.active, mid, &state.theirs.active)
    });
    let they_threaten = state.theirs.active.moves.iter()
        .any(|mid| can_ko(&state.theirs.active, mid, &state.ours.active));
    if we_threaten   { score += 220.0; }
    if they_threaten { score -= 220.0; }

    // ── 9. Hyperbeam-specific penalties ───────────────────────────────────
    let opp_has_heal = their_has_heal; // already computed above
    // A) Paralysed user + recharge.
    if state.ours.active.recharging && state.ours.active.status == Status::Par {
        score -= 300.0;
    }
    // B) Early-game recharge.
    if state.ours.active.recharging && state.turn <= 3 {
        score -= 150.0;
    }
    // C) Opponent has healing move + we are recharging — they heal for free.
    if state.ours.active.recharging && opp_has_heal {
        score -= 500.0;  // increased from 400 — this is a confirmed trap
    }
    // D) Paralysed + opponent has healer — HB is nearly always wrong.
    if state.ours.active.status == Status::Par && opp_has_heal {
        score -= 120.0;
    }
    // E) HB available but opponent has heal — penalise having it as the best
    //    option pre-emptively. This nudges the search away from HB lines.
    let we_have_hb = state.ours.active.moves.iter().any(|m| m == "hyperbeam");
    if we_have_hb && opp_has_heal && !state.ours.active.recharging {
        score -= 80.0;
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
    // Weight reduced from 0.4 to 0.2 to reduce switch oscillation — the search
    // was over-valuing bench mons and switching every turn against neutral targets.
    score += bench_quality(&state.ours.bench,   &state.theirs.active) * 0.2;
    score -= bench_quality(&state.theirs.bench, &state.ours.active)   * 0.2;

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
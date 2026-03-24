/// eval.rs v4 — Two Gen 1-specific evaluator tweaks from external review.
///
/// New in v4:
///   - PAR speed breakpoint bonus (§7b): discrete +90/-90 when paralysis
///     flips who moves first, capturing the binary nature of Gen 1 speed control
///   - Crit pressure (§7c): small bonus proportional to crit rate differential
///     when faster, reflecting that fast mons (Tauros/Alakazam/Starmie at ~22%)
///     apply meaningfully more crit variance than slow mons (~6%) in damage races
///
/// Previous changes (v3):
///   - Hyperbeam penalised when user is paralysed (recharge + 25% para = disaster)
///   - Hyperbeam penalised on turn 1 (opponent gets free recharge turn early)
///   - Sleep status penalty now uses sleep_turns correctly (was always 3 before)
///   - Bench quality excludes sleeping/frozen mons (they can't contribute)
///   - Recharge penalty increased (280 → 350) — free turns are more costly than estimated

use crate::state::*;
use crate::calc::{avg_damage_pct, can_ko, guaranteed_ko_screens, effective_stats};

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
        .filter(|p| !p.fainted).map(|p| p.hp_frac() as f64).sum();
    let their_hp: f64 = state.theirs.all_pokes()
        .filter(|p| !p.fainted).map(|p| p.hp_frac() as f64).sum();
    score += (our_hp - their_hp) * 250.0;

    // ── 3. Active matchup ─────────────────────────────────────────────────
    score += matchup_score(
        &state.ours.active, &state.theirs.active,
        state.theirs.reflect, state.theirs.light_screen,
        state.ours.reflect,   state.ours.light_screen,
    ) * 2.5;

    // ── 4. Status conditions ─────────────────────────────────────────────
    score += status_score(&state.ours.active)   *  1.0;
    score += status_score(&state.theirs.active) * -1.0;

    // ── 4b. Healing move availability and urgency ─────────────────────────
    let our_has_heal = state.ours.active.has_move_str("recover")
        || state.ours.active.has_move_str("softboiled");
    let their_has_heal = state.theirs.active.has_move_str("recover")
        || state.theirs.active.has_move_str("softboiled");
    // Low HP + heal available = more valuable than current HP suggests
    if our_has_heal && state.ours.active.hp_frac() < 0.55 {
        let heal_value = (0.50 - (0.55 - state.ours.active.hp_frac() as f64)).max(0.0) * 250.0;
        score += heal_value;
    }
    if their_has_heal && state.theirs.active.hp_frac() < 0.55 {
        let heal_value = (0.50 - (0.55 - state.theirs.active.hp_frac() as f64)).max(0.0) * 250.0;
        score -= heal_value;
    }
    // Penalise healing at full or near-full HP — Softboiled/Recover at 100% is
    // a wasted turn. This prevents the engine locking into a mutual stall loop
    // where both sides preemptively heal rather than pressing damage.
    // The threshold is 0.90: above that, healing costs more than it gains.
    if our_has_heal && state.ours.active.hp_frac() >= 0.90 {
        score -= 150.0;
    }
    if their_has_heal && state.theirs.active.hp_frac() >= 0.90 {
        score += 150.0;  // opponent wasting a heal turn is good for us
    }
    // Toxic urgency: a Toxiced mon with a heal should use it NOW.
    if our_has_heal && state.ours.active.status == Status::Tox {
        score += 200.0;
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
    let our_spe   = effective_stats(&state.ours.active).spe   as f64;
    let their_spe = effective_stats(&state.theirs.active).spe as f64;
    let spe_ratio = (our_spe - their_spe) / their_spe.max(1.0);
    score += spe_ratio.clamp(-1.0, 1.0) * 80.0;

    // ── 7b. PAR speed breakpoint bonus ───────────────────────────────────
    // In Gen 1, speed control is binary: you either go first or you don't.
    // A gradual spe_ratio undersells what PAR actually does — it flips a
    // matchup from "they move first" to "we move first", which is a discrete
    // strategic advantage worth more than a smooth interpolation captures.
    //
    // When PAR flips who has first-move advantage, award a discrete bonus.
    // We compute base speeds (pre-PAR) to detect the before/after flip.
    let our_base_spe   = state.ours.active.base_spe   as f64;
    let their_base_spe = state.theirs.active.base_spe as f64;
    let our_par   = state.ours.active.status   == Status::Par;
    let their_par = state.theirs.active.status == Status::Par;
    // Was trailing in speed before PAR, now leading after PAR?
    if their_par && !our_par {
        let their_par_spe = their_base_spe / 4.0;
        if our_base_spe < their_base_spe && our_spe > their_par_spe {
            score += 90.0; // we flipped to going first — meaningful advantage
        }
    }
    // Did we lose first-move advantage because WE are PAR'd?
    if our_par && !their_par {
        let our_par_spe = our_base_spe / 4.0;
        if their_base_spe < our_base_spe && their_spe > our_par_spe {
            score -= 90.0; // opponent flipped to going first — meaningful disadvantage
        }
    }

    // ── 7c. Crit pressure ────────────────────────────────────────────────
    // Gen 1 critical hit rate = base_speed / 512 (per-hit, ignoring Focus
    // Energy). Fast Pokémon land crits ~20-23% of the time; slow ones ~6-10%.
    // In close damage races the eval currently treats two 20% rolls as equal
    // to one 40% roll — but a 23% crit Alakazam attacking first is meaningfully
    // different from a 6% crit Snorlax attacking second. We add a small bonus
    // proportional to the *difference* in expected crit rates when it's us
    // attacking a slower, lower-crit-rate defender. Kept small (max ~25 pts)
    // so it nudges move selection in close races without distorting strategy.
    let our_crit_rate   = state.ours.active.base_spe   as f64 / 512.0;
    let their_crit_rate = state.theirs.active.base_spe as f64 / 512.0;
    // Only apply when we're the faster attacker — crit pressure is asymmetric
    if our_spe > their_spe {
        let crit_edge = (our_crit_rate - their_crit_rate).max(0.0);
        score += crit_edge * 120.0; // max ~(0.23-0.06)*120 ≈ 20 pts
    } else {
        let crit_edge = (their_crit_rate - our_crit_rate).max(0.0);
        score -= crit_edge * 120.0;
    }

    // ── 8. KO threat ──────────────────────────────────────────────────────
    // For most moves: use can_ko (average roll) as a threat heuristic.
    // For Explosion/Selfdestruct: only count as a threat when the minimum
    // damage roll guarantees the KO — the user always faints, so an
    // optimistic average-roll KO that doesn't land is a losing trade.
    // Pass the defender's active screens so damage is evaluated accurately.
    let their_reflect      = state.theirs.reflect;
    let their_light_screen = state.theirs.light_screen;
    let our_reflect        = state.ours.reflect;
    let our_light_screen   = state.ours.light_screen;

    let we_threaten = state.ours.active.move_ids().iter().any(|&mid_u16| {
        let mid_str = crate::ids::id_to_move(mid_u16);
        if mid_str == "hyperbeam" && state.ours.active.status == Status::Par { return false; }
        if mid_str == "hyperbeam" && their_has_heal { return false; }
        if mid_str == "explosion" || mid_str == "selfdestruct" {
            // Self-destruct: only a real threat when min roll KOs through screens
            return guaranteed_ko_screens(
                &state.ours.active, mid_u16, &state.theirs.active,
                their_reflect, their_light_screen,
            );
        }
        can_ko(&state.ours.active, mid_u16, &state.theirs.active)
    });
    let they_threaten = state.theirs.active.move_ids().iter().any(|&mid_u16| {
        let mid_str = crate::ids::id_to_move(mid_u16);
        if mid_str == "explosion" || mid_str == "selfdestruct" {
            return guaranteed_ko_screens(
                &state.theirs.active, mid_u16, &state.ours.active,
                our_reflect, our_light_screen,
            );
        }
        can_ko(&state.theirs.active, mid_u16, &state.ours.active)
    });
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
    let we_have_hb = state.ours.active.has_move_str("hyperbeam");
    if we_have_hb && opp_has_heal && !state.ours.active.recharging {
        score -= 80.0;
    }

    // ── 10. Substitute value ──────────────────────────────────────────────
    score += (state.ours.active.sub_hp   as f64 / state.ours.active.max_hp.max(1)   as f64) * 180.0;
    score -= (state.theirs.active.sub_hp as f64 / state.theirs.active.max_hp.max(1) as f64) * 180.0;

    // ── 11. Confusion ─────────────────────────────────────────────────────
    if state.ours.active.confused   { score -=  90.0; }
    if state.theirs.active.confused { score +=  90.0; }

    // ── 12. Trapping ──────────────────────────────────────────────────────
    if state.ours.active.is_trapped()   { score -=  60.0; }
    if state.theirs.active.is_trapped() { score +=  60.0; }

    // ── 13. Bench quality ─────────────────────────────────────────────────
    // Weight reduced from 0.4 to 0.2 to reduce switch oscillation — the search
    // was over-valuing bench mons and switching every turn against neutral targets.
    score += bench_quality(
        &state.ours.bench[..state.ours.bench_count as usize],
        &state.theirs.active,
        state.theirs.reflect, state.theirs.light_screen,
    ) * 0.2;
    score -= bench_quality(
        &state.theirs.bench[..state.theirs.bench_count as usize],
        &state.ours.active,
        state.ours.reflect, state.ours.light_screen,
    ) * 0.2;

    score
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

pub fn matchup_score(ours: &BattlePoke, theirs: &BattlePoke, their_reflect: bool, their_light_screen: bool, our_reflect: bool, our_light_screen: bool) -> f64 {
    let our_out   = best_expected_damage_pct(ours,   theirs, their_reflect,  their_light_screen);
    let their_out = best_expected_damage_pct(theirs, ours,   our_reflect,    our_light_screen);
    (our_out - their_out) * 400.0
}

fn best_expected_damage_pct(attacker: &BattlePoke, defender: &BattlePoke, reflect: bool, light_screen: bool) -> f64 {
    attacker.move_ids().iter().map(|&mid_u16| {
        avg_damage_pct(attacker, mid_u16, defender, reflect, light_screen)
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

fn bench_quality(bench: &[BattlePoke], opponent_active: &BattlePoke, opp_reflect: bool, opp_light_screen: bool) -> f64 {
    bench.iter().filter(|p| !p.fainted).map(|p| {
        // Sleeping or frozen mons contribute zero bench quality — they cannot
        // be switched in productively (switches() already blocks them, but
        // the evaluator should also not inflate our score because of them).
        if p.status == Status::Slp || p.status == Status::Frz {
            return 0.0;
        }
        let hp_w = p.hp_frac() as f64;
        let dmg  = best_expected_damage_pct(p, opponent_active, opp_reflect, opp_light_screen);
        hp_w * dmg * 200.0
    }).sum()
}
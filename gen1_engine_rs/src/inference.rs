/// inference.rs — Opponent move inference.
///
/// In competitive Gen 1 OU, most Pokémon run a very small pool of viable
/// movesets.  When we don't know the opponent's full moveset we can fill in
/// likely moves based on:
///   1. Moves already observed (ground truth)
///   2. Species-specific common moveset templates
///   3. Generic fallback: best STAB + coverage moves for the type
///
/// The inferred move list is added to `BattlePoke.moves` before the search
/// runs, so the engine reasons about the most likely opponent capability.
///
/// This is called from rust_engine_bridge.py → build_state() — or you can
/// call it directly inside main.rs before running search.

use crate::data::{get_pokemon, get_move, Type, MOVE_TABLE};
use crate::state::BattlePoke;

// ─── OU moveset templates ─────────────────────────────────────────────────────
//
// These are the most commonly seen movesets at the Gen 1 OU level.
// Listed in rough frequency order.  Engine picks the top-N that don't
// conflict with already-observed moves.

struct Template {
    species:  &'static str,
    movesets: &'static [&'static [&'static str]],
}

static TEMPLATES: &[Template] = &[
    Template { species: "tauros",    movesets: &[
        &["bodyslam", "earthquake", "blizzard", "hyperbeam"],
        &["bodyslam", "earthquake", "fireblast", "hyperbeam"],
    ]},
    Template { species: "starmie",   movesets: &[
        &["surf", "thunderbolt", "psychic", "recover"],
        &["surf", "blizzard",    "psychic", "recover"],
        &["surf", "thunderbolt", "blizzard","recover"],
    ]},
    Template { species: "alakazam",  movesets: &[
        &["psychic", "seismictoss", "thunderwave", "recover"],
        &["psychic", "seismictoss", "thunderwave", "reflect"],
        &["psychic", "seismictoss", "thunderwave", "psychic"],
    ]},
    Template { species: "chansey",   movesets: &[
        &["softboiled", "thunderwave", "seismictoss", "reflect"],
        &["softboiled", "thunderwave", "seismictoss", "icebeam"],
        &["softboiled", "thunderwave", "seismictoss", "sing"],
    ]},
    Template { species: "snorlax",   movesets: &[
        &["bodyslam", "earthquake", "selfdestruct", "amnesia"],
        &["bodyslam", "earthquake", "fireblast",    "selfdestruct"],
        &["bodyslam", "icebeam",    "earthquake",   "selfdestruct"],
    ]},
    Template { species: "exeggutor", movesets: &[
        &["psychic", "sleeppowder", "explosion", "hyperbeam"],
        &["psychic", "sleeppowder", "explosion", "solarbeam"],
        &["psychic", "sleeppowder", "stunspore",  "explosion"],
    ]},
    Template { species: "jolteon",   movesets: &[
        &["thunderbolt", "thunder", "bodyslam", "doublekick"],
        &["thunderbolt", "thunder", "thunderwave", "bodyslam"],
    ]},
    Template { species: "rhydon",    movesets: &[
        &["earthquake", "rockslide", "bodyslam", "substitute"],
        &["earthquake", "rockslide", "submission", "bodyslam"],
    ]},
    Template { species: "lapras",    movesets: &[
        &["blizzard", "thunderbolt", "bodyslam", "sing"],
        &["blizzard", "thunderbolt", "bodyslam", "confuseray"],
    ]},
    Template { species: "gengar",    movesets: &[
        &["psychic", "explosion", "hypnosis", "thunderbolt"],
        &["nightshade", "hypnosis", "thunderbolt", "explosion"],
    ]},
    Template { species: "slowbro",   movesets: &[
        &["surf", "psychic", "amnesia", "thunderwave"],
        &["surf", "psychic", "amnesia", "reflect"],
    ]},
    Template { species: "cloyster",  movesets: &[
        &["blizzard", "clamp", "explosion", "hyperbeam"],
        &["blizzard", "surf",  "explosion", "hyperbeam"],
    ]},
    Template { species: "zapdos",    movesets: &[
        &["thunderbolt", "drillpeck", "thunderwave", "agility"],
        &["thunderbolt", "drillpeck", "thunder",     "hyperbeam"],
    ]},
    Template { species: "articuno", movesets: &[
        &["blizzard", "icebeam", "agility", "hyperbeam"],
        &["blizzard", "icebeam", "reflect", "hyperbeam"],
    ]},
    Template { species: "moltres",  movesets: &[
        &["fireblast", "flamethrower", "agility", "hyperbeam"],
        &["fireblast", "flamethrower", "hyperbeam", "firespin"],
    ]},
    Template { species: "dragonite", movesets: &[
        &["agility", "surf", "blizzard", "hyperbeam"],
        &["agility", "fireblast", "thunderbolt", "hyperbeam"],
    ]},
    Template { species: "golem",     movesets: &[
        &["earthquake", "rockslide", "explosion", "bodyslam"],
        &["earthquake", "rockslide", "explosion", "fireblast"],
    ]},
    Template { species: "machamp",   movesets: &[
        &["submission", "bodyslam", "earthquake", "hyperbeam"],
        &["submission", "bodyslam", "rockslide",  "hyperbeam"],
    ]},
    Template { species: "nidoking",  movesets: &[
        &["earthquake", "thunderbolt", "blizzard", "bodyslam"],
        &["earthquake", "fireblast",   "blizzard", "bodyslam"],
    ]},
    Template { species: "nidoqueen", movesets: &[
        &["earthquake", "blizzard", "thunderbolt", "bodyslam"],
    ]},
    Template { species: "victreebel",movesets: &[
        &["sleeppowder", "razorleaf", "wrap", "hyperbeam"],
        &["sleeppowder", "razorleaf", "stunspore", "hyperbeam"],
    ]},
    Template { species: "venusaur",  movesets: &[
        &["sleeppowder", "razorleaf", "bodyslam", "hyperbeam"],
    ]},
    Template { species: "persian",   movesets: &[
        &["slash", "bubblebeam", "thunderbolt", "hyperbeam"],
        &["slash", "bodyslam",   "thunderbolt", "hyperbeam"],
    ]},
    Template { species: "hypno",     movesets: &[
        &["psychic", "hypnosis", "thunderwave", "softboiled"],
        &["psychic", "hypnosis", "seismictoss", "thunderwave"],
    ]},
    Template { species: "jynx",      movesets: &[
        &["blizzard", "psychic", "lovelykiss", "seismictoss"],
    ]},
    Template { species: "electabuzz",movesets: &[
        &["thunderbolt", "thunder", "thunderwave", "seismictoss"],
    ]},
    Template { species: "magmar",    movesets: &[
        &["fireblast", "firepunch", "thunderbolt", "hyperbeam"],
    ]},
    Template { species: "kabutops",  movesets: &[
        &["waterfall", "slash", "bodyslam", "surf"],
    ]},
    Template { species: "aerodactyl",movesets: &[
        &["hyperbeam", "earthquake", "rockslide", "agility"],
        &["hyperbeam", "earthquake", "fireblast", "agility"],
    ]},
    Template { species: "gyarados",  movesets: &[
        &["surf", "blizzard", "bodyslam", "hyperbeam"],
        &["surf", "thunderbolt", "bodyslam", "hyperbeam"],
    ]},
];

// ─── Public API ───────────────────────────────────────────────────────────────

/// Fill in `poke.moves` with inferred moves for any slots that are empty
/// (i.e. the opponent hasn't revealed those moves yet).
///
/// Strategy:
///   1. If we have a template for this species, pick the template whose
///      observed moves best match, then fill remaining slots from that template.
///   2. If no template, fall back to generic type-coverage heuristic.
///   3. Never overwrite moves already in the list (observed = ground truth).
///
/// `max_moves` is typically 4 (Gen 1 max).
pub fn infer_moves(poke: &mut BattlePoke, max_moves: usize) {
    if poke.moves.len() >= max_moves { return; }

    let known: Vec<String> = poke.moves.clone();
    let species = poke.species.to_lowercase();

    let inferred = if let Some(template_moves) = best_template(&species, &known) {
        template_moves
    } else {
        generic_coverage(&species, &known, max_moves)
    };

    for mid in inferred {
        if poke.moves.len() >= max_moves { break; }
        if !poke.moves.contains(&mid) {
            poke.moves.push(mid);
        }
    }
}

// ─── Template matching ────────────────────────────────────────────────────────

fn best_template(species: &str, known: &[String]) -> Option<Vec<String>> {
    let tmpl = TEMPLATES.iter().find(|t| t.species == species)?;

    // Score each moveset by how many known moves it contains
    let best_set = tmpl.movesets.iter().max_by_key(|ms| {
        known.iter().filter(|k| ms.iter().any(|m| *m == k.as_str())).count()
    })?;

    Some(best_set.iter().map(|s| s.to_string()).collect())
}

// ─── Generic coverage fallback ────────────────────────────────────────────────

fn generic_coverage(species: &str, known: &[String], want: usize) -> Vec<String> {
    let base = match get_pokemon(species) { Some(b) => b, None => return Vec::new() };
    let mut candidates: Vec<(String, f64)> = Vec::new();

    for (mid, mdata) in MOVE_TABLE.iter() {
        if mdata.bp == 0 { continue; }
        if known.iter().any(|k| k == mid) { continue; }

        // Score: STAB bonus + raw BP
        let stab = if mdata.move_type == base.t1 || Some(mdata.move_type) == base.t2 { 1.5 } else { 1.0 };
        let score = mdata.bp as f64 * stab;
        candidates.push((mid.to_string(), score));
    }

    // Sort descending by score, deduplicate by type (coverage diversity)
    candidates.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

    let mut result = Vec::new();
    let mut covered_types: Vec<Type> = Vec::new();

    for (mid, _) in candidates {
        if result.len() >= want { break; }
        if let Some(mdata) = get_move(&mid) {
            if !covered_types.contains(&mdata.move_type) {
                covered_types.push(mdata.move_type);
                result.push(mid);
            }
        }
    }
    result
}
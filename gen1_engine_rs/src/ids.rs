/// ids.rs — Compact integer IDs for moves and species.
///
/// All String-based move/species names are converted to u16 at the
/// JSON boundary (main.rs deserialization). Internal engine code uses
/// u16 everywhere — zero heap allocation in the hot search path.
///
/// ID 0 is always the sentinel "no move" / "unknown species".
/// Special pseudo-moves (recharge, struggle, __sleep_frz__) get reserved IDs.

// ─── Reserved pseudo-move IDs ─────────────────────────────────────────────────
pub const MOVE_NONE:       u16 = 0;
pub const MOVE_STRUGGLE:   u16 = 1;
pub const MOVE_RECHARGE:   u16 = 2;
pub const MOVE_SLEEP_FRZ:  u16 = 3;
pub const MOVE_ID_OFFSET:  u16 = 4;   // real moves start here

pub const SPECIES_UNKNOWN: u16 = 0;

// ─── Move name → ID ───────────────────────────────────────────────────────────

/// Linear scan — called only once per move at JSON parse time, not in search.
pub fn move_to_id(name: &str) -> u16 {
    // Normalise: lowercase, strip spaces/hyphens — matches poke-env's move ID format
    let norm: String = name.trim().to_lowercase()
        .chars().filter(|c| c.is_alphanumeric()).collect();
    match norm.as_str() {
        ""             => MOVE_NONE,
        "struggle"     => MOVE_STRUGGLE,
        "recharge"     => MOVE_RECHARGE,
        "__sleepfrz__" => MOVE_SLEEP_FRZ,
        other => MOVE_NAMES.iter().position(|&n| n == other)
            .map(|i| i as u16 + MOVE_ID_OFFSET)
            .unwrap_or(MOVE_NONE),
    }
}

pub fn id_to_move(id: u16) -> &'static str {
    match id {
        MOVE_NONE      => "",
        MOVE_STRUGGLE  => "struggle",
        MOVE_RECHARGE  => "recharge",
        MOVE_SLEEP_FRZ => "__sleep_frz__",
        n => {
            let idx = (n - MOVE_ID_OFFSET) as usize;
            MOVE_NAMES.get(idx).copied().unwrap_or("")
        }
    }
}

pub fn species_to_id(name: &str) -> u16 {
    // Normalise: lowercase, strip spaces/hyphens/dots — handles poke-env variants
    // like "mr-mime" → "mrmime", "farfetch'd" → "farfetchd"
    let norm: String = name.trim().to_lowercase()
        .chars().filter(|c| c.is_alphanumeric()).collect();
    if norm.is_empty() { return SPECIES_UNKNOWN; }
    SPECIES_NAMES.iter().position(|&n| n == norm.as_str())
        .map(|i| i as u16 + 1)
        .unwrap_or(SPECIES_UNKNOWN)
}

pub fn id_to_species(id: u16) -> &'static str {
    if id == SPECIES_UNKNOWN { return ""; }
    let idx = (id - 1) as usize;
    SPECIES_NAMES.get(idx).copied().unwrap_or("")
}

// ─── Boost stat slot indices (matches eval.rs key strings) ────────────────────
// [0]=atk [1]=def [2]=spc [3]=spe [4]=acc [5]=eva
pub const BOOST_ATK: usize = 0;
pub const BOOST_DEF: usize = 1;
pub const BOOST_SPC: usize = 2;
pub const BOOST_SPE: usize = 3;
pub const BOOST_ACC: usize = 4;
pub const BOOST_EVA: usize = 5;

pub fn boost_key_to_idx(key: &str) -> Option<usize> {
    match key {
        "atk" => Some(BOOST_ATK),
        "def" => Some(BOOST_DEF),
        "spc" => Some(BOOST_SPC),
        "spe" => Some(BOOST_SPE),
        "acc" => Some(BOOST_ACC),
        "eva" => Some(BOOST_EVA),
        _     => None,
    }
}

// ─── Static name tables ───────────────────────────────────────────────────────

/// All real Gen 1 move names used by the engine, inference templates, or Python bridge.
/// Order matters — index + MOVE_ID_OFFSET = the u16 ID.
/// Names are lowercase, alphanumeric-only (same normalisation as move_to_id).
pub static MOVE_NAMES: &[&str] = &[
    // Normal
    "tackle","bodyslam","doubleedge","hyperbeam","slash","scratch","cut","pound",
    "quickattack","bide","bind","wrap","stomp","strength","megapunch","megakick",
    "headbutt","takedown","lick","dreameater","nightshade","rage","mimic",
    "metronome","selfdestruct","explosion","swordsdance","growl","tailwhip",
    "screech","leer","smokescreen","stringshot","harden","minimize","softboiled",
    "rest","recover","doubleslap","cometpunch","triattack","sharpen","defensecurl",
    // Fire
    "ember","fireblast","flamethrower","firespin","firepunch",
    // Water
    "surf","waterfall","hydropump","clamp","crabhammer","bubble","bubblebeam",
    "watergun","withdraw",
    // Ice
    "blizzard","icebeam","icepunch","aurorabeam","mist",
    // Electric
    "thunderbolt","thunder","thundershock","thunderpunch","thunderwave",
    "agility","flash",
    // Grass
    "razorleaf","solarbeam","vinewhip","leechseed","absorb","megadrain",
    "petaldance","spore","sleeppowder","stunspore","poisonpowder",
    // Psychic
    "psychic","psybeam","confusion","hypnosis","teleport","reflect","barrier",
    "amnesia","kinesis","meditate","psywave","lightscreen","seismictoss",
    // Fighting
    "karatechop","lowkick","jumpkick","highjumpkick","submission","focusenergy",
    "counter","doublekick","rollingkick","superpower",
    // Poison
    "acid","sludge","poisonsting","twineedle","pinmissile","toxic","poisongas",
    // Ground
    "earthquake","dig","fissure","sandattack","bonemerang","boneclub",
    // Flying
    "fly","wingattack","peck","drillpeck","gust","skyattack",
    // Rock
    "rockslide","rockthrow",
    // Bug
    "leechlife","signalbeam",
    // Ghost
    "confuseray","spite","lick","nightshade",
    // Dragon
    "dragonrage","dragonite",
    // Status / misc
    "substitute","disable","haze","whirlwind","roar","transform","conversion",
    "acidarmor","encore","glare","lovelykiss","sing","supersonic",
];

/// All 151 Gen 1 species names.
pub static SPECIES_NAMES: &[&str] = &[
    "bulbasaur","ivysaur","venusaur","charmander","charmeleon","charizard",
    "squirtle","wartortle","blastoise","caterpie","metapod","butterfree",
    "weedle","kakuna","beedrill","pidgey","pidgeotto","pidgeot",
    "rattata","raticate","spearow","fearow","ekans","arbok",
    "pikachu","raichu","sandshrew","sandslash","nidoranf","nidorina",
    "nidoqueen","nidoranm","nidorino","nidoking","clefairy","clefable",
    "vulpix","ninetales","jigglypuff","wigglytuff","zubat","golbat",
    "oddish","gloom","vileplume","paras","parasect","venonat","venomoth",
    "diglett","dugtrio","meowth","persian","psyduck","golduck",
    "mankey","primeape","growlithe","arcanine","poliwag","poliwhirl",
    "poliwrath","abra","kadabra","alakazam","machop","machoke","machamp",
    "bellsprout","weepinbell","victreebel","tentacool","tentacruel",
    "geodude","graveler","golem","ponyta","rapidash","slowpoke","slowbro",
    "magnemite","magneton","farfetchd","doduo","dodrio","seel","dewgong",
    "grimer","muk","shellder","cloyster","gastly","haunter","gengar",
    "onix","drowzee","hypno","krabby","kingler","voltorb","electrode",
    "exeggcute","exeggutor","cubone","marowak","hitmonlee","hitmonchan",
    "lickitung","koffing","weezing","rhyhorn","rhydon","chansey",
    "tangela","kangaskhan","horsea","seadra","goldeen","seaking",
    "staryu","starmie","mrmime","scyther","jynx","electabuzz","magmar",
    "pinsir","tauros","magikarp","gyarados","lapras","ditto","eevee",
    "vaporeon","jolteon","flareon","porygon","omanyte","omastar",
    "kabuto","kabutops","aerodactyl","snorlax","articuno","zapdos",
    "moltres","dratini","dragonair","dragonite","mewtwo","mew",
];
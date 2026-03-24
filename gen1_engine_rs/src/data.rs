/// data.rs — Gen 1 static tables
/// Mirrors gen1_data.py exactly so the Rust engine and Python share the same truth.

// ─── Type system ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Type {
    Normal, Fire, Water, Electric, Grass, Ice,
    Fighting, Poison, Ground, Flying, Psychic,
    Bug, Rock, Ghost, Dragon,
}

impl Type {
    pub fn is_special(self) -> bool {
        matches!(self, Type::Fire | Type::Water | Type::Electric |
                       Type::Grass | Type::Ice | Type::Psychic | Type::Dragon)
    }
}

/// Gen 1 type chart.  Returns effectiveness multiplier * 10 to stay integer-safe.
/// 0=immune, 5=0.5x, 10=1x, 20=2x.
pub fn type_effectiveness(attack: Type, def1: Type, def2: Option<Type>) -> f64 {
    let e1 = raw_eff(attack, def1);
    let e2 = def2.map(|t| raw_eff(attack, t)).unwrap_or(10);
    // Gen 1 bug: Ghost → Psychic is 0x despite "should" be 2x
    (e1 as f64 / 10.0) * (e2 as f64 / 10.0)
}

fn raw_eff(atk: Type, def: Type) -> u8 {
    use Type::*;
    match (atk, def) {
        // Normal
        (Normal, Rock) => 5,
        (Normal, Ghost) => 0,
        // Fire
        (Fire, Fire) | (Fire, Water) | (Fire, Rock) | (Fire, Dragon) => 5,
        (Fire, Grass) | (Fire, Ice) | (Fire, Bug) => 20,
        // Water
        (Water, Water) | (Water, Grass) | (Water, Dragon) => 5,
        (Water, Fire) | (Water, Rock) | (Water, Ground) => 20,
        // Electric
        (Electric, Electric) | (Electric, Grass) | (Electric, Dragon) => 5,
        (Electric, Ground) => 0,
        (Electric, Water) | (Electric, Flying) => 20,
        // Grass
        (Grass, Fire) | (Grass, Grass) | (Grass, Poison) | (Grass, Flying) |
        (Grass, Bug) | (Grass, Dragon) => 5,
        (Grass, Water) | (Grass, Ground) | (Grass, Rock) => 20,
        // Ice
        (Ice, Water) | (Ice, Ice) => 5,
        (Ice, Grass) | (Ice, Ground) | (Ice, Flying) | (Ice, Dragon) => 20,
        // Fighting
        (Fighting, Poison) | (Fighting, Bug) | (Fighting, Flying) |
        (Fighting, Psychic) => 5,
        (Fighting, Ghost) => 0,
        (Fighting, Normal) | (Fighting, Ice) | (Fighting, Rock) => 20,
        // Poison
        (Poison, Ground) | (Poison, Rock) | (Poison, Ghost) => 5,
        (Poison, Bug) | (Poison, Grass) => 20,   // Gen 1: Poison 2x vs Bug
        // Ground
        (Ground, Grass) | (Ground, Bug) => 5,
        (Ground, Electric) => 0,
        (Ground, Fire) | (Ground, Poison) | (Ground, Rock) => 20,
        // Flying
        (Flying, Electric) | (Flying, Rock) => 5,
        (Flying, Grass) | (Flying, Fighting) | (Flying, Bug) => 20,
        // Psychic
        (Psychic, Psychic) => 5,
        (Psychic, Ghost) => 0,  // Gen 1 bug: Ghost immune to Psychic
        (Psychic, Fighting) | (Psychic, Poison) => 20,
        // Bug
        (Bug, Fire) | (Bug, Flying) | (Bug, Fighting) => 5,
        (Bug, Grass) | (Bug, Poison) | (Bug, Psychic) => 20,  // Gen 1: Bug 2x vs Poison
        // Rock
        (Rock, Fighting) | (Rock, Ground) => 5,
        (Rock, Fire) | (Rock, Ice) | (Rock, Flying) | (Rock, Bug) => 20,
        // Ghost — only affects Ghost type in Gen 1 (Normal/Fighting=immune, Ghost=0x)
        (Ghost, Normal) | (Ghost, Psychic) => 0,  // Gen 1 bug
        (Ghost, Ghost) => 20,
        // Dragon
        (Dragon, Dragon) => 20,
        _ => 10,
    }
}

// ─── Pokémon base stats ───────────────────────────────────────────────────────
// [HP, Atk, Def, Spc, Spe, type1, type2]

#[derive(Clone, Debug)]
pub struct PokeStats {
    pub hp:  u16, pub atk: u16, pub def: u16,
    pub spc: u16, pub spe: u16,
    pub t1: Type, pub t2: Option<Type>,
}

/// O(n) name lookup — used only at JSON parse time, never in the search hot path.
pub fn get_pokemon(name: &str) -> Option<PokeStats> {
    let key = name.trim();
    POKEMON_TABLE.iter().find(|(k, _)| *k == key).map(|(_, v)| v.clone())
}

/// O(1) lookup by species ID — use this everywhere inside the search.
/// species_id is the same u16 used in BattlePoke.species (from ids.rs).
/// Returns None only for SPECIES_UNKNOWN (0).
#[allow(dead_code)]
pub fn get_pokemon_by_id(species_id: u16) -> Option<&'static PokeStats> {
    if species_id == 0 { return None; }
    let idx = (species_id - 1) as usize;
    POKEMON_TABLE_BY_ID.get(idx).map(|(_, ps)| ps)
}

/// Pre-computed battle stats at L100/DV15/maxStatExp — stored per species by ID.
/// Indexed by (species_id - 1). Avoids all formula work during search.
#[derive(Clone, Debug)]
pub struct BattleStats {
    pub hp:  u16,
    pub atk: u16,
    pub def: u16,
    pub spc: u16,
    pub spe: u16,
    pub t1:  Type,
    pub t2:  Option<Type>,
}

/// Table of pre-computed BattleStats indexed by (species_id - 1).
/// Built once at startup via lazy_static.
use std::sync::OnceLock;
static BATTLE_STATS_TABLE: OnceLock<Vec<BattleStats>> = OnceLock::new();

pub fn get_battle_stats(species_id: u16) -> Option<&'static BattleStats> {
    if species_id == 0 { return None; }
    let table = BATTLE_STATS_TABLE.get_or_init(|| {
        POKEMON_TABLE_BY_ID.iter().map(|(_, ps)| BattleStats {
            hp:  calc_stat_hp(ps.hp),
            atk: calc_stat(ps.atk),
            def: calc_stat(ps.def),
            spc: calc_stat(ps.spc),
            spe: calc_stat(ps.spe),
            t1:  ps.t1,
            t2:  ps.t2,
        }).collect()
    });
    table.get((species_id - 1) as usize)
}

/// POKEMON_TABLE_BY_ID: same data as POKEMON_TABLE but in species-ID order
/// (matching SPECIES_NAMES in ids.rs). Index 0 = bulbasaur (species_id=1).
pub static POKEMON_TABLE_BY_ID: &[(&str, PokeStats)] = POKEMON_TABLE;

macro_rules! poke {
    ($hp:expr,$atk:expr,$def:expr,$spc:expr,$spe:expr,$t1:ident) => {
        PokeStats { hp:$hp, atk:$atk, def:$def, spc:$spc, spe:$spe,
                    t1: Type::$t1, t2: None }
    };
    ($hp:expr,$atk:expr,$def:expr,$spc:expr,$spe:expr,$t1:ident,$t2:ident) => {
        PokeStats { hp:$hp, atk:$atk, def:$def, spc:$spc, spe:$spe,
                    t1: Type::$t1, t2: Some(Type::$t2) }
    };
}

// All 151 Gen 1 Pokémon (matches gen1_data.py exactly)
pub static POKEMON_TABLE: &[(&str, PokeStats)] = &[
    ("bulbasaur",  poke!(45,49,49,65,45,Grass,Poison)),
    ("ivysaur",    poke!(60,62,63,80,60,Grass,Poison)),
    ("venusaur",   poke!(80,82,83,100,80,Grass,Poison)),
    ("charmander", poke!(39,52,43,60,65,Fire)),
    ("charmeleon", poke!(58,64,58,80,80,Fire)),
    ("charizard",  poke!(78,84,78,85,100,Fire,Flying)),
    ("squirtle",   poke!(44,48,65,50,43,Water)),
    ("wartortle",  poke!(59,63,80,65,58,Water)),
    ("blastoise",  poke!(79,83,100,85,78,Water)),
    ("caterpie",   poke!(45,30,35,20,45,Bug)),
    ("metapod",    poke!(50,20,55,25,30,Bug)),
    ("butterfree", poke!(60,45,50,90,70,Bug,Flying)),
    ("weedle",     poke!(40,35,30,20,50,Bug,Poison)),
    ("kakuna",     poke!(45,25,50,25,35,Bug,Poison)),
    ("beedrill",   poke!(65,90,40,45,75,Bug,Poison)),
    ("pidgey",     poke!(40,45,40,35,56,Normal,Flying)),
    ("pidgeotto",  poke!(63,60,55,50,71,Normal,Flying)),
    ("pidgeot",    poke!(83,80,75,70,91,Normal,Flying)),
    ("rattata",    poke!(30,56,35,25,72,Normal)),
    ("raticate",   poke!(55,81,60,50,97,Normal)),
    ("spearow",    poke!(40,60,30,31,70,Normal,Flying)),
    ("fearow",     poke!(65,90,65,61,100,Normal,Flying)),
    ("ekans",      poke!(35,60,44,40,55,Poison)),
    ("arbok",      poke!(60,95,69,65,80,Poison)),
    ("pikachu",    poke!(35,55,30,50,90,Electric)),
    ("raichu",     poke!(60,90,55,90,110,Electric)),
    ("sandshrew",  poke!(50,75,85,30,40,Ground)),
    ("sandslash",  poke!(75,100,110,45,65,Ground)),
    ("nidoranf",   poke!(55,47,52,40,41,Poison)),
    ("nidorina",   poke!(70,62,67,55,56,Poison)),
    ("nidoqueen",  poke!(90,92,87,75,76,Poison,Ground)),
    ("nidoranm",   poke!(46,57,40,40,50,Poison)),
    ("nidorino",   poke!(61,72,57,55,65,Poison)),
    ("nidoking",   poke!(81,102,77,85,85,Poison,Ground)),
    ("clefairy",   poke!(70,45,48,60,35,Normal)),
    ("clefable",   poke!(95,70,73,85,60,Normal)),
    ("vulpix",     poke!(38,41,40,65,65,Fire)),
    ("ninetales",  poke!(73,76,75,100,100,Fire)),
    ("jigglypuff", poke!(115,45,20,25,20,Normal)),
    ("wigglytuff", poke!(140,70,45,50,45,Normal)),
    ("zubat",      poke!(40,45,35,40,55,Poison,Flying)),
    ("golbat",     poke!(75,80,70,75,90,Poison,Flying)),
    ("oddish",     poke!(45,50,55,75,30,Grass,Poison)),
    ("gloom",      poke!(60,65,70,85,40,Grass,Poison)),
    ("vileplume",  poke!(75,80,85,100,50,Grass,Poison)),
    ("paras",      poke!(35,70,55,55,25,Bug,Grass)),
    ("parasect",   poke!(60,95,80,80,30,Bug,Grass)),
    ("venonat",    poke!(60,55,50,40,45,Bug,Poison)),
    ("venomoth",   poke!(70,65,60,90,90,Bug,Poison)),
    ("diglett",    poke!(10,55,25,45,95,Ground)),
    ("dugtrio",    poke!(35,100,50,70,120,Ground)),
    ("meowth",     poke!(40,45,35,40,90,Normal)),
    ("persian",    poke!(65,70,60,65,115,Normal)),
    ("psyduck",    poke!(50,52,48,50,55,Water)),
    ("golduck",    poke!(80,82,78,80,85,Water)),
    ("mankey",     poke!(40,80,35,35,70,Fighting)),
    ("primeape",   poke!(65,105,60,60,95,Fighting)),
    ("growlithe",  poke!(55,70,45,70,60,Fire)),
    ("arcanine",   poke!(90,110,80,100,95,Fire)),
    ("poliwag",    poke!(40,50,40,40,90,Water)),
    ("poliwhirl",  poke!(65,65,65,50,90,Water)),
    ("poliwrath",  poke!(90,95,95,70,70,Water,Fighting)),
    ("abra",       poke!(25,20,15,105,90,Psychic)),
    ("kadabra",    poke!(40,35,30,120,105,Psychic)),
    ("alakazam",   poke!(55,50,45,135,120,Psychic)),
    ("machop",     poke!(70,80,50,35,35,Fighting)),
    ("machoke",    poke!(80,100,70,50,45,Fighting)),
    ("machamp",    poke!(90,130,80,65,55,Fighting)),
    ("bellsprout", poke!(50,75,35,70,40,Grass,Poison)),
    ("weepinbell", poke!(65,90,50,85,55,Grass,Poison)),
    ("victreebel", poke!(80,105,65,100,70,Grass,Poison)),
    ("tentacool",  poke!(40,40,35,100,70,Water,Poison)),
    ("tentacruel", poke!(80,70,65,120,100,Water,Poison)),
    ("geodude",    poke!(40,80,100,30,20,Rock,Ground)),
    ("graveler",   poke!(55,95,115,45,35,Rock,Ground)),
    ("golem",      poke!(80,110,130,55,45,Rock,Ground)),
    ("ponyta",     poke!(50,85,55,65,90,Fire)),
    ("rapidash",   poke!(65,100,70,80,105,Fire)),
    ("slowpoke",   poke!(90,65,65,40,15,Water,Psychic)),
    ("slowbro",    poke!(95,75,110,80,30,Water,Psychic)),
    ("magnemite",  poke!(25,35,70,95,45,Electric)),
    ("magneton",   poke!(50,60,95,120,70,Electric)),
    ("farfetchd",  poke!(52,65,55,58,60,Normal,Flying)),
    ("doduo",      poke!(35,85,45,35,75,Normal,Flying)),
    ("dodrio",     poke!(60,110,70,60,110,Normal,Flying)),
    ("seel",       poke!(65,45,55,45,45,Water)),
    ("dewgong",    poke!(90,70,80,95,70,Water,Ice)),
    ("grimer",     poke!(80,80,50,40,25,Poison)),
    ("muk",        poke!(105,105,75,65,50,Poison)),
    ("shellder",   poke!(30,65,100,45,40,Water)),
    ("cloyster",   poke!(50,95,180,85,70,Water,Ice)),
    ("gastly",     poke!(30,35,30,100,80,Ghost,Poison)),
    ("haunter",    poke!(45,50,45,115,95,Ghost,Poison)),
    ("gengar",     poke!(60,65,60,130,110,Ghost,Poison)),
    ("onix",       poke!(35,45,160,30,70,Rock,Ground)),
    ("drowzee",    poke!(60,48,45,90,42,Psychic)),
    ("hypno",      poke!(85,73,70,115,67,Psychic)),
    ("krabby",     poke!(30,105,90,25,50,Water)),
    ("kingler",    poke!(55,130,115,50,75,Water)),
    ("voltorb",    poke!(40,30,50,55,100,Electric)),
    ("electrode",  poke!(60,50,70,80,140,Electric)),
    ("exeggcute",  poke!(60,40,80,60,40,Grass,Psychic)),
    ("exeggutor",  poke!(95,95,85,125,55,Grass,Psychic)),
    ("cubone",     poke!(50,50,95,40,35,Ground)),
    ("marowak",    poke!(60,80,110,50,45,Ground)),
    ("hitmonlee",  poke!(50,120,53,35,87,Fighting)),
    ("hitmonchan", poke!(50,105,79,35,76,Fighting)),
    ("lickitung",  poke!(90,55,75,60,30,Normal)),
    ("koffing",    poke!(40,65,95,60,35,Poison)),
    ("weezing",    poke!(65,90,120,85,60,Poison)),
    ("rhyhorn",    poke!(80,85,95,30,25,Ground,Rock)),
    ("rhydon",     poke!(105,130,120,45,40,Ground,Rock)),
    ("chansey",    poke!(250,5,5,105,50,Normal)),
    ("tangela",    poke!(65,55,115,100,60,Grass)),
    ("kangaskhan", poke!(105,95,80,40,90,Normal)),
    ("horsea",     poke!(30,40,70,70,60,Water)),
    ("seadra",     poke!(55,65,95,95,85,Water)),
    ("goldeen",    poke!(45,67,60,50,63,Water)),
    ("seaking",    poke!(80,92,65,80,68,Water)),
    ("staryu",     poke!(30,45,55,70,85,Water)),
    ("starmie",    poke!(60,75,85,100,115,Water,Psychic)),
    ("mrmime",     poke!(40,45,65,100,90,Psychic)),
    ("scyther",    poke!(70,110,80,55,105,Bug,Flying)),
    ("jynx",       poke!(65,50,35,95,95,Ice,Psychic)),
    ("electabuzz", poke!(65,83,57,85,105,Electric)),
    ("magmar",     poke!(65,95,57,85,93,Fire)),
    ("pinsir",     poke!(65,125,100,55,85,Bug)),
    ("tauros",     poke!(75,100,95,70,110,Normal)),
    ("magikarp",   poke!(20,10,55,20,80,Water)),
    ("gyarados",   poke!(95,125,79,100,81,Water,Flying)),
    ("lapras",     poke!(130,85,80,95,60,Water,Ice)),
    ("ditto",      poke!(48,48,48,48,48,Normal)),
    ("eevee",      poke!(55,55,50,65,55,Normal)),
    ("vaporeon",   poke!(130,65,60,110,65,Water)),
    ("jolteon",    poke!(65,65,60,110,130,Electric)),
    ("flareon",    poke!(65,130,60,110,65,Fire)),
    ("porygon",    poke!(65,60,70,75,40,Normal)),
    ("omanyte",    poke!(35,40,100,90,35,Rock,Water)),
    ("omastar",    poke!(70,60,125,115,55,Rock,Water)),
    ("kabuto",     poke!(30,80,90,55,55,Rock,Water)),
    ("kabutops",   poke!(60,115,105,70,80,Rock,Water)),
    ("aerodactyl", poke!(80,105,65,60,130,Rock,Flying)),
    ("snorlax",    poke!(160,110,65,65,30,Normal)),
    ("articuno",   poke!(90,85,100,125,85,Ice,Flying)),
    ("zapdos",     poke!(90,90,85,125,100,Electric,Flying)),
    ("moltres",    poke!(90,100,90,125,90,Fire,Flying)),
    ("dratini",    poke!(41,64,45,50,50,Dragon)),
    ("dragonair",  poke!(61,84,65,70,70,Dragon)),
    ("dragonite",  poke!(91,134,95,100,80,Dragon,Flying)),
    ("mewtwo",     poke!(106,110,90,154,130,Psychic)),
    ("mew",        poke!(100,100,100,100,100,Psychic)),
];

// ─── Level 100 stat formula ───────────────────────────────────────────────────
// Formula (non-HP): ((Base + DV)*2 + ceil(sqrt(StatExp))/4) * Lvl/100 + 5
// With 15 DVs and max StatExp (65535): ceil(sqrt(65535))=256, /4=64
// Simplified: (Base+15)*2 + 64) * 1 + 5  at L100
// HP: same but + Lvl + 10 instead of + 5 → + 110

pub fn calc_stat_hp(base: u16) -> u16 {
    // (base + 15) * 2 + 64 + 110  = base*2 + 30 + 64 + 110 = base*2 + 204
    base * 2 + 204
}

pub fn calc_stat(base: u16) -> u16 {
    // (base + 15) * 2 + 64 + 5  = base*2 + 30 + 64 + 5 = base*2 + 99
    base * 2 + 99
}

// ─── Move table ───────────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct MoveData {
    pub bp: u16,
    pub move_type: Type,
    pub min_hits: u8,
    pub max_hits: u8,
}

pub fn get_move(id: &str) -> Option<&'static MoveData> {
    let key = id.trim();
    MOVE_TABLE.iter().find(|(k, _)| *k == key).map(|(_, v)| v)
}

pub fn is_fixed_damage(id: &str) -> bool {
    matches!(id, "seismictoss" | "nightshade" | "psywave" | "sonicboom" | "dragonrage")
}
pub fn is_ohko(id: &str) -> bool {
    matches!(id, "guillotine" | "horndrill" | "fissure")
}
pub fn is_trapping(id: &str) -> bool {
    matches!(id, "wrap" | "bind" | "clamp" | "firespin")
}

macro_rules! mv {
    ($bp:expr, $t:ident) => {
        MoveData { bp: $bp, move_type: Type::$t, min_hits: 1, max_hits: 1 }
    };
    ($bp:expr, $t:ident, $lo:expr, $hi:expr) => {
        MoveData { bp: $bp, move_type: Type::$t, min_hits: $lo, max_hits: $hi }
    };
}

// Canonical move table (all Gen 1 damaging moves that matter for the engine)
pub static MOVE_TABLE: &[(&str, MoveData)] = &[
    // Normal
    ("tackle",        mv!(35, Normal)),
    ("bodyslam",      mv!(85, Normal)),
    ("doubleedge",    mv!(100, Normal)),
    ("hyperbeam",     mv!(150, Normal)),
    ("slash",         mv!(70, Normal)),
    ("scratch",       mv!(40, Normal)),
    ("cut",           mv!(50, Normal)),
    ("pound",         mv!(40, Normal)),
    ("quickattack",   mv!(40, Normal)),
    ("watergun",      mv!(40, Water)),
    ("bide",          mv!(0, Normal)),
    ("bind",          mv!(15, Normal, 2, 5)),
    ("wrap",          mv!(15, Normal, 2, 5)),
    ("stomp",         mv!(65, Normal)),
    ("strength",      mv!(80, Normal)),
    ("megapunch",     mv!(80, Normal)),
    ("megakick",      mv!(120, Normal)),
    ("headbutt",      mv!(70, Normal)),
    ("takedown",      mv!(90, Normal)),
    ("lick",          mv!(20, Ghost)),
    ("dreameater",    mv!(100, Psychic)),
    ("nightshade",    mv!(0, Ghost)),   // fixed damage
    // Fire
    ("ember",         mv!(40, Fire)),
    ("fireblast",     mv!(120, Fire)),
    ("flamethrower",  mv!(95, Fire)),
    ("firespin",      mv!(15, Fire, 2, 5)),
    ("firepunch",     mv!(75, Fire)),
    // Water
    ("surf",          mv!(95, Water)),
    ("blizzard",      mv!(120, Ice)),
    ("icebeam",       mv!(95, Ice)),
    ("icepunch",      mv!(75, Ice)),
    ("waterfall",     mv!(80, Water)),
    ("hydropump",     mv!(120, Water)),
    ("clamp",         mv!(35, Water, 2, 5)),
    ("crabhammer",    mv!(90, Water)),
    ("bubble",        mv!(20, Water)),
    ("bubblebeam",    mv!(65, Water)),
    // Electric
    ("thunderbolt",   mv!(95, Electric)),
    ("thunder",       mv!(120, Electric)),
    ("thundershock",  mv!(40, Electric)),
    ("thunderpunch",  mv!(75, Electric)),
    ("thunderwave",   mv!(0, Electric)),
    // Grass
    ("razorleaf",     mv!(55, Grass)),
    ("solarbeam",     mv!(120, Grass)),
    ("vinewhip",      mv!(35, Grass)),
    ("leechseed",     mv!(0, Grass)),
    ("absorb",        mv!(20, Grass)),
    ("megadrain",     mv!(40, Grass)),
    ("petaldance",    mv!(70, Grass, 2, 3)),
    ("spore",         mv!(0, Grass)),
    ("sleeppowder",   mv!(0, Grass)),
    ("stunspore",     mv!(0, Grass)),
    ("poisonpowder",  mv!(0, Grass)),
    // Psychic
    ("psychic",       mv!(90, Psychic)),
    ("psybeam",       mv!(65, Psychic)),
    ("confusion",     mv!(50, Psychic)),
    ("hypnosis",      mv!(0, Psychic)),
    ("teleport",      mv!(0, Psychic)),
    ("reflect",       mv!(0, Psychic)),
    ("barrier",       mv!(0, Psychic)),
    ("amnesia",       mv!(0, Psychic)),
    ("kinesis",       mv!(0, Psychic)),
    ("meditate",      mv!(0, Psychic)),
    // Fighting
    ("karatechop",    mv!(50, Normal)),  // Gen 1: Normal type
    ("lowkick",       mv!(50, Fighting)),
    ("jumpkick",      mv!(70, Fighting)),
    ("highjumpkick",  mv!(85, Fighting)),
    ("submission",    mv!(80, Fighting)),
    ("seismictoss",   mv!(0, Fighting)),  // fixed
    ("focusenergy",   mv!(0, Normal)),
    ("counter",       mv!(0, Fighting)),
    ("superpower",    mv!(120, Fighting)),
    // Poison
    ("acid",          mv!(40, Poison)),
    ("sludge",        mv!(65, Poison)),
    ("poisonsting",   mv!(15, Poison)),
    ("twineedle",     mv!(25, Bug, 2, 2)),
    ("poisongasp",    mv!(0, Poison)),
    ("pinmissile",    mv!(14, Bug, 2, 5)),
    // Ground
    ("earthquake",    mv!(100, Ground)),
    ("dig",           mv!(100, Ground)),
    ("fissure",       mv!(0, Ground)),    // OHKO
    ("mudslap",       mv!(20, Ground)),
    ("sandattack",    mv!(0, Ground)),
    // Flying
    ("fly",           mv!(70, Flying)),
    ("wingattack",    mv!(35, Flying)),
    ("gust",          mv!(40, Normal)),   // Gen 1: Normal type
    ("peck",          mv!(35, Flying)),
    ("drillpeck",     mv!(80, Flying)),
    ("skyattack",     mv!(140, Flying)),
    ("whirlwind",     mv!(0, Normal)),
    ("agility",       mv!(0, Psychic)),
    // Bug
    ("stringshot",    mv!(0, Bug)),
    ("leechlife",     mv!(20, Bug)),
    ("xscissor",      mv!(80, Bug)),
    ("bugbite",       mv!(60, Bug)),
    // Rock
    ("rockslide",     mv!(75, Rock)),
    ("rockthrow",     mv!(50, Rock)),
    ("guillotine",    mv!(0, Normal)),    // OHKO
    ("horndrill",     mv!(0, Normal)),    // OHKO
    // Ghost (and misc)
    ("nightshade2",   mv!(0, Ghost)),
    ("confuseray",    mv!(0, Ghost)),
    // Dragon
    ("dragonrage",    mv!(0, Dragon)),    // fixed 40
    ("dragonbreath",  mv!(60, Dragon)),
    // Self-destruct family
    ("explosion",     mv!(250, Normal)),
    ("selfdestruct",  mv!(200, Normal)),
    // Multi-hit misc
    ("doubleslap",    mv!(15, Normal, 2, 5)),
    ("cometpunch",    mv!(18, Normal, 2, 5)),
    ("furyattack",    mv!(15, Normal, 2, 5)),
    ("furyswipes",    mv!(18, Normal, 2, 5)),
    ("spikecannon",   mv!(20, Normal, 2, 5)),
    ("barrage",       mv!(15, Normal, 2, 5)),
    ("eggbomb",       mv!(100, Normal)),
    ("doublekick",    mv!(30, Fighting, 2, 2)),
    ("bonemerang",    mv!(50, Ground, 2, 2)),
    ("waterfall2",    mv!(80, Water)),
    // Misc status
    ("growl",         mv!(0, Normal)),
    ("tail whip",     mv!(0, Normal)),
    ("smokescreen",   mv!(0, Normal)),
    ("leer",          mv!(0, Normal)),
    ("minimize",      mv!(0, Normal)),
    ("curse",         mv!(0, Normal)),
    ("disable",       mv!(0, Normal)),
    ("mimic",         mv!(0, Normal)),
    ("metronome",     mv!(0, Normal)),
    ("protect",       mv!(0, Normal)),
    ("detect",        mv!(0, Normal)),
    ("endure",        mv!(0, Normal)),
    ("lightscreen",   mv!(0, Psychic)),
    ("mist",          mv!(0, Ice)),
    ("psywave",       mv!(0, Psychic)),  // fixed
    ("sonicboom",     mv!(0, Normal)),   // fixed
    ("superfang",     mv!(0, Normal)),   // fixed
    ("substitute",    mv!(0, Normal)),
    ("swords dance",  mv!(0, Normal)),
    ("swordsdance",   mv!(0, Normal)),
    ("harden",        mv!(0, Normal)),
    ("withdraw",      mv!(0, Water)),
    ("defense curl",  mv!(0, Normal)),
    ("defensecurl",   mv!(0, Normal)),
    ("doubleteam",    mv!(0, Normal)),
    ("transform",     mv!(0, Normal)),
    ("recover",       mv!(0, Normal)),
    ("softboiled",    mv!(0, Normal)),
    ("rest",          mv!(0, Psychic)),
    ("splash",        mv!(0, Normal)),
    ("struggle",      mv!(50, Normal)),
];

// ─── Stat-stage lookup ────────────────────────────────────────────────────────
pub fn apply_stage(stat: i32, stage: i8) -> i32 {
    let s = stage.max(-6).min(6);
    let (num, den): (i32, i32) = match s {
        -6 => (2,8), -5 => (2,7), -4 => (2,6),
        -3 => (2,5), -2 => (2,4), -1 => (2,3),
         0 => (2,2),
         1 => (3,2),  2 => (4,2),  3 => (5,2),
         4 => (6,2),  5 => (7,2),  6 => (8,2),
        _  => (2,2),
    };
    (stat * num / den).max(1).min(999)
}
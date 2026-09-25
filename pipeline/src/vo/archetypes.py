"""Archetypes: which approved voice every NPC speaks with, and the race style guide that designs them.

An Archetype is one humanoid race and gender (e.g. `orc_f`) or one Creature Family and gender (e.g.
`great_beasts_m`). NPC race labels come from the Forever display tables (`npcs.race`); every label must map here,
or `vo prepare` stops and names it. Families only get a gender variant that NPCs actually have.

Mapping decisions for labels that are neither a playable race nor an obvious creature type:
- Gilnean (Winter Reveler, a human in holiday dress): Human.
- High Order Skyborne (the only NPC is Lady Sylvanas Windrunner, "Banshee Queen", whose display carries a Forever
  label): Spirits, with the banshees.
- Questobjects (Cracked/Faint Necrotic Crystal: quest objects whose text is narration, "you discover a crystal..."):
  the Narrator, not an Archetype.
- Golem (Phalanx, Vendor-Tron 1000) and Gorilla (A-Me 01, a robot) follow the Creature Family grouping as decided in
  #10 (Ancients & Earth, Wild Folk); a robot is a Candidate problem for the style guide, not a family of its own.

The style guide (STYLE) holds each Archetype's VoxCPM2 voice-design description and the anchor line its Candidates
read. The bake-off (#10) winners are kept verbatim: human_m, human_f, orc_f, troll_m, troll_f (round 2), dwarf_f
(round 3's new description). orc_m keeps round 2's description; its Candidates come from round 5 (the orc chain on
the anchor). The rest are drafted in the same style: body, age, accent, pitch and texture, attitude, and a
"clearly X, never Y" guard.
Notes from "Regenerate with note" are appended to the description (see prepare).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

NARRATOR = "narrator"  # a race label mapped here speaks with the Narrator (not an Archetype)

# Playable-style humanoid races: one Archetype per race and gender. Label -> id prefix.
RACES: dict[str, str] = {
    "Human": "human", "Dwarf": "dwarf", "Night Elf": "nightelf", "Orc": "orc", "Tauren": "tauren",
    "Undead": "undead", "Goblin": "goblin", "Troll": "troll", "Gnome": "gnome", "Blood Elf": "bloodelf",
    "Ogre": "ogre",
}

# Creature Families: id -> (label, member race labels).
FAMILIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "great_beasts": ("Great Beasts", ("Dragon", "Dreadlord", "Demon", "Giant")),
    "ancients": ("Ancients & Earth", ("Ancient", "Keeper", "Stonekeeper", "Elemental", "Golem", "Forceofnature")),
    "spirits": ("Spirits", ("Banshee", "Ghost", "Wisp", "Lostone", "High Order Skyborne")),
    "wild_folk": ("Wild Folk", ("Furbolg", "Gnoll", "Quilboar", "Troglodyte", "Centaur", "Satyr", "Gorilla")),
    "fey": ("Fey", ("Dryad",)),
    "naga": ("Naga", ("Naga",)),
    "undead_constructs": ("Undead Constructs", ("Fleshgolem", "Skeleton")),
    "oddities": ("Oddities", ("Chicken", "Snowman", "Cupid", "Bogbeast", "Wolf", "Salamander", "Nightmare")),
}

# Other labels folded into a humanoid race or the Narrator (see the module docstring).
ALIASES: dict[str, str] = {"Gilnean": "Human", "Questobjects": NARRATOR}

GENDERS = {"male": "m", "female": "f"}


class UnmappedRace(ValueError):
    """An NPC race label with no Archetype mapping: add it to RACES, FAMILIES or ALIASES."""


def group(race: str) -> str:
    """The Archetype group (humanoid race prefix or family id) for an NPC race label, or NARRATOR."""
    race = ALIASES.get(race, race)
    if race == NARRATOR:
        return NARRATOR
    if race in RACES:
        return RACES[race]
    for fid, (_, members) in FAMILIES.items():
        if race in members:
            return fid
    raise UnmappedRace(race)


def archetype_id(race: str, gender: str) -> str:
    """`orc_f`, `great_beasts_m`, or NARRATOR. Gender must be male or female (extraction resolves it)."""
    g = group(race)
    if g == NARRATOR:
        return NARRATOR
    if gender not in GENDERS:
        raise UnmappedRace(f"{race} with gender {gender!r}")
    return f"{g}_{GENDERS[gender]}"


def label(aid: str) -> str:
    g, _, s = aid.rpartition("_")
    name = FAMILIES[g][0] if g in FAMILIES else next(k for k, v in RACES.items() if v == g)
    return f"{name}, {'male' if s == 'm' else 'female'}"


def kind(aid: str) -> str:
    return "family" if aid.rpartition("_")[0] in FAMILIES else "race"


@dataclass
class Archetype:
    id: str
    label: str
    kind: str                                            # race | family
    gender: str                                          # male | female
    races: dict[str, int] = field(default_factory=dict)  # "Race gender" label -> NPCs
    npcs: int = 0
    lines: int = 0


def derive(conn: sqlite3.Connection) -> list[Archetype]:
    """Every Archetype the pipeline DB needs: one per humanoid race and gender with NPCs, one per Creature Family
    and gender with NPCs. Raises UnmappedRace naming every label that has no mapping."""
    rows = conn.execute(
        "SELECT n.race, n.gender, COUNT(DISTINCT n.id), COUNT(l.id) FROM npcs n"
        " LEFT JOIN lines l ON l.npc_id = n.id WHERE n.race IS NOT NULL GROUP BY n.race, n.gender").fetchall()
    out: dict[str, Archetype] = {}
    bad = []
    for race, gender, npcs, lines in rows:
        try:
            aid = archetype_id(race, gender)
        except UnmappedRace:
            bad.append(f"{race} ({gender})")
            continue
        if aid == NARRATOR:
            continue
        a = out.setdefault(aid, Archetype(aid, label(aid), kind(aid), gender))
        a.races[f"{race} {gender}"] = npcs
        a.npcs += npcs
        a.lines += lines
    if bad:
        raise UnmappedRace("no Archetype for race label(s): " + ", ".join(sorted(bad))
                           + " (add them to vo.archetypes RACES, FAMILIES or ALIASES)")
    return sorted(out.values(), key=lambda a: (a.kind != "race", -a.lines, a.id))


def npc_archetypes(conn: sqlite3.Connection) -> dict[int, str]:
    """{npc id: Archetype id (or NARRATOR)} for every NPC with a mapped race."""
    out = {}
    for npc, race, gender in conn.execute("SELECT id, race, gender FROM npcs WHERE race IS NOT NULL"):
        try:
            out[npc] = archetype_id(race, gender)
        except UnmappedRace:
            continue
    return out


# --- race style guide ------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Style:
    description: str   # VoxCPM2 voice-design instruction
    anchor_text: str   # the line every Candidate reads; lines continue from it
    mode: str = "cont"  # "cont": anchor + transcript as prompt; "ultimate": also the anchor as reference
    effect_chain: str | None = None  # a name in vo.effects.CHAINS applied to every line; off (see vo.effects)
    anchor_chain: str | None = None  # a name in vo.effects.CHAINS applied once to each Candidate anchor
    design: bool = True  # False: no fresh voice-design Candidates unless asked (seeded from the bake-off instead)


A_ORC = ("You there. Raiders in the eastern ravine have been stealing our wolves. Go, bring me the heads of their "
         "leaders, and the Horde will remember your name.")
A_TROLL = ("Ah, mon, de spirits told me you be comin'. Dem raptors in de jungle be stealin' our eggs again. You go "
           "bring dem back, and I make it worth your while.")
A_HUMAN = ("Traveller, a moment of your time. Bandits have been raiding the farms along the river road, and the guard "
           "is stretched too thin. If you could drive them off, the whole village would be in your debt.")
A_DWARF = ("Well, don't just stand there gawkin'. The forge is hot, the ale is cold, and there's work in these "
           "mountains for anyone with a strong back and a stout heart.")
A_NIGHTELF = ("Be welcome, traveller, and walk softly. The forest has grown restless since the moon last waned, and "
              "something dark stirs beneath the old trees. Elune guide you, if you would help us.")
A_TAUREN = ("Walk with the Earth Mother, young one. The kodo herds have grown thin, and the wind carries the scent of "
            "danger from the south. Help us, and the spirits of the plains will honour you.")
A_UNDEAD = ("Another one crawls out of the grave, eager to serve. Good. The Dark Lady has work for the living and the "
            "dead alike, and the Scourge will not destroy itself.")
A_GOBLIN = ("Hey, hey, friend! You look like someone who knows a good deal when they see one. Time is money, so let's "
            "skip the chit-chat. Bring me those parts, and I'll make it worth your while.")
A_GNOME = ("Oh, splendid, a volunteer! The gyro-stabilisers have gone completely haywire again, and my assistant is "
           "still stuck in the ceiling. Could you fetch me some spare cogs? Quickly now!")
A_BLOODELF = ("You may approach. The magisters have no time for idle visitors, but perhaps you can be of use. Our "
              "enemies grow bolder in the Dead Scar, and Quel'Thalas does not forget those who serve her.")
A_OGRE = ("You! Little one! Me hungry, and dem gnolls take all da meat. You go smash dem, bring meat back, and maybe "
          "me not smash you. Now go, go!")
A_BEAST = ("Mortal. You stand before a power older than your kingdoms, older than your gods. Speak your purpose "
           "quickly, for my patience is not endless, and I have crushed far greater than you.")
A_ANCIENT = ("Long have I stood here, young one. The roots run deep, and the stone remembers all. The balance of this "
             "land has been broken. Listen, and I will tell you what must be done.")
A_SPIRIT = ("Can you hear me? I am bound to this place, long after death. The living rarely listen, but you... you "
            "might set things right, and let me finally rest.")
A_WILD = ("You not from here. The tribe does not trust outsiders. But the sickness spreads through the forest, and "
          "our elders are dying. Help us, and you are friend to our people.")
A_FEY = ("Oh! A visitor, how lovely! The glade has been so quiet since the satyrs came. Would you help me drive them "
         "away? The flowers would be ever so grateful, and so would I.")
A_NAGA = ("Ssso, a surface dweller comes to the water's edge. How brave. The tide brings many secrets, and I might "
          "share one... if you do something for me first.")
A_CONSTRUCT = ("Me guard. Me big. Master say nobody pass. You want pass? You bring what master want. Then maybe you "
               "pass. Maybe.")
A_ODDITY = ("Well hello there! Don't be alarmed, I know I look a little strange. It's a long story, really. But I "
            "could use a hand, and you look like just the helpful sort!")

STYLE: dict[str, Style] = {
    # --- humanoids: bake-off winners (#10) ---
    "human_m": Style(
        "A clear, steady adult male baritone with a neutral southern English accent. Earnest, kind and a little weary, "
        "like a guard captain or farmer in a medieval kingdom. Natural, grounded, unhurried.", A_HUMAN, "ultimate"),
    "human_f": Style(
        "A clear, warm young adult woman with a gentle southern English accent. Friendly, earnest and a little "
        "anxious, like a townswoman in a medieval city. Natural, expressive, mid pitch.", A_HUMAN, "cont"),
    # Round 2's description. Round 5 (#10): no designed anchor holds the growl, so orc_m's Candidates are the bake-off
    # anchors (vo.bakeoff_import, `vo prepare --import-bakeoff orc_m`) with the orc chain on the anchor.
    "orc_m": Style(
        "A huge, hulking male orc warrior with an extremely deep bass voice, very low pitch, thick gravel and a "
        "guttural growl in the throat, harsh vocal fry on every word. Slow, heavy, menacing and proud, biting off "
        "short forceful phrases like a battle-scarred warchief. Monstrous, not human.", A_ORC, "cont",  # "cont": as the round-5 winner was heard
        anchor_chain="orc", design=False),
    "orc_f": Style(
        "A powerful, muscular orc warrior woman with a low, husky, rough contralto voice, gravelly and throaty with a "
        "growl at the edges. Blunt, fierce and commanding, speaking in short hard phrases like a veteran of many "
        "battles. Deep for a woman, strong chest resonance.", A_ORC, "ultimate"),
    "troll_m": Style(
        "A tall, lanky male jungle troll witch doctor with a thick Caribbean Jamaican patois accent, a deep raspy "
        "croaking voice, lazy drawn-out vowels and a relaxed sing-song rhythm. Sly, mischievous and knowing, with a "
        "hint of a hiss on the s sounds.", A_TROLL, "cont"),
    "troll_f": Style(
        "A tall female jungle troll shaman with a strong Caribbean Jamaican accent, a smoky, raspy low voice, long "
        "drawn-out vowels and a lilting sing-song rhythm. Playful, mystical and a little dangerous, like a voodoo "
        "priestess by the fire.", A_TROLL, "ultimate"),
    "dwarf_f": Style(
        "A short, stout dwarf woman in her fifties, a blacksmith and brewer from the mountains, with a strong, broad "
        "Scottish accent: rolled r's, clipped consonants and a bouncing Highland lilt. Her voice is a low, warm, "
        "chesty alto with a gravelly edge from years at a smoky forge. Loud, hearty and cheerful, quick to laugh, "
        "blunt and motherly. Clearly a woman, clearly a dwarf, never posh.", A_DWARF, "cont"),
    # --- humanoids: drafted ---
    "dwarf_m": Style(
        "A short, barrel-chested dwarf man in his sixties, a miner and warrior from the mountain halls, with a thick, "
        "broad Scottish accent: rolled r's, clipped consonants and a gruff Highland burr. His voice is a deep, booming, "
        "gravelly baritone, rough from pipe smoke and forge fumes. Loud, hearty and proud, quick to laugh and quicker "
        "to argue. Clearly a dwarf, never posh.", A_DWARF),
    "nightelf_m": Style(
        "A tall, ancient night elf man, a sentinel of the moonlit forest, with a calm, deep, resonant baritone and a "
        "soft, lilting, faintly otherworldly accent. Slow, measured and formal, every word chosen with care. Serene "
        "and wise, with an undertone of old sorrow. Clearly an elf, never modern or casual.", A_NIGHTELF),
    "nightelf_f": Style(
        "A tall, graceful night elf woman, a priestess of the moon and a sentinel, with a clear, cool, mid-low voice, "
        "soft and slightly ethereal, and a gentle lilting accent. Measured, formal and dignified, calm with quiet "
        "strength and a hint of melancholy. Clearly an elf, never girlish or casual.", A_NIGHTELF),
    "tauren_m": Style(
        "A huge, gentle tauren bull, a shaman of the grassy plains, with a very deep, warm, rumbling bass voice and a "
        "slow, unhurried rhythm with long pauses. Earthy, wise and kind, like an old tribal elder speaking by the "
        "fire, with great chest resonance. Big and heavy, clearly not human-sized.", A_TAUREN),
    "tauren_f": Style(
        "A tall, strong tauren woman, a druid and healer of the plains, with a low, warm, resonant alto voice, slow "
        "and calm with a gentle rumble. Earthy, nurturing and wise, speaking with quiet strength and patience. Deep "
        "for a woman, clearly large and powerful, never frail.", A_TAUREN),
    "undead_m": Style(
        "A Forsaken undead man, a corpse given voice: dry, raspy, hollow and hoarse, as if his lungs are long dead, "
        "with a sneering, clipped English accent. Sardonic, bitter and sinister, with a cold, mocking edge and a rattle "
        "in his breath. Clearly dead, never warm or healthy.", A_UNDEAD),
    "undead_f": Style(
        "A Forsaken undead woman, a corpse given voice: dry, raspy and hollow, with a breathy rasp and a cold, clipped "
        "English accent. Sardonic, bitter and quietly menacing, with a mocking edge. Clearly dead, never warm or "
        "sweet.", A_UNDEAD),
    "goblin_m": Style(
        "A small, wiry goblin merchant with a high, nasal, fast-talking voice and a brash New York street accent. "
        "Greedy, excitable and pushy, a wheeling-and-dealing salesman who talks a mile a minute with a cackle in "
        "his throat. Clearly a goblin, never deep or calm.", A_GOBLIN),
    "goblin_f": Style(
        "A small, sharp goblin businesswoman with a high, nasal, rapid-fire voice and a brash New York street accent. "
        "Shrewd, greedy and impatient, a fast-talking trader with a cackling laugh. Clearly a goblin, never soft or "
        "slow.", A_GOBLIN),
    "gnome_m": Style(
        "A tiny gnome tinkerer with a high, bright, quick voice and a crisp English accent. Enthusiastic, excitable "
        "and a little scatterbrained, rattling off technical ideas at speed, cheerful and squeaky but clearly a grown "
        "man. Small and energetic, never deep.", A_GNOME),
    "gnome_f": Style(
        "A tiny gnome engineer woman with a high, bright, bubbly voice and a crisp English accent. Quick, clever and "
        "excitable, bursting with ideas, cheerful and chirpy but clearly a grown woman. Small and energetic, never "
        "deep.", A_GNOME),
    "bloodelf_m": Style(
        "A haughty blood elf nobleman of Quel'Thalas with a smooth, refined tenor-baritone and a polished, upper-class "
        "English accent. Elegant, precise and arrogant, faintly disdainful of everyone he speaks to, with a silky, "
        "theatrical flair. Clearly an aristocrat, never rough.", A_BLOODELF),
    "bloodelf_f": Style(
        "A proud blood elf noblewoman of Quel'Thalas with a smooth, cool, silky mid voice and a polished, upper-class "
        "English accent. Elegant, precise and imperious, a little condescending, with a refined, measured delivery. "
        "Clearly an aristocrat, never rough.", A_BLOODELF),
    "ogre_m": Style(
        "A huge, dim-witted ogre brute with a very deep, thick, booming voice, slurred and dopey, with a guttural "
        "rumble. Slow, simple and blunt, speaking in clumsy short phrases, easily excited and a little menacing. "
        "Enormous and stupid, clearly not human.", A_OGRE),
    # --- Creature Families: drafted ---
    "great_beasts_m": Style(
        "An immense, ancient dragon or demon lord with a colossal, deep, booming bass voice that resonates like a "
        "cavern. Slow, imperious and menacing, every word heavy with power, cold and supremely arrogant. Vast and "
        "inhuman, never small.", A_BEAST),
    "great_beasts_f": Style(
        "An ancient, powerful demoness with a deep, smoky, commanding female voice, sultry and cruel, with a resonant, "
        "unnatural edge. Imperious and menacing, savouring every word. Inhuman and dangerous, never sweet.", A_BEAST),
    "ancients_m": Style(
        "An ancient being of wood, stone and elemental power, like a living tree or a mountain given voice: an "
        "extremely deep, slow, creaking, rumbling bass, with long drawn-out words and grinding resonance. Patient, "
        "solemn and wise. Vast and ancient, clearly not human.", A_ANCIENT),
    "spirits_m": Style(
        "A ghostly spirit of a dead man: a hollow, breathy, distant male voice, soft and echoing, sorrowful and eerie, "
        "with drawn-out whispery words as if from beyond the grave. Clearly a ghost, never warm or lively.", A_SPIRIT),
    "spirits_f": Style(
        "A banshee, the spirit of a dead elven woman: a cold, hollow, ethereal female voice, breathy and haunting, with "
        "a mournful, wailing edge. Bitter and sorrowful, faintly echoing. Clearly a ghost, never warm.", A_SPIRIT),
    "wild_folk_m": Style(
        "A shaggy, bestial forest creature, a furbolg or gnoll of a wild tribe: a rough, gruff, growly low voice with "
        "simple, halting speech. Wary, tribal and earthy, animalistic but understandable. Clearly a beast-man, "
        "never polished.", A_WILD),
    "wild_folk_f": Style(
        "A fierce centaur woman of a wild tribe: a strong, rough, earthy low female voice with blunt, halting speech. "
        "Proud, wary and tribal, with a growl at the edges. Clearly a wild creature, never polished.", A_WILD),
    "fey_f": Style(
        "A dryad, a playful forest nymph: a light, bright, airy young female voice, musical and sing-song, sweet, "
        "cheerful and whimsical, with a little giggle in it. Magical and innocent, never harsh.", A_FEY),
    "naga_f": Style(
        "A naga sea witch: a sibilant, hissing female voice with long stretched s sounds, silky, cold and scheming, "
        "low and serpentine. Clearly a serpent, never warm.", A_NAGA),
    "undead_constructs_m": Style(
        "A hulking abomination, a flesh golem stitched from corpses: a thick, wet, gurgling, dull-witted deep male "
        "voice, slow and slurred, speaking in simple short words. Monstrous and stupid, clearly not human.",
        A_CONSTRUCT),
    "oddities_m": Style(
        "A quirky, whimsical creature with a bright, cheerful, slightly odd male voice, animated and playful, "
        "full of bounce and good humour, a little eccentric. Friendly and fun, never menacing.", A_ODDITY),
}


def style(aid: str) -> Style:
    """The style guide entry; an Archetype the guide doesn't know yet (e.g. a new gender variant from Capture) gets
    a generic draft from its label so `vo prepare` never stops for want of a description."""
    if aid in STYLE:
        return STYLE[aid]
    other = aid[:-1] + ("f" if aid.endswith("m") else "m")
    if other in STYLE:
        s = STYLE[other]
        who = "woman" if aid.endswith("f") else "man"
        return Style(f"The {who} of this kind: {s.description}", s.anchor_text, s.mode, s.effect_chain, s.anchor_chain)
    return Style(f"A {label(aid).lower()} voice with strong, distinctive character.", A_ODDITY)

"""Source Data: Forever's display tables, resolving a creature display ID to race, gender and model.

Rules (Build Spec, "Race and gender resolution"):
1. A display with CreatureDisplayInfoExtra (humanoid character model) gives race and sex directly.
2. Otherwise the CreatureModelData file path gives the race (`character/<race>/<sex>/…` or
   `creature/<family>/…`); gender is the display's own Gender, else the model family's default,
   else it is ambiguous and flagged.
"""
import csv
import re
from dataclasses import dataclass
from pathlib import Path

BUILD = "1.60.1.69977"
TABLES = ("CreatureDisplayInfo", "CreatureDisplayInfoExtra", "CreatureModelData", "ChrRaces")
DB2_URL = "https://wago.tools/db2/{table}/csv?build={build}"
LISTFILE_URL = "https://github.com/wowdev/wow-listfile/releases/latest/download/community-listfile.csv"

GENDERS = {0: "male", 1: "female"}
RACE_ALIAS = {"Gilnean": "Human"}  # ChrRaces variants that sound the same

# Creature model folders -> race. Folders not listed resolve to their own title-cased name.
FAMILY = [
    (r"^ogre", "Ogre"), (r"^naga", "Naga"), (r"^skeleton", "Skeleton"), (r"^troll", "Troll"),
    (r"^gnoll", "Gnoll"), (r"^kobold", "Kobold"), (r"^murloc", "Murloc"), (r"^harpy", "Harpy"),
    (r"^centaur", "Centaur"), (r"^quillboar", "Quilboar"), (r"^furbolg", "Furbolg"),
    (r"^satyr", "Satyr"), (r"^tauren", "Tauren"), (r"^goblin", "Goblin"), (r"^dwarf", "Dwarf"),
    (r"^gnome", "Gnome"), (r"^banshee", "Banshee"), (r"^ghost", "Ghost"), (r"^spirit", "Ghost"),
    (r"^(dragon|drake|dragonwhelp|dragonspawn|onyxia|chromaticdragon)", "Dragon"),
    (r"^(elemental|waterelemental|fireelemental|airelemental|earthelemental)", "Elemental"),
    (r"^(demon|felguard|doomguard|infernal|succubus|felhunter|voidwalker|imp$|eredar)", "Demon"),
    (r"^(zombie|ghoul|abomination|lich)", "Undead"), (r"^(ent|treant|ancient)", "Ancient"),
    (r"^(dryad)", "Dryad"), (r"^(keeperofthegrove)", "Keeper"), (r"^(tuskarr)", "Tuskarr"),
    (r"^(orc|felorc)", "Orc"), (r"^(human)", "Human"), (r"^(nightelf)", "Night Elf"),
    (r"^(highelf|bloodelf)", "Blood Elf"), (r"^(scourge|undead)", "Undead"),
    (r"^(giant|mountaingiant|seagiant)", "Giant"), (r"^(silithid|qiraji|anubisath)", "Silithid"),
    (r"^(trogg|troglodyte)", "Troglodyte"), (r"^(golem|harvestgolem|golemstone|golemharvest)", "Golem"),
]
# Model families whose voice defaults to one gender when the display itself doesn't say.
FAMILY_GENDER = {"Banshee": "female", "Harpy": "female", "Dryad": "female", "Naga": None}
FOLDER_GENDER = [(r"female$", "female"), (r"male$", "male")]


@dataclass(frozen=True)
class Display:
    race: str | None
    gender: str | None
    model: str | None       # model file path, or None when unknown
    how: str                # extra | character | creature | missing
    gender_default: bool = False  # gender came from the family default or a guess


def family(folder: str) -> str:
    for pattern, race in FAMILY:
        if re.search(pattern, folder):
            return race
    return folder.replace("_", " ").title()


def from_path(path: str, races_by_file: dict[str, str]) -> tuple[str | None, str | None]:
    """(race, gender-from-path) for a model file path."""
    parts = path.lower().split("/")
    if parts[0] == "character" and len(parts) > 2:
        race = races_by_file.get(parts[1]) or family(parts[1])
        return race, parts[2] if parts[2] in ("male", "female") else None
    if parts[0] == "creature" and len(parts) > 1:
        gender = next((g for p, g in FOLDER_GENDER if re.search(p, parts[1])), None)
        return family(parts[1]), gender
    return None, None


class Displays:
    """Display ID -> Display, loaded from wago.tools CSV exports and the community listfile."""

    def __init__(self, info: dict[int, dict], extra: dict[int, dict], models: dict[int, int],
                 races: dict[int, dict], paths: dict[int, str], server_gender: dict[int, int] | None = None):
        self.info, self.extra, self.models, self.paths = info, extra, models, paths
        self.race_names = {i: RACE_ALIAS.get(r["Name_lang"], r["Name_lang"]) for i, r in races.items()}
        self.races_by_file = {r["ClientFileString"].lower(): r["Name_lang"] for r in races.values()}
        self.server_gender = server_gender or {}
        self._cache: dict[int, Display] = {}

    @classmethod
    def load(cls, db2_dir: Path, listfile: Path, server_gender: dict[int, int] | None = None) -> "Displays":
        def rows(table):
            with open(db2_dir / f"{table}.csv", encoding="utf-8", newline="") as f:
                return {int(r["ID"]): r for r in csv.DictReader(f)}

        models = {i: int(r["FileDataID"]) for i, r in rows("CreatureModelData").items()}
        wanted = {str(v) for v in models.values()}
        paths = {}
        with open(listfile, encoding="utf-8") as f:
            for line in f:
                fdid, _, path = line.partition(";")
                if fdid in wanted:
                    paths[int(fdid)] = path.strip()
        return cls(rows("CreatureDisplayInfo"), rows("CreatureDisplayInfoExtra"), models,
                   rows("ChrRaces"), paths, server_gender)

    def get(self, display_id: int) -> Display:
        if display_id not in self._cache:
            self._cache[display_id] = self._resolve(display_id)
        return self._cache[display_id]

    def _resolve(self, display_id: int) -> Display:
        info = self.info.get(display_id)
        if info is None:
            return Display(None, None, None, "missing")
        path = self.paths.get(self.models.get(int(info["ModelID"]), -1))
        extra = self.extra.get(int(info["ExtendedDisplayInfoID"] or 0))
        if extra is not None:
            race = self.race_names.get(int(extra["DisplayRaceID"]))
            return Display(race, GENDERS.get(int(extra["DisplaySexID"])), path, "extra")
        if path is None:
            return Display(None, None, None, "missing")
        race, gender = from_path(path, self.races_by_file)
        how = "character" if path.lower().startswith("character/") else "creature"
        if gender is None:
            gender = GENDERS.get(int(info["Gender"])) or GENDERS.get(self.server_gender.get(display_id, 2))
        if gender is None and race is not None:
            default = FAMILY_GENDER.get(race, "male")
            return Display(race, default, path, how, gender_default=True)
        return Display(race, gender, path, how)

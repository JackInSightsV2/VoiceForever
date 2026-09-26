"""Game voice Candidates (ADR-0007): Archetype anchors built from WoW's own NPC voice clips.

Forever ships the original NPC voice sets: per race and gender, a few "kits" (standard, guard, official, ...), each
one voice actor saying greetings, farewells, vendor lines and "pissed" lines when clicked, as short Ogg clips under
`sound/creature/<race><gender><kit>npc/`. A few creature folders (bosses, ogres, dragons) hold speech too, mixed with
grunts: their clips pass a stricter transcript check (creature_spoken). `vo prepare --import-gamevoice` turns each distinct speaker into a Candidate next to the VoxCPM2-designed ones:

1. Kits: the community listfile's paths matching an NPC voice set (NPC_KIT) or a listed creature folder
   (CREATURE_KITS), grouped by folder, for the Archetypes vo.archetypes knows.
2. Clips: downloaded by FileDataID from wago.tools for Forever's build (a clip the build doesn't have is skipped and
   remembered), decoded, trimmed and resampled to RATE, then transcribed with the pipeline's Whisper. A clip whose
   transcript isn't speech (empty, a grunt, a Whisper hallucination, too many words for its length) is dropped.
3. Speakers: kits are merged when their voices are the same person (WavLM-SV centroids as alike as each kit is with
   itself, complete linkage; see SAME_SPEAKER), a clip far from its kit's voice (a heavy effect, another actor) is dropped (CLIP_FLOOR), and up to MAX_SPEAKERS
   speakers are kept per Archetype (NPC voice sets first, the standard kit first, then the most audio).
4. Anchors: per speaker, clean clips joined into an 8-15 s reference (per-clip loudness matched, short gaps, a
   greeting last since continuation copies the delivery of the anchor's end), with the clips' transcripts joined as
   the transcript VoxCPM2 continuation needs.

Everything is cached under data/gamevoice/ (clips, transcripts, the per-Archetype anchors and their plan), so a
second import reads the plan instead of downloading, transcribing and embedding again.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from functools import cache
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from vo import archetypes, basevoices, display

CASC_URL = "https://wago.tools/api/casc/{fdid}?version={build}"
RATE = 24000
MAX_SPEAKERS = basevoices.MAX
# Two kits are one person when their centroids are as alike as each kit is with itself (split_half). Measured on
# orc_m, human_f, troll_m (Forever 1.60.1): a kit's split-half 0.946-0.996; between kits 0.39-0.985, and only orc_m
# standard ~ guard (0.985) is inside its kits' own range. SAME_SPEAKER is the fallback for a kit too small to measure.
SAME_SPEAKER = 0.97
CLIP_FLOOR = 0.70     # a clip less similar than this to the rest of its kit is dropped
MIN_CLIP_S = 0.6
TARGET_S = 12.0       # stop adding clips once the anchor is this long ...
MAX_S = 15.0          # ... and never go past this
MIN_ANCHOR_S = 5.0    # a speaker with less clean speech than this gets no Candidate
GAP_S = 0.3
CLIP_RMS_DB = -20.0
PEAK = 0.95
MAX_WORDS_PER_S = 5.0  # Whisper hallucinating on a grunt writes more words than fit
MIN_KNOWN = 0.6        # creature clips: this share of the words must be English (creature_spoken) ...
MIN_WORDS_PER_S = 0.8  # ... and they must fill the clip (two words in an 8 s roar are a guess)
MIN_CLIPS = 2          # a speaker needs at least this many clean clips
SAMPLE_TRIES = 3

# NPC voice sets. Their clip kinds, in the order they're picked for an anchor (greetings are the calmest).
KINDS = ("greeting", "farewell", "vendor", "pissed")
NPC_KIT = (
    re.compile(r"^sound/creature/(?P<race>[a-z]+?)(?P<g>male|female)(?P<kit>[a-z]*)npc/[^/]+\.ogg$"),
    re.compile(r"^sound/creature/npc(?P<race>[a-z]+?)(?P<g>male|female)(?P<kit>[a-z]+)/[^/]+\.ogg$"),
    re.compile(r"^sound/creature/(?P<race>goblin|gilnean)(?P<kit>civ|guard|vendor|ven)(?P<g>m|f)/[^/]+\.ogg$"),
)
# Folder race prefix -> Archetype race prefix (vo.archetypes.RACES); Gilnean is Human there too.
RACE_PREFIX = {**{p: p for p in archetypes.RACES.values()}, "gilnean": "human", "scourge": "undead"}
GENDER = {"male": "m", "female": "f", "m": "m", "f": "f"}

# Creature folders whose clips may hold speech (bosses, talking creatures), with their Archetype. Every clip is tried;
# grunts and roars are dropped by the transcript check, so a folder without speech yields no speaker.
CREATURE_KITS: dict[str, str] = {
    "ogre": "ogre_m", "ogremage": "ogre_m", "ogreking": "ogre_m", "ogredumb": "ogre_m", "generic_ogre": "ogre_m",
    "nefarian": "great_beasts_m", "lordvictornefarius": "great_beasts_m", "doomlordkazzak": "great_beasts_m",
    "dreadlord": "great_beasts_m", "ragnaros": "ancients_m", "keeper_remulos": "ancients_m",
    "banshee": "spirits_f", "abomination": "undead_constructs_m", "fleshgolem": "undead_constructs_m",
    "gnoll": "wild_folk_m", "furbolg": "wild_folk_m", "quilboar": "wild_folk_m", "centaur": "wild_folk_m",
    "satyr": "wild_folk_m", "centaurfemale": "wild_folk_f", "dryad": "fey_f", "nagafemale": "naga_f",
    "naga_female": "naga_f",
}
# Creature clips that are never speech, by file name: combat grunts and roars.
CREATURE_SKIP = re.compile(r"attack|wound|death|roar|footstep|breath|spellcast|emote|swing|clickable|stand|run|walk")

# Transcripts that aren't speech: interjections a grunt is written as, and Whisper's stock hallucinations.
INTERJECTIONS = {"ah", "aah", "ahh", "argh", "arg", "ugh", "uh", "uhh", "um", "hmm", "hm", "hmph", "huh", "oh", "ooh",
                 "ow", "ouch", "grr", "rawr", "roar", "ha", "hah", "haha", "heh", "hehe", "ho", "hoo", "whoa", "wo", "yah",
                 "hyah", "hah", "gah", "bah", "pah", "eh", "er", "mm", "mmm", "rrr", "raa", "aaa", "aargh", "hrm", "hrmph"}
HALLUCINATIONS = {"thank you", "thanks for watching", "thank you for watching", "you", "bye", "subtitles by",
                  "please subscribe", "thank you very much", "so", "okay"}


class Unavailable(ValueError):
    """Some clips couldn't be downloaded this time (network): the Archetype's anchors aren't built or cached."""


@dataclass(frozen=True)
class Clip:
    fdid: int
    path: str
    kind: str  # greeting | farewell | vendor | pissed | other


@dataclass
class Kit:
    archetype: str
    folder: str
    key: str     # short name: the kit ("standard", "guard") or the creature folder
    source: str  # npc | creature
    clips: list[Clip] = field(default_factory=list)


@dataclass
class Anchor:
    """One speaker's Candidate anchor."""
    archetype: str
    key: str              # candidate id is "<archetype>/gv-<key>"
    label: str            # shown on the Approval page
    description: str
    path: str             # the anchor WAV (RATE, mono)
    transcript: str
    duration_s: float
    fdids: list[int]      # the clips in it, in order
    folders: list[str]    # the kits merged into this speaker

    @property
    def candidate(self) -> str:
        return f"{self.archetype}/gv-{self.key}"


# --- 1. kits from the listfile ---------------------------------------------------------------------------------------

def kind(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return next((k for k in KINDS if k in name), "other")


def npc_kit(path: str) -> tuple[str, str, str] | None:
    """(archetype id, folder, kit key) of an NPC voice set clip path, or None."""
    for rx in NPC_KIT:
        m = rx.match(path)
        if m is None:
            continue
        prefix = RACE_PREFIX.get(m["race"])
        if prefix is None:
            return None
        folder = path.split("/")[2]
        key = m["kit"] or "standard"
        key = {"ven": "vendor"}.get(key, key)
        if m["race"] == "gilnean":
            key = f"gilnean-{key}"
        return f"{prefix}_{GENDER[m['g']]}", folder, key
    return None


def read_listfile(path: Path) -> Iterable[tuple[int, str]]:
    """(FileDataID, lower-case path) of every sound/creature Ogg clip in the community listfile."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "sound/creature/" not in line.lower():
                continue
            fdid, _, p = line.strip().partition(";")
            p = p.lower()
            if p.endswith(".ogg") and fdid.isdigit():
                yield int(fdid), p


def find_kits(rows: Iterable[tuple[int, str]], creature: dict[str, str] | None = None) -> dict[str, list[Kit]]:
    """{archetype id: [Kit]} from listfile rows: every NPC voice set, and the listed creature folders. Only NPC voice
    set clips of a known kind are kept (every such clip is spoken); creature folders keep every clip. Kits are in
    folder order, their clips in path order."""
    creature = CREATURE_KITS if creature is None else creature
    kits: dict[tuple[str, str], Kit] = {}
    for fdid, p in rows:
        got = npc_kit(p)
        if got is not None:
            aid, folder, key = got
            k = kind(p)
            if k == "other":
                continue
            kit = kits.setdefault((aid, folder), Kit(aid, folder, key, "npc"))
        else:
            parts = p.split("/")
            if len(parts) != 4 or parts[2] not in creature or CREATURE_SKIP.search(parts[3]):
                continue
            aid, folder, k = creature[parts[2]], parts[2], kind(p)
            kit = kits.setdefault((aid, folder), Kit(aid, folder, folder, "creature"))
        kit.clips.append(Clip(fdid, p, k))
    out: dict[str, list[Kit]] = {}
    for (aid, _), kit in sorted(kits.items()):
        kit.clips.sort(key=lambda c: c.path)
        out.setdefault(aid, []).append(kit)
    for group_ in out.values():  # two sets with one kit name (goblinguardm, goblinmaleguardnpc): key by folder
        seen = [k.key for k in group_]
        for k in group_:
            if seen.count(k.key) > 1:
                k.key = k.folder
    return out


# --- 2. clips: download, decode, transcript check --------------------------------------------------------------------

def download(fdid: int, dest: Path, build: str = display.BUILD, timeout: float = 150.0) -> bool:
    """Fetch one file of Forever's build by FileDataID; False if the build doesn't have it (HTTP 404)."""
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(CASC_URL.format(fdid=fdid, build=build),
                                 headers={"User-Agent": "VoiceForever-pipeline"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (404, 400):
            return False
        raise
    if not data.startswith(b"OggS"):
        return False
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return True


def decode(path: Path, rate: int = RATE) -> np.ndarray:
    """An audio file as mono float32 at `rate`."""
    from math import gcd

    from pedalboard.io import AudioFile
    from scipy.signal import resample_poly
    with AudioFile(str(path)) as f:
        x, sr = f.read(f.frames), int(f.samplerate)
    x = np.asarray(x, dtype=np.float32).mean(axis=0)
    if sr != rate:
        g = gcd(sr, rate)
        x = resample_poly(x, rate // g, sr // g).astype(np.float32)
    return x


def spoken(text: str, duration_s: float) -> bool:
    """Whether a clip's transcript is speech worth continuing from: at least one real word (not an interjection a
    grunt is written as), not a stock Whisper hallucination, and no more words than fit in the clip."""
    words = re.findall(r"[a-z][a-z']*", text.lower().replace("’", "'"))
    if not words:
        return False
    if " ".join(words) in HALLUCINATIONS:
        return False
    if all(w.strip("'") in INTERJECTIONS or len(w.strip("'")) < 2 for w in words):
        return False
    return len(words) <= MAX_WORDS_PER_S * duration_s + 2


@cache
def lexicon_words() -> frozenset[str]:
    """English words (lower case) from misaki's pronunciation dictionaries (a pipeline dependency already)."""
    import importlib.resources

    words: set[str] = set()
    for name in ("us_gold.json", "us_silver.json"):
        words |= {w.lower() for w in json.loads(importlib.resources.files("misaki.data").joinpath(name).read_text())}
    return frozenset(words)


def _known(word: str, lexicon: frozenset[str]) -> bool:
    return any(w in lexicon for w in (word, word.rstrip("s"), word[:-2] if word.endswith(("es", "ed")) else word,
                                      word[:-3] if word.endswith("ing") else word))


def creature_spoken(text: str, duration_s: float, lexicon: frozenset[str] | None = None) -> bool:
    """`spoken`, held stricter for creature clips, which are mostly grunts that Whisper writes up as words: plain
    ASCII, no letter held three times ("Grrrr"), at least two real words and MIN_WORDS_PER_S, most (MIN_KNOWN) of them
    English (a lore name may be among them)."""
    if not spoken(text, duration_s) or not text.isascii() or "<|" in text or re.search(r"([a-z])\1\1", text.lower()):
        return False
    words = [w.strip("'") for w in re.findall(r"[a-z][a-z']*", text.lower())]
    words = [w for w in words if w not in INTERJECTIONS]
    if len(words) < max(2, MIN_WORDS_PER_S * duration_s):
        return False
    lexicon = lexicon_words() if lexicon is None else lexicon
    return sum(_known(w, lexicon) for w in words) >= MIN_KNOWN * len(words)


def clean_text(text: str) -> str:
    """A clip transcript as one sentence of the anchor transcript: trimmed, ending in punctuation."""
    t = " ".join(text.split())
    return t if not t or t[-1] in ".!?…" else t + "."


# --- 3. speakers -----------------------------------------------------------------------------------------------------

def _cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1))


def centroid(embs: list[np.ndarray]) -> np.ndarray:
    c = np.mean(np.stack(embs), axis=0)
    return c / (np.linalg.norm(c) or 1)


def keep_clips(embs: list[np.ndarray], floor: float = CLIP_FLOOR) -> list[bool]:
    """Which of a kit's clips sound like the kit: cosine to the centroid of the others at least `floor`. A kit of one
    or two clips keeps them all (no majority to compare with)."""
    if len(embs) < 3:
        return [True] * len(embs)
    return [_cos(e, centroid(embs[:i] + embs[i + 1:])) >= floor for i, e in enumerate(embs)]


def split_half(embs: list[np.ndarray]) -> float | None:
    """How alike one speaker's kit is with itself: cosine of the centroids of its odd and even clips (None under
    four clips). Two kits are one person when they're as alike as that."""
    return _cos(centroid(embs[0::2]), centroid(embs[1::2])) if len(embs) >= 4 else None


def group(names: list[str], embs: dict[str, np.ndarray], same: float | dict[str, float] = SAME_SPEAKER
          ) -> list[list[str]]:
    """Kits grouped into speakers: complete-linkage agglomeration of their centroids. Two kits a, b are one person
    when their cosine is at least min(same[a], same[b]) (a kit's own split-half similarity; SAME_SPEAKER for a kit
    not in `same`, or for all with a number); groups merge while every pair across them is, most alike first.
    Deterministic: groups keep input order."""
    floor = (lambda n: same) if isinstance(same, (int, float)) else (lambda n: same.get(n, SAME_SPEAKER))
    groups = [[n] for n in names]
    while True:
        best, pair = -1.0, None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                cross = [(_cos(embs[a], embs[b]), min(floor(a), floor(b))) for a in groups[i] for b in groups[j]]
                if all(s >= f for s, f in cross) and min(s for s, _ in cross) > best:
                    best, pair = min(s for s, _ in cross), (i, j)
        if pair is None:
            return groups
        i, j = pair
        groups[i] = groups[i] + groups[j]
        del groups[j]


def rank(kits: list[Kit], seconds: dict[str, float]) -> list[Kit]:
    """Kits in preference order: NPC voice sets before creature folders, the standard kit first, then most clean
    audio, then folder name."""
    return sorted(kits, key=lambda k: (k.source != "npc", k.key != "standard", -seconds.get(k.folder, 0.0), k.folder))


# --- 4. anchors ------------------------------------------------------------------------------------------------------

@dataclass
class Part:
    fdid: int
    kind: str
    samples: np.ndarray  # at RATE, trimmed
    text: str

    @property
    def seconds(self) -> float:
        return len(self.samples) / RATE


def _level(x: np.ndarray, db: float = CLIP_RMS_DB) -> np.ndarray:
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) or 1.0
    return (x * (10 ** (db / 20) / rms)).astype(np.float32)


def pick(parts: list[Part], target_s: float = TARGET_S, max_s: float = MAX_S, gap_s: float = GAP_S) -> list[Part]:
    """The clips of an anchor: by kind (greetings first, see KINDS), then clip order, until `target_s`, never past
    `max_s`. Returned in playing order: the reverse, so the anchor ends on a greeting."""
    order = {k: i for i, k in enumerate(KINDS)}
    chosen: list[Part] = []
    total = 0.0
    for p in sorted(parts, key=lambda p: (order.get(p.kind, len(KINDS)), p.fdid)):
        if total >= target_s:
            break
        add = p.seconds + (gap_s if chosen else 0.0)
        if total + add > max_s:
            continue
        chosen.append(p)
        total += add
    return chosen[::-1]


def assemble(parts: list[Part], gap_s: float = GAP_S) -> tuple[np.ndarray, str]:
    """Clips joined into one anchor (each at the same loudness, `gap_s` of silence between, peak-limited), and the
    joined transcript."""
    if not parts:
        raise ValueError("no clips to assemble")
    gap = np.zeros(int(gap_s * RATE), dtype=np.float32)
    out = []
    for i, p in enumerate(parts):
        if i:
            out.append(gap)
        out.append(_level(p.samples))
    x = np.concatenate(out)
    peak = float(np.max(np.abs(x))) or 1.0
    if peak > PEAK:
        x = x * (PEAK / peak)
    return x.astype(np.float32), " ".join(clean_text(p.text) for p in parts)


# --- the library: cached build of an Archetype's anchors -------------------------------------------------------------

Heard = Callable[[Path], str]                     # a WAV -> its transcript (the pipeline's Whisper)
Embed = Callable[[np.ndarray, int], np.ndarray]  # samples, rate -> speaker embedding


class Library:
    """The game voice anchors of each Archetype, built once and cached under `root` (data/gamevoice/):
    ogg/<fdid>.ogg the downloaded clips, wav/<fdid>.wav decoded and trimmed, clips.json per clip (missing from the
    build, duration, transcript, spoken), anchors/<archetype>/plan.json and <key>.wav the anchors."""

    def __init__(self, root: Path, listfile: Path, *, heard: Heard, embed: Embed | None = None,
                 fetch: Callable[[int, Path], bool] = download, kits: dict[str, list[Kit]] | None = None,
                 log: Callable[[str], None] = print):
        self.root, self.listfile, self._heard, self._embed, self._fetch = root, listfile, heard, embed, fetch
        self._kits, self.log = kits, log
        self._clips_path = root / "clips.json"
        self._clips: dict[str, dict] | None = None
        self.failed = 0  # downloads that failed (network) during this build: its plan isn't cached

    def kits(self) -> dict[str, list[Kit]]:
        if self._kits is None:
            if not self.listfile.exists():
                raise ValueError(f"community listfile not found at {self.listfile} (vo fetch downloads it)")
            self._kits = find_kits(read_listfile(self.listfile))
        return self._kits

    def _clip_db(self) -> dict[str, dict]:
        if self._clips is None:
            self._clips = json.loads(self._clips_path.read_text()) if self._clips_path.exists() else {}
        return self._clips

    def _save_clips(self) -> None:
        self._clips_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._clips_path.with_name(self._clips_path.name + ".part")
        tmp.write_text(json.dumps(self._clip_db(), indent=1, sort_keys=True))
        tmp.replace(self._clips_path)

    def part(self, clip: Clip, strict: bool = False) -> Part | None:
        """A clip downloaded, decoded, trimmed and transcribed (cached), or None if the build lacks it, it's too short,
        or its transcript isn't speech (`strict`: by creature_spoken)."""
        from vo import audio
        from vo.prepare import read_wav, write_wav

        info = self._clip_db().setdefault(str(clip.fdid), {"path": clip.path})
        if info.get("missing"):
            return None
        wav = self.root / "wav" / f"{clip.fdid}.wav"
        if not wav.exists():
            ogg = self.root / "ogg" / f"{clip.fdid}.ogg"
            try:
                got = self._fetch(clip.fdid, ogg)
            except OSError as e:  # a timeout or network error: not known missing, tried again next import
                self.log(f"  clip {clip.fdid} ({clip.path}): download failed, skipped this time: {e}")
                self.failed += 1
                return None
            if not got:
                info["missing"] = True
                return None
            try:
                x = audio.trim(decode(ogg), RATE, pad_s=0.05)
            except ValueError:  # silent
                info.update(duration_s=0.0, spoken=False)
                return None
            write_wav(wav, x, RATE)
        x, _ = read_wav(wav)
        info["duration_s"] = round(len(x) / RATE, 3)
        if len(x) / RATE < MIN_CLIP_S:
            info["spoken"] = False
            return None
        if "text" not in info:
            info["text"] = self._heard(wav).strip()
        info["spoken"] = (creature_spoken if strict else spoken)(info["text"], len(x) / RATE)
        return Part(clip.fdid, clip.kind, x, info["text"]) if info["spoken"] else None

    def plan_path(self, aid: str) -> Path:
        return self.root / "anchors" / aid / "plan.json"

    def anchors(self, aid: str) -> list[Anchor]:
        """The Archetype's game voice anchors (up to MAX_SPEAKERS), from the cached plan if its audio is all there,
        else built (downloads, transcripts, speaker embeddings) and cached."""
        p = self.plan_path(aid)
        if p.exists():
            got = [Anchor(**a) for a in json.loads(p.read_text())["anchors"]]
            if all(Path(a.path).exists() for a in got):
                return got
        return self._build(aid)

    def _build(self, aid: str) -> list[Anchor]:
        from vo.prepare import write_wav

        kits = self.kits().get(aid, [])
        parts: dict[str, list[Part]] = {}
        failed = self.failed
        for kit in kits:
            got = [x for c in kit.clips if (x := self.part(c, kit.source == "creature")) is not None]
            self._save_clips()
            if got:
                parts[kit.folder] = got
        if self.failed > failed:
            raise Unavailable(f"{aid}: {self.failed - failed} game voice clip download(s) failed; import again later")
        clips_n = {k.folder: len(k.clips) for k in kits}
        self.log(f"{aid}: {len(kits)} game voice kits, {sum(clips_n.values())} clips, "
                 f"{sum(len(v) for v in parts.values())} spoken clips in the build")
        if not parts:
            self._write_plan(aid, [], {})
            return []
        embed = self._embed or _default_embed()
        embs = {f: [embed(x.samples, RATE) for x in ps] for f, ps in parts.items()}
        for f in list(parts):  # drop clips far from their kit's voice
            keep = keep_clips(embs[f])
            dropped = [x.fdid for x, k in zip(parts[f], keep) if not k]
            if dropped:
                self.log(f"  {f}: dropped {len(dropped)} clip(s) unlike the rest of the kit: {dropped}")
            parts[f] = [x for x, k in zip(parts[f], keep) if k]
            embs[f] = [e for e, k in zip(embs[f], keep) if k]
        seconds = {f: sum(x.seconds for x in ps) for f, ps in parts.items()}
        ranked = [k for k in rank(kits, seconds) if parts.get(k.folder)]
        cents = {k.folder: centroid(embs[k.folder]) for k in ranked}
        halves = {f: h for f in cents if (h := split_half(embs[f])) is not None}
        by_folder = {k.folder: k for k in ranked}
        anchors: list[Anchor] = []
        similar = {}
        for g in group([k.folder for k in ranked], cents, halves):
            if len(anchors) >= MAX_SPEAKERS:
                break
            gk = [by_folder[f] for f in g]
            chosen = pick([x for f in g for x in parts[f]])
            samples, transcript = assemble(chosen)
            dur = len(samples) / RATE
            if dur < MIN_ANCHOR_S or len(chosen) < MIN_CLIPS:
                self.log(f"  {'_'.join(g)}: only {dur:.1f}s of clean speech in {len(chosen)} clip(s), no Candidate")
                continue
            key = "_".join(k.key for k in gk)
            wav = self.root / "anchors" / aid / f"{key}.wav"
            write_wav(wav, samples, RATE)
            src = "WoW NPC voice set" if gk[0].source == "npc" else "WoW creature voice"
            anchors.append(Anchor(
                aid, key, f"game voice: {', '.join(k.key for k in gk)}",
                f"{src} {', '.join(g)} (Forever build {display.BUILD}, {len(chosen)} clips, FileDataIDs "
                f"{', '.join(str(x.fdid) for x in chosen)})",
                str(wav.resolve()), transcript, round(dur, 3), [x.fdid for x in chosen], list(g)))
            if len(g) > 1:
                similar[key] = round(min(_cos(cents[a], cents[b]) for a in g for b in g if a < b), 3)
            self.log(f"  speaker {key}: {', '.join(g)}; {len(chosen)} clips, {dur:.1f}s")
        kit_sim = {f"{a} ~ {b}": round(_cos(cents[a], cents[b]), 3) for i, a in enumerate(cents)
                   for b in list(cents)[i + 1:]}
        self._write_plan(aid, anchors, {"kits": clips_n, "spoken": {f: len(v) for f, v in parts.items()},
                                        "merged_similarity": similar, "kit_similarity": kit_sim,
                                        "split_half": {f: round(h, 3) for f, h in halves.items()}})
        return anchors

    def _write_plan(self, aid: str, anchors: list[Anchor], stats: dict) -> None:
        p = self.plan_path(aid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"archetype": aid, "build": display.BUILD, **stats,
                                 "anchors": [asdict(a) for a in anchors]}, indent=1))


def _default_embed() -> Embed:
    from vo import speaker
    return speaker.embedder()

"""Text prep: turn a line's raw text into the text spoken by TTS (`lines.tts_text`).

Raw text keeps every token exactly as the Source Data has it. `prepare()` produces the
spoken form for one player gender, in this order:

1. `$G male:female;` resolves to the given player gender (see `genders()`).
2. Formatting that is never spoken is stripped: colour codes, hyperlinks, textures,
   `<...>` stage directions, `*emphasis*`, `[...]` brackets, slash commands.
3. `$B` becomes a pause (a line break; TTS renders it as a sentence break).
4. `$N`, `$R`, `$C` become a Neutral Address (ADR-0002) chosen from context.
5. Abbreviations and numbers are expanded ("10g" -> "ten gold", "7th" -> "seventh").
6. The Lexicon (lore-name pronunciations, #12) is applied, if one is given.
"""
import re
from collections.abc import Callable

Lexicon = Callable[[str], str]
"""Maps prepared text to text with lore names respelled for TTS. The Lexicon stage (#12) supplies one."""

PAUSE = "\n"

# --- $G player gender -------------------------------------------------------------------

GENDER = re.compile(r"\$[Gg]\s*([^:;]*):([^;]*);")


def genders(raw: str) -> list[str | None]:
    """Player genders a line is voiced for: [None] when it has no $G (one line serves both), else m and f."""
    return ["m", "f"] if GENDER.search(raw) else [None]


def resolve_gender(raw: str, gender: str | None) -> str:
    """Pick the male or female word of every $G token. With no gender the raw text must have no $G."""
    if gender is None:
        if GENDER.search(raw):
            raise ValueError("text has $G tokens; a player gender is required")
        return raw
    group = {"m": 1, "f": 2}[gender]
    return GENDER.sub(lambda m: m[group].strip(), raw)


# --- Formatting -------------------------------------------------------------------------

FORMATTING = [
    (re.compile(r"\|c[0-9a-fA-F]{8}"), ""),              # colour start |cffRRGGBB
    (re.compile(r"\|r"), ""),                             # colour end
    (re.compile(r"\|H[^|]*\|h(.*?)\|h"), r"\1"),          # hyperlink: keep the shown text
    (re.compile(r"\|T[^|]*\|t"), ""),                     # texture
    (re.compile(r"\|n"), PAUSE),                          # escaped newline
    (re.compile(r"<[^<>]*>"), " "),                       # <stage directions>, <snort>
    (re.compile(r"[<>]"), ""),                            # a stray bracket: "Well done, $N>."
    (re.compile(r"\[PH\]"), ""),                          # placeholder marker
    (re.compile(r"[\[\]*]"), ""),                         # [brackets], *Whir*, * bullets
    (re.compile(r"(^|(?<=\s)|(?<=\$[Bb]))/(?=[A-Za-z])"), ""),  # "/cheer at it" -> "cheer at it"
    (re.compile(r"(?<=[A-Za-z0-9])/(?=[A-Za-z0-9])"), " "),     # "OOX-17/TN" -> "OOX-17 TN"
]


def strip_formatting(text: str) -> str:
    for pattern, repl in FORMATTING:
        text = pattern.sub(repl, text)
    return text


# --- Neutral Address ($N, $R, $C) ---------------------------------------------------------

VOCATIVE_WORD = "friend"      # someone is being addressed: "Greetings, $c" -> "Greetings, friend"
NOUN_WORD = "adventurer"      # race or class used as a noun: "a $r like you" -> "an adventurer like you"
GREETINGS = {"hail", "greetings", "hello", "hey", "welcome", "farewell", "thanks", "ho"}

# A player token, with an optional possessive, plural or (for names) a drawled suffix: "$nah", "$rs".
PLAYER_TOKEN = re.compile(r"\$([NnRrCc])('s\b|s\b|[a-z]+)?")


def _is_vocative(text: str, start: int, end: int) -> bool:
    """Is the player being addressed ("Greetings, $c", "$C, listen") rather than described ("a brave $c")?"""
    before = text[:start].rstrip(" \t\"'")
    after = text[end:].lstrip(" \t")
    if not before or before[-1] in ",;:!?.\n":
        return True
    word = re.search(r"(\w+)$", before)[1].lower() if re.search(r"(\w+)$", before) else ""
    if word in GREETINGS:
        return True
    return word == "you" and (not after or after[0] in ",!?.;\n")  # "Thank you $c."


def neutral_address(text: str) -> str:
    """Replace $N/$R/$C with a Neutral Address that reads naturally in context."""
    def repl(m: re.Match) -> str:
        kind, suffix = m[1].lower(), m[2] or ""
        if kind == "n" or _is_vocative(text, m.start(), m.end()):
            word = VOCATIVE_WORD
        else:
            word = NOUN_WORD
        if suffix == "'s":
            return word + "'s"
        if suffix == "s" and kind != "n":
            return word + "s"
        return word  # "$nah" (a drawled name) is just the name; any other letters are dropped
    text = PLAYER_TOKEN.sub(repl, text)
    # "a $c" -> "an adventurer"
    text = re.sub(rf"\b([Aa]) (?={NOUN_WORD}\b)", r"\1n ", text)
    # "Greetings, $c $N" -> "Greetings, friend friend" -> "Greetings, friend"
    return re.sub(rf"\b({VOCATIVE_WORD}|{NOUN_WORD})(\s+\1)+\b", r"\1", text)


def _capitalise_sentences(text: str) -> str:
    """Capitalise the first letter of the text, of each pause and after sentence-ending punctuation
    (but not after an ellipsis or an initial: "but... maybe", "the K.E.F. and")."""
    return re.sub(r"(^|\n|(?<!\.\.)(?<!\b[A-Z])[.!?]\s+)([\"']?)([a-z])", lambda m: m[1] + m[2] + m[3].upper(), text)


# --- Numbers and abbreviations ----------------------------------------------------------------

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
SCALES = [(1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand"), (100, "hundred")]
ORDINAL = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
           "nine": "ninth", "twelve": "twelfth"}


def number_words(n: int) -> str:
    """0 -> "zero", 21 -> "twenty-one", 1234 -> "one thousand two hundred thirty-four"."""
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")
    for size, name in SCALES:
        if n >= size:
            rest = n % size
            return f"{number_words(n // size)} {name}" + (f" {number_words(rest)}" if rest else "")
    raise AssertionError(n)


def ordinal_words(n: int) -> str:
    """1 -> "first", 7 -> "seventh", 20 -> "twentieth", 109 -> "one hundred ninth"."""
    words = number_words(n)
    head, last = re.match(r"(.*?)([a-z]+)$", words).groups()
    if last in ORDINAL:
        return head + ORDINAL[last]
    if last.endswith("y"):
        return head + last[:-1] + "ieth"
    return head + last + "th"


MONEY = {"g": "gold", "s": "silver", "c": "copper"}
ABBREVIATIONS = [
    (r"\bMr\.", "Mister"), (r"\bMrs\.", "Missus"), (r"\bMs\.", "Miz"), (r"\bDr\.", "Doctor"),
    (r"\bLt\.", "Lieutenant"), (r"\bSgt\.", "Sergeant"), (r"\bCapt\.", "Captain"), (r"\bCpt\.", "Captain"),
    (r"\bGen\.", "General"), (r"\bJr\.", "Junior"), (r"\bSr\.", "Senior"), (r"\bMt\.", "Mount"),
    (r"\bSt\.(?= [A-Z])", "Saint"), (r"\bvs\.", "versus"), (r"\betc\.", "et cetera"),
    # "the Venture Co. goblins" -> "Company"; before a capital or the end, the full stop also ends a sentence.
    (r"\bCo\.(?=\s+[a-z0-9])", "Company"), (r"\bCo\.", "Company."),
]


def expand_abbreviations(text: str) -> str:
    for pattern, word in ABBREVIATIONS:
        text = re.sub(pattern, word, text)
    return text


def _int(digits: str) -> int:
    return int(digits.replace(",", ""))


NUMBER = r"\d{1,3}(?:,\d{3})+|\d+"


def expand_numbers(text: str) -> str:
    # Times: "2pm" -> "two P M"
    text = re.sub(r"\b(\d{1,2})\s?([ap])(?:\.m\.|m\b)", lambda m: f"{number_words(int(m[1]))} {m[2].upper()} M", text)
    # Letters glued to digits: "PX83" -> "PX 83", "21and" -> "21 and" (but not "7th", "10g")
    text = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", text)
    text = re.sub(r"(\d)(?!(?:st|nd|rd|th|[gsc])\b)(?=[A-Za-z])", r"\1 ", text)
    # Money: "10g 5s" -> "ten gold five silver"
    text = re.sub(rf"\b({NUMBER})\s?([gsc])\b", lambda m: f"{number_words(_int(m[1]))} {MONEY[m[2]]}", text)
    # Ordinals: "7th", "109th", "1st"
    text = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", lambda m: ordinal_words(int(m[1])), text)
    # Numbered: "Cog #5" -> "Cog number five"
    text = re.sub(r"#\s?(?=\d)", "number ", text)
    # Decimals: "15.9" -> "fifteen point nine"
    text = re.sub(r"\b(\d+)\.(\d+)\b",
                  lambda m: f"{number_words(int(m[1]))} point " + " ".join(ONES[int(d)] for d in m[2]), text)
    text = re.sub(rf"\b({NUMBER})\s?%", lambda m: f"{number_words(_int(m[1]))} percent", text)
    return re.sub(rf"\b({NUMBER})\b", lambda m: number_words(_int(m[1])), text)


# --- Tokens we can't voice -------------------------------------------------------------------

def strip_unknown_tokens(text: str) -> str:
    # "$2063w": a server-side counter (e.g. items donated so far); the value isn't known offline.
    text = re.sub(r"\$\d+[A-Za-z]?", "many", text)
    # Any other "$X...;" token (e.g. "$Tpunk;" in a test quest) or stray "$".
    text = re.sub(r"\$[A-Za-z][^$;\n]*;", "", text)
    return text.replace("$", "")


# --- Whitespace ---------------------------------------------------------------------------------

def tidy(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([,.!?;:])", r"\1", text)            # "Sir !" -> "Sir!"
    text = re.sub(r"\.{4,}", "...", text)                   # "but...<grin>...I'm" -> "but...I'm"
    text = re.sub(r"\s*\n\s*", PAUSE, text)                 # blank lines -> one pause
    text = re.sub(r"(?m)^[,.;:!?]+\s*", "", text)           # a pause can't start with punctuation
    return text.strip()


def prepare(raw: str, gender: str | None = None, lexicon: Lexicon | None = None) -> str:
    """The spoken text for one player gender of a raw line."""
    text = resolve_gender(raw, gender)
    text = strip_formatting(text)
    text = re.sub(r"\$[Bb]", PAUSE, text)
    text = neutral_address(text)
    text = strip_unknown_tokens(text)
    text = expand_abbreviations(text)
    text = expand_numbers(text)
    text = tidy(text)
    text = _capitalise_sentences(text)
    if lexicon is not None:
        text = lexicon(text)
    return text

import os
import random
import re
import sqlite3
from pathlib import Path

import pytest

from vo import cli, text
from vo.text import prepare


# --- $G player gender ---

def test_genders_without_token():
    assert text.genders("No tokens.") == [None]


def test_genders_with_token():
    assert text.genders("Hello $gsir:madam;.") == ["m", "f"]


@pytest.mark.parametrize("raw, male, female", [
    ("Hello, $Gsir:madam;.", "Hello, sir.", "Hello, madam."),
    ("Kind $g sir : lady;! Thanks.", "Kind sir! Thanks.", "Kind lady! Thanks."),
    ("A big $G brother : sister; to me.", "A big brother to me.", "A big sister to me."),
    ("Yes $g sir : ma'am;, twice $g sir : ma'am;.", "Yes sir, twice sir.", "Yes ma'am, twice ma'am."),
])
def test_gender_variants(raw, male, female):
    assert prepare(raw, "m") == male
    assert prepare(raw, "f") == female


def test_gender_token_needs_a_gender():
    with pytest.raises(ValueError):
        prepare("Hello $gsir:madam;.")


# --- Neutral Address ---

@pytest.mark.parametrize("raw, spoken", [
    ("Greetings, $c.", "Greetings, friend."),
    ("Greetings $c.", "Greetings friend."),
    ("Hail, $N.", "Hail, friend."),
    ("Well done, $n!", "Well done, friend!"),
    ("How goes the hunting, $N?", "How goes the hunting, friend?"),
    ("Thank you $c.", "Thank you friend."),
    ("You've done well, $C. This is a masterwork hilt.", "You've done well, friend. This is a masterwork hilt."),
    ("Your skill is impressive, $c; he is credited with kills.", "Your skill is impressive, friend; he is credited with kills."),
])
def test_vocative_is_friend(raw, spoken):
    assert prepare(raw) == spoken


@pytest.mark.parametrize("raw, spoken", [
    ("$N, you are brave.", "Friend, you are brave."),
    ("$C, listen closely.", "Friend, listen closely."),
    ("$c! I thought I would die.", "Friend! I thought I would die."),
    ("Ah. $c. How may I help?", "Ah. Friend. How may I help?"),
    ("Done.$B$B$n, go now.", "Done.\nFriend, go now."),
])
def test_capitalised_at_sentence_start(raw, spoken):
    assert prepare(raw) == spoken


@pytest.mark.parametrize("raw, spoken", [
    ("Luck to you, brave $c.", "Luck to you, brave adventurer."),
    ("Young $c, there is work.", "Young adventurer, there is work."),
    ("We need a $r like you.", "We need an adventurer like you."),
    ("A $C like you.", "An adventurer like you."),
    ("You're a $R of worth.", "You're an adventurer of worth."),
    ("You are a brave and cunning $c.", "You are a brave and cunning adventurer."),
])
def test_race_and_class_as_noun_is_adventurer(raw, spoken):
    assert prepare(raw) == spoken


@pytest.mark.parametrize("raw, spoken", [
    ("One more step down the $C's path.", "One more step down the adventurer's path."),
    ("Is this $N's sword?", "Is this friend's sword?"),
    ("Thanks to $rs like you.", "Thanks to adventurers like you."),
    ("Take it from me, $nama, you don't.", "Take it from me, friend, you don't."),
    ("You shouldn't feel bad, $nah.", "You shouldn't feel bad, friend."),
])
def test_possessives_plurals_and_drawls(raw, spoken):
    assert prepare(raw) == spoken


def test_repeated_address_collapses():
    assert prepare("Greetings, $c $N.") == "Greetings, friend."


# --- $B pause ---

def test_b_is_a_pause():
    assert prepare("First.$B$BSecond.$bThird.") == "First.\nSecond.\nThird."


def test_pause_trims_whitespace():
    assert prepare("  First.  $B$B  Second.  ") == "First.\nSecond."


# --- Formatting ---

@pytest.mark.parametrize("raw, spoken", [
    ("Bring me |cffff0000ten|r pelts.", "Bring me ten pelts."),
    ("Take |cFF00FF00this|r and |cff1eff00that|r.", "Take this and that."),
    ("A stray |r code.", "A stray code."),
    ("See |cff0070dd|Hitem:1234:0:0:0|h[Big Sword]|h|r here.", "See Big Sword here."),
    ("Icon |TInterface\\Icons\\INV_Misc:0|t here.", "Icon here."),
])
def test_colour_codes_and_links_stripped(raw, spoken):
    assert prepare(raw) == spoken


@pytest.mark.parametrize("raw, spoken", [
    ("I might know who did but...<grin>...I'm too hungry.", "I might know who did but...I'm too hungry."),
    ("<Sob> Oh please, don't look at me!", "Oh please, don't look at me!"),
    ("Deliver it.$B$B<You must not release your spirit.>", "Deliver it."),
    ("Here.$b$b<He trails off, grumbling...>$b$bWhat?", "Here.\nWhat?"),
    ("Well done, $N>. The deed.", "Well done, friend. The deed."),
])
def test_angle_bracket_text_stripped(raw, spoken):
    assert prepare(raw) == spoken


@pytest.mark.parametrize("raw, spoken", [
    ("*Whir* *Click*$B$B  I seek rare fish.", "Whir Click\nI seek rare fish."),
    ("Bring me:$B$B*30 Thorium Bars.", "Bring me:\nThirty Thorium Bars."),
    ("[PH] Description", "Description"),
    ("/cheer at it before you go.", "Cheer at it before you go."),
    ("When ready, /lay down here.", "When ready, lay down here."),
    ("Escort OOX-17/TN to the port.", "Escort OOX-seventeen TN to the port."),
])
def test_other_formatting_stripped(raw, spoken):
    assert prepare(raw) == spoken


# --- Unvoiceable tokens ---

def test_server_counter_becomes_many():
    assert prepare("We've collected $2063w pieces of leather.") == "We've collected many pieces of leather."


def test_unknown_token_dropped():
    assert prepare("$Tpunk;! Kill Kobold Vermin.") == "Kill Kobold Vermin."


# --- Numbers and abbreviations ---

@pytest.mark.parametrize("n, words", [
    (0, "zero"), (7, "seven"), (13, "thirteen"), (20, "twenty"), (42, "forty-two"), (100, "one hundred"),
    (109, "one hundred nine"), (1000, "one thousand"), (4000, "four thousand"),
    (500_000, "five hundred thousand"), (1_234_567, "one million two hundred thirty-four thousand five hundred sixty-seven"),
])
def test_number_words(n, words):
    assert text.number_words(n) == words


@pytest.mark.parametrize("n, words", [
    (1, "first"), (2, "second"), (3, "third"), (5, "fifth"), (7, "seventh"), (8, "eighth"), (9, "ninth"),
    (12, "twelfth"), (20, "twentieth"), (21, "twenty-first"), (109, "one hundred ninth"),
])
def test_ordinal_words(n, words):
    assert text.ordinal_words(n) == words


@pytest.mark.parametrize("raw, spoken", [
    ("Bring 5 Linen Cloth.", "Bring five Linen Cloth."),
    ("It costs 10g.", "It costs ten gold."),
    ("That's 1g 50s 3c.", "That's one gold fifty silver three copper."),
    ("Kill 10 Kobold Vermin.", "Kill ten Kobold Vermin."),
    ("On his 7th birthday.", "On his seventh birthday."),
    ("The 109th division.", "The one hundred ninth division."),
    ("The 1st, 2nd and 3rd.", "The first, second and third."),
    ("Around 4,000 years ago.", "Around four thousand years ago."),
    ("15.9 pounds!", "Fifteen point nine pounds!"),
    ("A 100% cook pleaser.", "A one hundred percent cook pleaser."),
    ("I need a cog #5.", "I need a cog number five."),
    ("Between 2pm and 4pm.", "Between two P M and four P M."),
    ("Pages 18, 21and 24.", "Pages eighteen, twenty-one and twenty-four."),
    ("Place the PX83-Enigmatron.", "Place the PX eighty-three-Enigmatron."),
])
def test_numbers_expanded(raw, spoken):
    assert prepare(raw) == spoken


@pytest.mark.parametrize("raw, spoken", [
    ("Ask Mr. Smith.", "Ask Mister Smith."),
    ("Ask Mrs. Smith.", "Ask Missus Smith."),
    ("Ask Dr. Smith.", "Ask Doctor Smith."),
    ("The Venture Co. geologists.", "The Venture Company geologists."),
    ("Fight the Venture Co.", "Fight the Venture Company."),
    ("Fight the Venture Co. They are greedy.", "Fight the Venture Company. They are greedy."),
    ("Ask Lt. Doren and Sgt. Hartman.", "Ask Lieutenant Doren and Sergeant Hartman."),
])
def test_abbreviations_expanded(raw, spoken):
    assert prepare(raw) == spoken


def test_initials_do_not_start_a_sentence():
    assert prepare("The attacks on the K.E.F. and the rest.") == "The attacks on the K.E.F. and the rest."


def test_ellipsis_does_not_start_a_sentence():
    assert prepare("Ursangous is... was mighty.") == "Ursangous is... was mighty."


# --- Lexicon hook ---

def test_lexicon_applied_last():
    lexicon = lambda s: s.replace("Kel'Thuzad", "Kel-thoo-zad")
    assert prepare("Beware Kel'Thuzad, $c.", lexicon=lexicon) == "Beware Kel-thoo-zad, friend."


# --- Real Source Data ---

WORLD = Path(os.environ.get("VO_WORLD", cli.DEFAULT_WORLD))


@pytest.mark.skipif(not WORLD.exists(), reason="VMaNGOS world DB not present (set VO_WORLD)")
def test_real_quest_texts_are_clean():
    world = sqlite3.connect(f"file:{WORLD}?mode=ro", uri=True)
    texts = [t for row in world.execute(
        "SELECT Details, Objectives, RequestItemsText, OfferRewardText FROM quest_template") for t in row if t]
    token_texts = [t for t in texts if "$" in t or "<" in t or "|" in t]
    sample = random.Random(0).sample(texts, 500) + token_texts
    for raw in sample:
        for gender in text.genders(raw):
            spoken = prepare(raw, gender)
            # Only a line that is nothing but a stage direction ("<Thorius sobs.>") has nothing to say.
            assert spoken or re.fullmatch(r"(\s|\$[Bb]|<[^>]*>)*", raw), raw
            for bad in ("$", "|c", "|r", "<", ">"):
                assert bad not in spoken, (raw, spoken)
            assert not any(c.isdigit() for c in spoken), (raw, spoken)


def test_stage_direction_only_line_is_silent():
    assert prepare("<Thorius sobs.>") == ""

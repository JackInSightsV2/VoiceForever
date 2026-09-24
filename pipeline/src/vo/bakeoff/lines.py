"""The fixed bake-off line set: original lines in the style of WoW quest text and Gossip.

Player tokens are already replaced by a Neutral Address ("friend", "adventurer").
Each line is assigned to one reference voice so every race hears every kind of line.
"""
from dataclasses import dataclass

KINDS = ("greeting", "detail", "progress", "completion", "lore")


@dataclass(frozen=True)
class Line:
    id: str
    kind: str
    voice: str
    text: str


def _l(n: int, kind: str, voice: str, text: str) -> Line:
    return Line(f"{n:02d}-{kind}", kind, voice, text)


LINES: tuple[Line, ...] = (
    # --- dwarf, male ---
    _l(1, "greeting", "dwarf_m", "Ah, a fresh face in Ironforge! Pull up a stool, friend, and mind the soot."),
    _l(2, "detail", "dwarf_m",
       "The troggs have broken through the lower tunnels of the Gnomeregan road again. "
       "Filthy creatures, and there's more of 'em every week. I need someone with a strong arm "
       "to clear out the nest near the old mine cart. Bring me back eight trogg claws as proof, "
       "and I'll see you're paid in good Ironforge silver."),
    _l(3, "progress", "dwarf_m", "Have you got them claws yet? The lads won't go back down there until it's done."),
    _l(4, "completion", "dwarf_m", "Ha! That's the stuff! Those troggs'll think twice before crawling back up here. Here, you've earned this."),
    _l(5, "lore", "dwarf_m", "My grandfather fought beside the Bronzebeards when the Dark Irons came calling. Stormwind owes us more than it'll ever admit."),
    _l(6, "detail", "dwarf_m",
       "A shipment of black powder went missing on the road out of Thelsamar. The driver swears it was "
       "bandits, but I smell kobold. Search the hills to the east and find where they've stashed it. "
       "And be careful, friend. One stray spark and you'll be picking pebbles out of your beard for a month."),
    _l(7, "greeting", "dwarf_m", "Aye, what'll it be? Ale, a bed, or a bit of both?"),
    _l(8, "progress", "dwarf_m", "Still no sign of that powder? Hmph. Keep lookin'."),
    _l(9, "completion", "dwarf_m", "You found it, and not a crate blown sky high. You've a steady hand for an outsider."),
    _l(10, "lore", "dwarf_m", "They say the Scourge has crossed into Quel'Thalas. If the elves can't hold, the north is lost."),
    # --- night elf, female ---
    _l(11, "greeting", "nelf_f", "Ishnu-alah, traveller. The shade of Teldrassil welcomes you."),
    _l(12, "detail", "nelf_f",
       "Something has poisoned the moonwells of the northern glade. The water runs dark, and the "
       "creatures that drink from it turn wild and hateful. I believe the corruption comes from a "
       "fel-touched totem hidden among the roots. Find it, destroy it, and bring me a vial of the "
       "tainted water so that I may study what has been done to our home."),
    _l(13, "progress", "nelf_f", "Have you found the source of the corruption? The glade grows weaker with every night."),
    _l(14, "completion", "nelf_f", "The water is already clearing. Elune has guided your hand, and the forest will remember this."),
    _l(15, "lore", "nelf_f", "Ten thousand years we have kept our watch. Now the silithid stir beneath Ahn'Qiraj, and the old wall trembles."),
    _l(16, "detail", "nelf_f",
       "The sentinels have not returned from their patrol near the Barrens border. Their last message "
       "spoke of orc scouts felling the ancient trees for their war machines. Travel south to the "
       "Forest Song outpost and learn what became of them. If they have fallen, recover their "
       "moonglaives. They must not remain in enemy hands."),
    _l(17, "greeting", "nelf_f", "Walk softly here, adventurer. The trees are listening."),
    _l(18, "progress", "nelf_f", "The sentinels are still missing. Do not linger."),
    _l(19, "completion", "nelf_f", "Their glaives are returned to us. We will sing for them tonight, beneath the moon."),
    _l(20, "lore", "nelf_f", "Darnassus was built in hope. I pray it does not become another monument to what we have lost."),
    # --- orc, male ---
    _l(21, "greeting", "orc_m", "Lok'tar, friend. Speak quickly. There is work to be done."),
    _l(22, "detail", "orc_m",
       "The quilboar grow bold. They raid our caravans on the road to Crossroads and leave our "
       "grunts for the vultures. Thrall wants the road secure, and I will not fail the Warchief. "
       "Go to the Razormane camps and kill their thornweavers. Without their magic, the rest will "
       "scatter like frightened boars. Return when the job is finished, and not before."),
    _l(23, "progress", "orc_m", "Why are you standing here? The thornweavers still breathe."),
    _l(24, "completion", "orc_m", "Good. You fight with honor. Orgrimmar will hear of this."),
    _l(25, "lore", "orc_m", "We were slaves once, in the camps of Lordaeron. Thrall broke those chains. Never forget it."),
    _l(26, "detail", "orc_m",
       "One of our wolf riders was taken by the centaur of the Kolkar clan. He carries orders that "
       "must reach Camp Taurajo before the next moon. Find the Kolkar war camp, cut him free, and "
       "see him safely out. If he is already dead, take the orders from his body. The message "
       "matters more than the messenger."),
    _l(27, "greeting", "orc_m", "Strength and honor. What do you need?"),
    _l(28, "progress", "orc_m", "Have you freed my rider? Every hour he is in their hands is an hour too long."),
    _l(29, "completion", "orc_m", "He lives, and the orders are safe. The Horde thanks you. Take this, and wear it proudly."),
    _l(30, "lore", "orc_m", "Some say the Burning Legion is gone. I say it only waits, like a wolf at the edge of the firelight."),
    # --- human, female ---
    _l(31, "greeting", "human_f", "Welcome to Stormwind, friend. Mind the guards, they're in a foul mood today."),
    _l(32, "detail", "human_f",
       "My brother went to Westfall to work the fields after the harvest failed here. That was two "
       "months ago, and his letters have stopped. People say the Defias have taken the farms along "
       "the coast road. I can't go myself, not with the little ones to look after. Would you go to "
       "Sentinel Hill and ask after him? His name is Tomas, and he has a scar across his chin."),
    _l(33, "progress", "human_f", "Any word of Tomas? Please, anything at all."),
    _l(34, "completion", "human_f", "He's alive? Oh, thank the Light. I don't know how I'll ever repay you, but please, take this."),
    _l(35, "lore", "human_f", "My father helped rebuild Stormwind after the orcs burned it. The stonemasons were never paid, you know. Not one copper."),
    _l(36, "detail", "human_f",
       "The Cathedral has asked for volunteers to carry medicine to the Redridge Mountains. "
       "The blackrock orcs have Lakeshire half under siege, and the wounded are running out of time. "
       "Take this satchel of salves to Magistrate Solomon in the town hall. Travel quickly and "
       "stay off the high passes. The worgen have been seen in Duskwood again."),
    _l(37, "greeting", "human_f", "Oh! You startled me. Can I help you with something, adventurer?"),
    _l(38, "progress", "human_f", "You haven't delivered the salves yet? Lakeshire can't wait much longer."),
    _l(39, "completion", "human_f", "The Magistrate sent word. You've saved lives today, friend. The Light will not forget it."),
    _l(40, "lore", "human_f", "They say Kel'Thuzad was a man once, a scholar of Dalaran. Hard to believe anything human could become that."),
    # --- troll, male ---
    _l(41, "greeting", "troll_m", "Hey mon, welcome to Sen'jin. Don't be touchin' the fetishes, dey bite."),
    _l(42, "detail", "troll_m",
       "Dem murlocs been stealin' our fishin' nets again, and my people be gettin' hungry. "
       "Dey hidin' in a cave on de far side of Echo Isles, all piled up like a treasure hoard. "
       "Go and take back six nets, and if one of dem big mudscale warriors gets in your way, "
       "you be showin' him de sharp end of your blade. Vol'jin be watchin', so do it proper."),
    _l(43, "progress", "troll_m", "You got dem nets yet, mon? De fish not gonna wait forever."),
    _l(44, "completion", "troll_m", "Ha! Now dat be what I call a good catch! Here, take a little somethin' for your trouble."),
    _l(45, "lore", "troll_m", "Da Gurubashi empire was mighty once. Now Zul'Gurub be full of blood priests and bad mojo."),
    _l(46, "detail", "troll_m",
       "De spirits be restless tonight. Somethin' been disturbin' de old burial ground north of "
       "Razor Hill. I need you to go and look, but you don't go empty handed. Take dis totem and "
       "place it at de center of de graves. If de spirits rise, you stand your ground, "
       "and you speak to dem with respect."),
    _l(47, "greeting", "troll_m", "Ah, a new friend. Sit, sit. De fire be warm tonight."),
    _l(48, "progress", "troll_m", "De totem still in your pack? De spirits be waitin', mon."),
    _l(49, "completion", "troll_m", "De spirits be calm now. You got a good heart, even if you be a little bit strange."),
    _l(50, "lore", "troll_m", "Undercity be a nasty place, mon. Da Forsaken say dey free, but dey smell like a graveyard."),
)

# Lines the Narrator candidates also read: quest detail text is what the Narrator voices.
NARRATOR_LINES: tuple[str, ...] = ("02-detail", "12-detail", "22-detail", "32-detail", "42-detail", "15-lore")


def by_id() -> dict[str, Line]:
    return {line.id: line for line in LINES}

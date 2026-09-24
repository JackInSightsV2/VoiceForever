# Voice Packs are split by faction and level band, assigned per quest and per NPC

Every line goes to one of 18 Voice Packs: `VoiceForever_<Alliance|Horde|Neutral>_<1-10|10-20|...|50-60>`. A band is named by the level a player enters it: levels 1–9 go to 1-10, 10–19 to 10-20, and 50 and above to 50-60. A zone that opens at 10 (Westfall) is therefore in 10-20. The Neutral packs hold lines both factions hear, such as Booty Bay, Ratchet, the Argent Dawn and the Cenarion Circle, so a player installs their faction's packs plus the Neutral ones for their levels.

Quest Text is assigned per quest, so all of a quest's parts ship together:

- **Faction:** RequiredRaces first. If that doesn't decide it, the faction of the quest's giver and ender (one side's NPC with no NPC from the other side). With no NPC at all (Narrator only), the quest zone's team. Otherwise Neutral.
- **Band:** QuestLevel, else MinLevel, else the quest zone's minimum level, else its NPCs' band.

Gossip is assigned per NPC:

- **Faction:** from the NPC's faction template. If it is hostile to every race of one faction, it belongs to the other faction; otherwise it is Neutral.
- **Band:** the lowest minimum level of the zones it spawns in, where capitals count as level 1; otherwise the NPC's own level.

A Neutral template is never moved to a faction because of its zone (Ratchet is in the Horde's Barrens). Zone minimum levels and zone teams are a fixed table in `vo/packs.py`, since `area_template.area_level` is empty. Assignment reads only the pipeline DB and the VMaNGOS world DB, so the same inputs always give the same packs. The Core Addon merges whatever packs are installed, and a missing pack is just a lookup miss recorded through Capture.

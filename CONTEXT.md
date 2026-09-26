# VoiceForever

Pre-generated, per-NPC voice acting for every line of WoW Classic dialogue, shipped as a client addon for World of Warcraft: Forever.

## Target

**Forever**:
World of Warcraft: Forever, Blizzard's official level-60 branch of WoW set in the original 2004 world, running on the retail engine and addon API. The game the addon targets.
_Avoid_: Warcraft Forever, 1.12 client, vanilla client, private server

**Source Data**:
The offline data behind Core Content: the VMaNGOS world database (quest text, gossip, NPCs, spawns, quest links), QuestieDB's Forever tables (zones, quest givers), and the Forever client's display tables (race and gender).

## Voices

**Archetype**:
A human-approved voice template for one race and gender, or one Creature Family, fixed by its Base Voices. A race and gender may have several. An Archetype is approved once it has at least one Base Voice.
_Avoid_: base archetype

**Base Voice**:
One approved Anchor of an Archetype: a different person of that race and gender. An Archetype has up to eight. Its NPCs are spread across them, and Neighbours get different ones where possible.
_Avoid_: "the Archetype anchor" (there may be several)

**Approval Gate**:
The only human step before an unattended run: approving Base Voices for every Archetype and the Lexicon's top names. Approval can be incremental: a partial run voices the lines of Archetypes approved so far, and the rest wait.

**Lexicon**:
Phonetic spellings for lore names (e.g. Kel'Thuzad), used in spoken audio. The most frequent names are human-approved; the rest are drafted and ASR-checked automatically.

**Anchor**:
The short clip that fixes a voice's identity. Every line of that voice is generated as a continuation of its anchor. An Archetype's anchors (its Base Voices) are picked by ear; an NPC's anchor is derived from its Base Voice automatically.
_Avoid_: reference clip, ref

**Creature Family**:
A group of non-humanoid creature types that share one Archetype (e.g. Great Beasts: dragons, demons, giants).

**Candidate**:
A proposed Anchor for an Archetype, awaiting human review. Every Candidate the reviewer would be happy to hear as an NPC of that race is approved, and becomes a Base Voice. Most are designed by VoxCPM2; a game voice Candidate is built from WoW's own NPC voice clips of one speaker (ADR-0007). An Archetype with game voice Candidates designs no more: its new Candidates are variations. A variation Candidate is derived from another Candidate on request: its anchor cloned with a style, or strongly shifted in pitch, formant and pace. A retired Candidate is hidden from review but kept.

**NPC Voice**:
One NPC's own voice, derived automatically from one Base Voice of its Archetype. There is no per-NPC human step.
_Avoid_: hand-crafted voice

**Neighbours**:
Two NPCs a player is likely to hear close together: their spawns are near each other, or they share a quest chain. Neighbours' NPC Voices must sound clearly different.

**Narrator**:
The single voice used for quest text that doesn't come from an NPC. It is outside the NPC Voice system.

## Lines

**Neutral Address**:
A neutral word that replaces a player-specific token (name `$N`, race `$R`, class `$C`) in spoken audio, e.g. "friend" or "adventurer". The on-screen text keeps the real word. Player gender (`$G`) is not neutralised; both variants are voiced.

**Quest Text**:
The text of one quest shown in the quest window: detail (with objectives), progress and completion. Identified by quest, part and player gender; never by its wording.

**Drift**:
Quest Text whose wording in Forever no longer matches the Source Data. The audio still plays; Drift is recorded through Capture so the line can be regenerated.

**Gossip**:
NPC-level text shown when the player interacts with an NPC: the gossip window text, or the quest greeting of an NPC offering several quests. Belongs to the NPC, not to a quest.

**Ambient Line**:
Text an NPC says, yells, whispers or emotes without the player interacting (e.g. "%s growls", a guard's yell). Out of scope: never voiced.
_Avoid_: scripted line, emote, bark

## Content

**Core Content**:
Dialogue that exists in the Source Data, i.e. the original 2004 world as carried into Forever. The v1 target.

**Forever Content**:
Dialogue added by Forever that the Source Data doesn't know (new quests, zones, the Skyborne race). Voiced incrementally as it is captured in-game. Its Quest Text can also be read ahead of Capture from Wowhead's Forever pages; in-game text wins.

**Capture**:
Dialogue text and NPC details recorded in-game by the Core Addon, fed back into the pipeline as a second source alongside the Source Data. Players contribute Capture by uploading their saved addon data to a public upload page.

**Page Text**:
Text of books, plaques and readable items. Out of scope: never voiced.

## Distribution

**Core Addon**:
The small addon players always install: interaction handling, lookup, playback, settings and Capture. Contains no audio.

**Voice Pack**:
A separately installable addon holding the audio for one faction and level band (e.g. Alliance 1–10). Players install only the packs they need.
_Avoid_: data pack, audio pack, zone pack

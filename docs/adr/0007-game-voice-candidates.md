---
status: accepted
---

# Archetype Candidates may be built from WoW's own NPC voice clips

Besides VoxCPM2-designed Candidates (ADR-0004), an Archetype can have game voice Candidates: anchors assembled from the NPC voice clips that ship in the Forever client (the greeting, farewell, vendor and "pissed" sets per race and gender, plus a few speaking creatures). They are downloaded by FileDataID, transcribed with the pipeline's Whisper, grouped into distinct speakers by speaker embedding, and joined into 8–15 s anchors that every line of an approved one then continues from. The user asked for this after being told the risks. The voices are performances by Blizzard's voice actors, and cloning them into new lines for a publicly distributed addon may raise likeness and consent concerns with those actors, may breach Blizzard's terms of use for game assets, and could lead to a takedown of the addon or its Voice Packs. The decision is the user's. Game voice Candidates go through the same Approval Gate and the same eight-Base-Voice cap as designed ones, and are marked "game voice" on the Approval page, so each one used is a separate, deliberate approval and can be withdrawn by unapproving it.

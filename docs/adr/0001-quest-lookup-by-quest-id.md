# Quest Text is looked up by quest ID, not by text hash

The addon identifies Quest Text by `(questId, part, player gender)` using `GetQuestID()`, rather than hashing the displayed text as the original spec planned. Text hashing needed identical normalisation in Lua and Python and broke on player tokens, and it made any Blizzard rewording in Forever a silent miss. A text hash is still shipped, but only to detect Drift: on mismatch the audio plays and the change is logged through Capture. Gossip has no ID, so it uses per-NPC template matching instead.

# Sending captured dialogue

World of Warcraft: Forever has quests and NPCs that aren't in any public database. While you play, VoiceForever quietly
records the lines it doesn't know yet (quest text, gossip, who said it and where). Nothing is uploaded automatically,
and nothing recorded can log in to your account: it's only the text NPCs said to you.

**Talk to everyone** for the most useful captures: open every quest, hand quests in (turn-in text is only captured when
you actually hand in), and talk to trainers, vendors and guards. New Forever content matters most.

The game writes the addon's data to disk only when you log out or reload, so type `/vf save` now and then, and log out
normally rather than closing the window.

## Option A: paste into a GitHub issue

1. In game, type **`/vf export`**. A box opens with your captured dialogue already selected.
2. Press **Ctrl+C** (Mac: **Cmd+C**).
3. Open a **[Submit captured dialogue](../../../issues/new?template=capture.yml)** issue (you need a free GitHub
   account), paste into *Captured dialogue*, and click **Create**.

If the box says **Page 1 of N**, paste page 1 into the issue, then click **Next >** and paste each other page as a
comment on the same issue.

## Option B: attach the collector zip

The collector script also sends the game's caches, which hold the text of every quest you've seen, not only the lines
the addon recorded.

1. Close the game.
2. Run the script from the [`collector`](../collector) folder:
   - **Windows:** double-click `Collect VoiceForever.bat`
   - **Mac:** double-click `Collect VoiceForever.command` (the first time: right-click it, then **Open**)
3. It saves `VoiceForever-<your name>-<date>.zip` on your Desktop. Drag it into the *Files* box of a
   [Submit captured dialogue](../../../issues/new?template=capture.yml) issue.

What the zip holds (all small):

| File | What it holds |
| --- | --- |
| `WTF/Account/<ACCOUNT>/SavedVariables/VoiceForever.lua` | What the addon captured: gossip, quest text, and which NPC said it |
| `Cache/WDB/enUS/questcache.wdb` | Every quest your game has seen |
| `Cache/WDB/enUS/creaturecache.wdb` | Every NPC your game has seen: names, titles, what they look like |
| `Cache/WDB/enUS/gameobjectcache.wdb` | Objects: wanted posters, books, notes |
| `Cache/ADB/enUS/DBCache.bin` | Dialogue the server has sent your game |

Sending the same thing twice does no harm; duplicates are merged.

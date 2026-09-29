# Settings

Open the panel with `/vf options`, or **Options → AddOns → VoiceForever**. Every setting is also a `/vf` command:
`/vf <setting> on`, `/vf <setting> off`, or `/vf <setting>` to toggle.

| Setting (`/vf` name) | Default | What it does |
| --- | --- | --- |
| Auto-play (`autoplay`) | on | Play a line as soon as its window opens. Off: use the Replay button or `/vf replay`. |
| Quest details (`detail`) | on | Voice the quest text when a quest is offered. |
| Quest progress (`progress`) | on | Voice the quest giver while a quest is in progress. |
| Quest completion (`complete`) | on | Voice the quest giver when you hand in a quest. |
| Quest greetings (`greeting`) | on | Voice NPCs that offer several quests. |
| Gossip (`gossip`) | on | Voice NPC gossip windows. |
| Greet once (`greetonce`) | on | Speak an NPC's greeting or gossip only once: coming back within a minute, without walking away, stays quiet. Replay still plays it. |
| Narrator (`narrator`) | on | Voice quest objectives, and quests from objects and items, with the Narrator. |
| Narrator only (`narratoronly`) | off | Only the Narrator speaks: quest objectives, and quests from objects and items. NPCs stay silent. |
| Text follows the voice (`reveal`) | on | Reveal the quest text word by word as it is spoken. Off: the text shows as usual. |
| Replay button (`replaybutton`) | on | Show a Replay button on the quest and gossip windows. |
| English audio on non-English clients (`englishaudio`) | on | Play the English quest audio on non-English clients. Gossip is voiced on English clients only. |

Two values:

- **Volume** (`/vf volume <0-100>`): the game's Dialog volume, which the voice plays on.
- **Start delay** (`/vf delay <0-3>`): seconds between a window opening and its voice starting. Default 1.

`/vf reset` restores every default.

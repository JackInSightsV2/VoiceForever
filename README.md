<p align="center"><img src="assets/icon.png" width="128" alt="VoiceForever"></p>

# VoiceForever

Voice-over for quest text and NPC gossip in **World of Warcraft: Forever**.

When you talk to an NPC or open a quest, VoiceForever plays that character's lines out loud: the quest giver speaks the
quest, and a Narrator reads the objectives and the quests that come from objects (wanted posters, notes, books). The
quest text fades in word by word in step with the voice.

## Features

- **Quests and gossip**: quest details, progress, completion, multi-quest greetings and NPC gossip.
- **Narrator**: quest objectives, and quests started from objects and items. A **Narrator only** setting keeps
  NPCs silent and plays just the Narrator.
- **Text follows the voice**: the quest text reveals word by word as it is spoken.
- **Greet once**: coming back to an NPC within a minute doesn't repeat its greeting.
- **Works with your quest UI**: Blizzard's quest frame, [Immersion](https://www.curseforge.com/wow/addons/immersion) and
  [DialogueUI](https://www.curseforge.com/wow/addons/dialogueui).
- **Replay and stop** buttons and key bindings, a start delay, and per-type toggles.
- **Play** in the quest log: hear any quest you've picked up again.
- **Dialogue capture**: lines the addon doesn't know yet are recorded, so new Forever content can be added
  ([how to send them](docs/contributing-dialogue.md)).

## Install

1. Download this repository (**Code → Download ZIP**) and unzip it.
2. Copy the `VoiceForever` folder into your Forever AddOns folder:
   - **Windows:** `C:\Program Files (x86)\World of Warcraft\_classic_beta_\Interface\AddOns\`
   - **Mac:** `/Applications/World of Warcraft/_classic_beta_/Interface/AddOns/`
3. Start the game, open **AddOns** at character select, and make sure **VoiceForever** is ticked.

Type `/vf` in game to check it loaded. This repository contains the addon only; no audio is included.

## Use

| Command | What it does |
| --- | --- |
| `/vf` | Status and the list of commands |
| `/vf options` | Open the settings panel (also under **Options → AddOns → VoiceForever**) |
| `/vf replay` / `/vf stop` | Replay or stop the current line |
| `/vf volume <0-100>` | Dialog volume |
| `/vf delay <0-3>` | Seconds between a window opening and its voice starting (default 1) |
| `/vf <setting> [on\|off]` | Toggle a setting, e.g. `/vf gossip off` |
| `/vf export` | A copyable box of captured dialogue ([contributing](docs/contributing-dialogue.md)) |
| `/vf save` | Reload the UI so captured dialogue is written to disk |
| `/vf reset` | Restore the default settings |

Key bindings for **Replay** and **Stop** are under **Options → Keybindings → VoiceForever**.

All settings are described in [docs/settings.md](docs/settings.md).

## Compatibility

- **Game:** World of Warcraft: Forever (Interface 16001).
- **Language:** quest text plays on every client; gossip plays on English clients (the audio is English).
- **Quest UIs:** Blizzard's, Immersion and DialogueUI are supported, including the word-by-word text reveal.

## Contributing

Forever adds quests and NPCs that aren't in any public database. If you play with VoiceForever on, you can send the
dialogue it captured: see [docs/contributing-dialogue.md](docs/contributing-dialogue.md). Bugs and ideas are welcome as
[issues](../../issues).

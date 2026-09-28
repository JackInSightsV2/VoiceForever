#!/bin/bash
# VoiceForever collector (Mac): zips what the addon captured and the game's caches, for sending back.
# Collects only: WTF/Account/*/SavedVariables/VoiceForever.lua and Cache/WDB/enUS/{questcache,creaturecache,
# gameobjectcache}.wdb and Cache/ADB/enUS/DBCache.bin. Nothing that can log in to your account.
set -u
GAME="/Applications/World of Warcraft/_classic_beta_"
if [ ! -d "$GAME/WTF" ]; then
  GAME=$(osascript -e 'POSIX path of (choose folder with prompt "Where is World of Warcraft: Forever? Pick the _classic_beta_ folder.")' 2>/dev/null)
  GAME="${GAME%/}"
fi
if [ -z "$GAME" ] || [ ! -d "$GAME/WTF" ]; then echo "Couldn't find the game folder (it has a WTF folder inside)."; read -r -p "Press Return to close."; exit 1; fi
NAME=$(id -un | tr -cd '[:alnum:]_-')
OUT="$HOME/Desktop/VoiceForever-$NAME-$(date +%Y-%m-%d).zip"
cd "$GAME" || exit 1
FILES=()
while IFS= read -r f; do FILES+=("$f"); done < <(find WTF/Account -path "*/SavedVariables/VoiceForever.lua" 2>/dev/null)
for f in Cache/WDB/enUS/questcache.wdb Cache/WDB/enUS/creaturecache.wdb Cache/WDB/enUS/gameobjectcache.wdb Cache/ADB/enUS/DBCache.bin; do
  [ -f "$f" ] && FILES+=("$f")
done
if [ ${#FILES[@]} -eq 0 ]; then echo "Nothing to collect yet: play a session with VoiceForever on, then log out."; read -r -p "Press Return to close."; exit 1; fi
rm -f "$OUT"
zip -q "$OUT" "${FILES[@]}"
echo "Saved $OUT (${#FILES[@]} files). Send it as the collector README says."
open -R "$OUT"
read -r -p "Press Return to close."

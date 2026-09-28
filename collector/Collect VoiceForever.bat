@echo off
rem VoiceForever collector (Windows): zips what the addon captured and the game's caches, for sending back.
rem Collects only: WTF\Account\*\SavedVariables\VoiceForever.lua and Cache\WDB\enUS\{questcache,creaturecache,
rem gameobjectcache}.wdb and Cache\ADB\enUS\DBCache.bin. Nothing that can log in to your account.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$game = 'C:\Program Files (x86)\World of Warcraft\_classic_beta_';" ^
  "if (-not (Test-Path \"$game\WTF\")) { Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description = 'Where is World of Warcraft: Forever? Pick the _classic_beta_ folder.'; if ($d.ShowDialog() -eq 'OK') { $game = $d.SelectedPath } };" ^
  "if (-not (Test-Path \"$game\WTF\")) { Write-Host 'Could not find the game folder (it has a WTF folder inside).'; exit 1 };" ^
  "$tmp = Join-Path $env:TEMP ('vf-' + [guid]::NewGuid()); New-Item -ItemType Directory $tmp | Out-Null;" ^
  "$files = @(Get-ChildItem \"$game\WTF\Account\" -Recurse -Filter VoiceForever.lua -ErrorAction SilentlyContinue | Where-Object { $_.Directory.Name -eq 'SavedVariables' });" ^
  "foreach ($c in 'Cache\WDB\enUS\questcache.wdb','Cache\WDB\enUS\creaturecache.wdb','Cache\WDB\enUS\gameobjectcache.wdb','Cache\ADB\enUS\DBCache.bin') { if (Test-Path \"$game\$c\") { $files += Get-Item \"$game\$c\" } };" ^
  "if ($files.Count -eq 0) { Write-Host 'Nothing to collect yet: play a session with VoiceForever on, then log out.'; exit 1 };" ^
  "foreach ($f in $files) { $rel = $f.FullName.Substring($game.Length + 1); $dest = Join-Path $tmp $rel; New-Item -ItemType Directory (Split-Path $dest) -Force | Out-Null; Copy-Item $f.FullName $dest };" ^
  "$name = ($env:USERNAME -replace '[^A-Za-z0-9_-]', ''); $out = Join-Path ([Environment]::GetFolderPath('Desktop')) (\"VoiceForever-$name-\" + (Get-Date -Format yyyy-MM-dd) + '.zip');" ^
  "if (Test-Path $out) { Remove-Item $out }; Compress-Archive -Path (Join-Path $tmp '*') -DestinationPath $out; Remove-Item $tmp -Recurse -Force;" ^
  "Write-Host \"Saved $out ($($files.Count) files). Send it as the collector README says.\"; Start-Process explorer.exe \"/select,`\"$out`\"\""
pause

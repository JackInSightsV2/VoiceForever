"""Symlink the Core Addon and built Voice Packs into a WoW AddOns directory."""
from pathlib import Path


def install(addons_dir: Path, core: Path, packs_dir: Path) -> list[Path]:
    if not addons_dir.is_dir():
        raise NotADirectoryError(addons_dir)
    sources = [core, *sorted(p for p in packs_dir.iterdir() if p.is_dir())] if packs_dir.is_dir() else [core]
    links = []
    for src in sources:
        link = addons_dir / src.name
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            raise FileExistsError(f"{link} exists and is not a symlink; remove it first")
        link.symlink_to(src.resolve(), target_is_directory=True)
        links.append(link)
    return links

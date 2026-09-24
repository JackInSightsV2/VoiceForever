import json
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
CORE = TESTS.parents[1] / "addon" / "VoiceForever"
VECTORS = json.loads((TESTS / "drift_vectors.json").read_text())


def lua_literal(v) -> str:
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + "".join(f"\\{ord(c):03d}" if c in '\\"' or ord(c) < 32 else c for c in v) + '"'
    if isinstance(v, list):
        return "{" + ", ".join(lua_literal(x) for x in v) + "}"
    return "{" + ", ".join(f"[{lua_literal(k)}] = {lua_literal(x)}" for k, x in v.items()) + "}"


def addon_files() -> list[Path]:
    """The Core Addon's Lua files in the order its .toc loads them."""
    lines = (CORE / "VoiceForever.toc").read_text().splitlines()
    return [CORE / l.strip() for l in lines if l.strip() and not l.startswith("#")]


@pytest.fixture
def lua():
    """Run a Lua script with the WoW stub and ADDON_FILES set; return the JSON values it emit()s."""
    if not shutil.which("lua"):
        pytest.skip("lua not installed")

    def run(script: str) -> list:
        prelude = (f"ADDON_FILES = {lua_literal([str(f) for f in addon_files()])}\n"
                   f"dofile({lua_literal(str(TESTS / 'wowstub.lua'))})\n")
        out = subprocess.run(["lua", "-"], input=prelude + script, capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return [json.loads(l) for l in out.stdout.splitlines()]

    return run

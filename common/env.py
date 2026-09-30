"""Load `.env` into the process environment.

A tiny loader rather than a dependency on python-dotenv: the file format we
need is `KEY=value` lines, and adding a package for that is not worth it.

Values already present in the real environment win, so an exported variable
always beats the file.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def parse_env_file(path: Path) -> Dict[str, str]:
    """Read `KEY=value` lines, ignoring blanks, comments and `export `."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    # utf-8-sig: a file saved from a Windows editor may carry a BOM, which
    # would otherwise end up inside the first key name.
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_env(path: Optional[Path] = None, override: bool = False) -> Dict[str, str]:
    """Load the file into `os.environ` and return what it defined."""
    values = parse_env_file(path or ENV_FILE)
    for key, value in values.items():
        if override or not os.environ.get(key):
            os.environ[key] = value
    return values


def require(*names: str) -> str:
    """First of `names` that is set, after loading `.env`.

    Raises with a message that says exactly what to do, rather than failing
    deep inside an API client.
    """
    load_env()
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    raise RuntimeError(
        f"none of {', '.join(names)} is set. Put it in {ENV_FILE} as "
        f"`{names[0]}=your-key`, or export it in your shell."
    )

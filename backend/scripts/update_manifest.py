"""Regenerate protected_files.sha256 after an intentional edit to a tracked module."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "protected_files.sha256"


def main() -> None:
    names = [line.split()[-1] for line in MANIFEST.read_text().splitlines() if line.strip() and not line.startswith("#")]
    lines = [f"{hashlib.sha256((ROOT / n).read_bytes().replace(b"\r\n", b"\n")).hexdigest().upper()}  {n}" for n in names]
    MANIFEST.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

"""SHA-256 integrity verification for the six read-only core modules."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.config import PROJECT_ROOT


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_protected_manifest(manifest_path: Path) -> dict:
    result: dict = {
        "manifest": str(manifest_path),
        "ok": False,
        "files": [],
        "error": None,
    }
    if not manifest_path.exists():
        result["error"] = "Protected-file hash manifest is missing."
        return result

    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        result["error"] = f"Could not read hash manifest: {exc}"
        return result

    all_ok = True
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        expected, name = parts[0].lower(), parts[-1]
        path = PROJECT_ROOT / name
        entry = {
            "name": name,
            "expected": expected,
            "actual": None,
            "ok": False,
            "error": None,
        }
        if not path.exists():
            entry["error"] = "File is missing."
            all_ok = False
            result["files"].append(entry)
            continue
        try:
            actual = _sha256(path)
        except OSError as exc:
            entry["error"] = str(exc)
            all_ok = False
            result["files"].append(entry)
            continue
        entry["actual"] = actual
        entry["ok"] = actual == expected
        all_ok = all_ok and entry["ok"]
        result["files"].append(entry)

    result["ok"] = all_ok and bool(result["files"])
    if not result["files"]:
        result["error"] = "Hash manifest contained no file entries."
    return result

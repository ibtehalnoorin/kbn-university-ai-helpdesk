"""Protected-file integrity tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.config import PROJECT_ROOT
from app.services.integrity import verify_protected_manifest

PROTECTED = [
    "main.py",
    "processor.py",
    "kb_manager.py",
    "ingest.py",
    "helpers.py",
    "researcher.py",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_manifest_exists_and_lists_all_protected_files() -> None:
    manifest = PROJECT_ROOT / "protected_files.sha256"
    assert manifest.exists()
    text = manifest.read_text(encoding="utf-8")
    for name in PROTECTED:
        assert name in text


def test_all_protected_files_match_the_recorded_hashes() -> None:
    result = verify_protected_manifest(PROJECT_ROOT / "protected_files.sha256")
    assert result["ok"] is True, result
    checked = {entry["name"] for entry in result["files"]}
    assert checked == set(PROTECTED)
    for entry in result["files"]:
        assert entry["ok"] is True, entry
        assert entry["actual"] == _sha256(PROJECT_ROOT / entry["name"])


def test_manifest_detects_a_changed_file(tmp_path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("print('original')\n", encoding="utf-8")
    manifest = tmp_path / "sample.sha256"
    manifest.write_text(
        f"{'0' * 64}  {target.name}\n",
        encoding="utf-8",
    )
    result = verify_protected_manifest(manifest)
    assert result["ok"] is False
    assert result["files"][0]["ok"] is False


def test_hash_ignores_line_endings(tmp_path, monkeypatch) -> None:
    import app.services.integrity as integrity

    monkeypatch.setattr(integrity, "PROJECT_ROOT", tmp_path)
    (tmp_path / "sample.py").write_bytes(b"a = 1\r\nb = 2\r\n")  # Windows checkout
    lf_hash = hashlib.sha256(b"a = 1\nb = 2\n").hexdigest()
    manifest = tmp_path / "m.sha256"
    manifest.write_text(f"{lf_hash}  sample.py\n", encoding="utf-8")
    assert integrity.verify_protected_manifest(manifest)["ok"] is True

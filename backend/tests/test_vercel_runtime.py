"""Packaging for Vercel: read-only bundle, writable /tmp, FastAPI entrypoint."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_chroma_system():
    from chromadb.api.shared_system_client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    yield
    SharedSystemClient.clear_system_cache()


def test_kb_manager_honours_kbn_chroma_dir(monkeypatch, tmp_path):
    import embeddings
    from kb_manager import KnowledgeBase

    target = tmp_path / "elsewhere" / "store"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KBN_CHROMA_DIR", str(target))
    monkeypatch.setattr(embeddings, "embed", lambda text: [0.1, 0.2, 0.3])
    KnowledgeBase().add_document("d1", "hello", {"source": "t.md", "chunk_index": 0})
    assert (target / "chroma.sqlite3").exists()
    assert not (tmp_path / "local_vector_kb").exists()


def _fake_project(tmp_path: Path) -> Path:
    project = tmp_path / "bundle"
    (project / "local_vector_kb").mkdir(parents=True)
    (project / "local_vector_kb" / "chroma.sqlite3").write_text("db")
    (project / ".fastembed_cache").mkdir()
    return project


def test_prepare_runtime_copies_the_index_to_a_writable_dir_once(tmp_path):
    from app.runtime import prepare_runtime

    project, env = _fake_project(tmp_path), {}
    scratch = tmp_path / "tmp"
    first = prepare_runtime(project, env, read_only=True, scratch_dir=scratch)
    store = Path(env["KBN_CHROMA_DIR"])
    assert store == scratch / "kbn" / "local_vector_kb" and (store / "chroma.sqlite3").read_text() == "db"
    assert first["copied"] is True
    (store / "chroma.sqlite3").write_text("changed-at-runtime")
    second = prepare_runtime(project, env, read_only=True, scratch_dir=scratch)
    assert second["copied"] is False and (store / "chroma.sqlite3").read_text() == "changed-at-runtime"


def test_prepare_runtime_points_fastembed_at_the_bundled_model_cache(tmp_path):
    from app.runtime import prepare_runtime

    project, env = _fake_project(tmp_path), {}
    prepare_runtime(project, env, read_only=True, scratch_dir=tmp_path / "tmp")
    assert Path(env["FASTEMBED_CACHE_PATH"]) == tmp_path / "tmp" / "kbn" / "fastembed_cache"


def test_prepare_runtime_does_nothing_on_a_writable_host(tmp_path):
    from app.runtime import prepare_runtime

    project, env = _fake_project(tmp_path), {}
    result = prepare_runtime(project, env, read_only=False, scratch_dir=tmp_path / "tmp")
    assert env == {} and result["copied"] is False


def test_entrypoint_exposes_a_fastapi_app_from_a_read_only_style_start(tmp_path):
    code = "import index; print(type(index.app).__name__, index.app.title)"
    env = {**os.environ, "VERCEL": "1", "TMPDIR": str(tmp_path), "KBN_START_WORKER": "false"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    assert out.stdout.strip().splitlines()[-1].startswith("FastAPI KBN University AI Helpdesk")
    assert (tmp_path / "kbn" / "local_vector_kb" / "chroma.sqlite3").exists()


def test_vercel_json_builds_the_frontend_and_schedules_the_purge():
    config = json.loads((ROOT / "vercel.json").read_text())
    script = ROOT / "backend" / "scripts" / "vercel_build.py"
    assert "vercel_build.py" in config["buildCommand"] and script.exists()
    assert "npm" in script.read_text() and "TextEmbedding" in script.read_text()
    assert any(c["path"] == "/internal/cron/purge" for c in config["crons"])
    assert config["functions"]["index.py"]["maxDuration"] >= 60


def _requirement_names(lines) -> set[str]:
    names = set()
    for raw in lines:
        line = raw.split("#")[0].strip().strip(",").strip('"')
        if line and not line.startswith("-"):
            names.add(re.split(r"[\[<>=~! ;]", line, maxsplit=1)[0].lower())
    return names


def test_pyproject_dependencies_match_the_production_requirements():
    import tomllib

    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    prod = (ROOT / "backend" / "requirements-prod.txt").read_text().splitlines()
    assert _requirement_names(declared) == _requirement_names(prod)
    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["vercel"]["entrypoint"] == "index:app"

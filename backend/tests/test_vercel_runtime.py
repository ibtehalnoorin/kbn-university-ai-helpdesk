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


def test_prepare_runtime_reads_the_bundled_model_in_place_instead_of_copying_it(tmp_path):
    # The Hugging Face cache is symlinks to one blob; copying followed them and wrote the
    # 64 MB model to /tmp three times (192 MB per cold start). The model is only read.
    from app.runtime import prepare_runtime

    project, env = _fake_project(tmp_path), {}
    prepare_runtime(project, env, read_only=True, scratch_dir=tmp_path / "tmp")
    assert Path(env["FASTEMBED_CACHE_PATH"]) == project / ".fastembed_cache"
    assert env["HF_HUB_OFFLINE"] == "1"
    assert not (tmp_path / "tmp" / "kbn" / "fastembed_cache").exists()


def test_prepare_runtime_without_a_bundled_model_downloads_to_tmp(tmp_path):
    from app.runtime import prepare_runtime

    project, env = _fake_project(tmp_path), {}
    (project / ".fastembed_cache").rmdir()
    prepare_runtime(project, env, read_only=True, scratch_dir=tmp_path / "tmp")
    assert Path(env["FASTEMBED_CACHE_PATH"]) == tmp_path / "tmp" / "kbn" / "fastembed_cache"
    assert "HF_HUB_OFFLINE" not in env


@pytest.mark.skipif(not (ROOT / ".fastembed_cache").exists(), reason="model not bundled (run vercel_build.py)")
def test_embedding_works_from_a_read_only_bundled_model(tmp_path):
    import shutil

    bundle = tmp_path / "bundle"
    shutil.copytree(ROOT / ".fastembed_cache", bundle, symlinks=True)
    subprocess.run(["chmod", "-R", "a-w", str(bundle)], check=True)
    code = "import embeddings; print(len(embeddings.embed('hello')))"
    env = {**os.environ, "FASTEMBED_CACHE_PATH": str(bundle), "HF_HUB_OFFLINE": "1", "KBN_EMBEDDING_PROVIDER": "onnx"}
    try:
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    finally:
        subprocess.run(["chmod", "-R", "u+w", str(bundle)], check=True)
    assert out.returncode == 0 and out.stdout.strip().splitlines()[-1] == "384", out.stderr[-600:]


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


def test_the_tmp_copy_is_writable_even_when_the_bundle_is_read_only(tmp_path):
    # Found by running the app against a locked-down bundle: copytree preserved the
    # read-only modes, so ChromaDB could not write to its own copy.
    from app.runtime import prepare_runtime

    project, env = _fake_project(tmp_path), {}
    subprocess.run(["chmod", "-R", "a-w", str(project)], check=True)
    try:
        prepare_runtime(project, env, read_only=True, scratch_dir=tmp_path / "tmp")
        store = Path(env["KBN_CHROMA_DIR"])
        assert os.access(store, os.W_OK) and os.access(store / "chroma.sqlite3", os.W_OK)
        (store / "new.file").write_text("ok")  # directories are writable too
    finally:
        subprocess.run(["chmod", "-R", "u+w", str(project)], check=True)


def test_vercel_defaults_fit_inside_the_function_time_limit(tmp_path):
    from app.runtime import prepare_runtime

    env: dict = {}
    prepare_runtime(_fake_project(tmp_path), env, read_only=True, scratch_dir=tmp_path / "tmp")
    assert int(env["KBN_REQUEST_TIMEOUT_SECONDS"]) < 60  # vercel.json gives the function 60 s
    env = {"KBN_REQUEST_TIMEOUT_SECONDS": "25"}
    prepare_runtime(_fake_project(tmp_path / "other"), env, read_only=True, scratch_dir=tmp_path / "tmp2")
    assert env["KBN_REQUEST_TIMEOUT_SECONDS"] == "25"  # explicit settings win

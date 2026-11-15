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
    (project / ".embedding_model").mkdir()
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
    assert Path(env["KBN_EMBEDDING_MODEL_DIR"]) == project / ".embedding_model"
    assert env["HF_HUB_OFFLINE"] == "1"
    assert not (tmp_path / "tmp" / "kbn" / "fastembed_cache").exists()


def test_prepare_runtime_without_a_bundled_model_downloads_to_tmp(tmp_path):
    from app.runtime import prepare_runtime

    project, env = _fake_project(tmp_path), {}
    (project / ".embedding_model").rmdir()
    prepare_runtime(project, env, read_only=True, scratch_dir=tmp_path / "tmp")
    assert Path(env["FASTEMBED_CACHE_PATH"]) == tmp_path / "tmp" / "kbn" / "fastembed_cache"
    assert "HF_HUB_OFFLINE" not in env and "KBN_EMBEDDING_MODEL_DIR" not in env


MODEL_DIR = ROOT / ".embedding_model"


@pytest.mark.skipif(not MODEL_DIR.exists(), reason="model not bundled (run backend/scripts/vercel_build.py)")
def test_bundled_model_is_plain_files_and_small():
    # The Hugging Face cache layout is symlinks to one blob. Vercel counted the 65 MB model
    # three times (192 MB) and the bundle reached 588.72 MB against a 500 MB limit.
    files = [p for p in MODEL_DIR.rglob("*")]
    assert not [p for p in files if p.is_symlink()], "symlinks get counted once per link"
    assert sum(p.stat().st_size for p in files if p.is_file()) < 100 * 1024 * 1024


@pytest.mark.skipif(not MODEL_DIR.exists(), reason="model not bundled (run backend/scripts/vercel_build.py)")
def test_embedding_works_from_a_read_only_bundled_model(tmp_path):
    import shutil

    bundle = tmp_path / "bundle"
    shutil.copytree(MODEL_DIR, bundle)
    subprocess.run(["chmod", "-R", "a-w", str(bundle)], check=True)
    code = "import embeddings; print(len(embeddings.embed('hello')))"
    env = {**os.environ, "KBN_EMBEDDING_MODEL_DIR": str(bundle), "HF_HUB_OFFLINE": "1",
           "KBN_EMBEDDING_PROVIDER": "onnx", "FASTEMBED_CACHE_PATH": str(tmp_path / "unused")}
    try:
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    finally:
        subprocess.run(["chmod", "-R", "u+w", str(bundle)], check=True)
    assert out.returncode == 0 and out.stdout.strip().splitlines()[-1] == "384", out.stderr[-600:]
    assert not [f for f in (tmp_path / "unused").rglob("*") if f.is_file()]  # nothing was downloaded


def test_flatten_model_replaces_symlinks_with_real_files(tmp_path):
    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    try:
        import vercel_build
    finally:
        sys.path.pop(0)
    cache = tmp_path / "cache" / "models--org--m"
    (cache / "blobs").mkdir(parents=True)
    (cache / "blobs" / "abc").write_bytes(b"onnx-bytes")
    snap = cache / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    (snap / "model.onnx").symlink_to(Path("../../blobs/abc"))
    (snap / "config.json").write_text("{}")
    dest = vercel_build.flatten_model(tmp_path / "cache", tmp_path / "out")
    assert sorted(p.name for p in dest.iterdir()) == ["config.json", "model.onnx"]
    assert (dest / "model.onnx").read_bytes() == b"onnx-bytes"
    assert not any(p.is_symlink() for p in dest.iterdir())


def test_remove_node_modules_frees_the_bundle(tmp_path):
    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    try:
        import vercel_build
    finally:
        sys.path.pop(0)
    (tmp_path / "frontend" / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "frontend" / "dist").mkdir()
    vercel_build.remove_node_modules(tmp_path)
    assert not (tmp_path / "frontend" / "node_modules").exists() and (tmp_path / "frontend" / "dist").exists()


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


def test_build_script_can_build_only_the_model_or_only_the_frontend(monkeypatch):
    # In a Vercel "services" setup the website and the API build separately.
    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    try:
        import vercel_build
    finally:
        sys.path.pop(0)
    calls: list[str] = []
    monkeypatch.setattr(vercel_build, "build_frontend", lambda: calls.append("frontend"))
    monkeypatch.setattr(vercel_build, "bundle_model", lambda: calls.append("model"))
    vercel_build.main([])
    vercel_build.main(["--model-only"])
    vercel_build.main(["--frontend-only"])
    assert calls == ["frontend", "model", "model", "frontend"]


@pytest.mark.skipif(__import__("shutil").which("uv") is None, reason="uv not installed")
def test_the_project_builds_without_package_discovery_errors(tmp_path):
    # Vercel's Python runtime runs uv, which builds this repo as a wheel before installing it.
    # setuptools' auto-discovery aborts on our flat layout ("Multiple top-level packages
    # discovered": backend, frontend, knowledge_docs, local_vector_kb) unless disabled.
    out = subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path)], cwd=ROOT,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-700:]


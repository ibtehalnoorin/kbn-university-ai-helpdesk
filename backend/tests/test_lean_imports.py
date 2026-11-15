"""The production image must run without the optional web-scraping stack."""

import importlib
import sys


def test_helpers_import_without_crawl4ai(monkeypatch):
    monkeypatch.setitem(sys.modules, "crawl4ai", None)  # makes `import crawl4ai` fail
    sys.modules.pop("helpers", None)
    module = importlib.import_module("helpers")
    assert module.classify_intent("who is the vice chancellor")[0]

"""Crawl the university's own website into ``knowledge_docs/`` for the helpdesk.

    python backend/scripts/crawl_site.py            # fetch, clean, write site_*.md
    python backend/scripts/reindex.py               # then rebuild the vector index

Only information pages are fetched. Examination-result, admitted-student, archive and similar
pages are skipped on purpose: they list named individuals and are mostly out of date, and a public
chatbot must not expose them. Menu text repeated on most pages is removed so it does not drown
the real content.
"""

from __future__ import annotations

import html
import re
import sys
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "knowledge_docs"
SITE = "https://www.kbn.university"
USER_AGENT = "KBN-Helpdesk-Indexer/1.0 (+official university helpdesk)"

# Anything matching is never crawled (results, named students, archives, question papers, media).
_DENY = re.compile(
    r"result|sem(?:ester)?\b|-sem|sem-|reval|supp|instant|promoted|"
    r"exam-(?:nov|dec|jan|feb|mar|apr|may|jun|jul|aug|sep|oct)|course-work-exam|phase-(?:i|ii|iii)|"
    r"hallticket|students?-admitted|admitted|archive|question|blog|gallery|webinar|career|"
    r"^/home\d|^/items-|online-form|downloads|jan-feb|june-july|march-april|"
    r"^/be\d|^/be-|^/ba-bsc|^/ballb-|^/bsc|^/b-e-|^/b-ed-|^/b-sc-ott|diploma-in-arabic-jan",
    re.I,
)


def is_allowed(path: str) -> bool:
    path = "/" + path.strip("/")
    return not _DENY.search(path)


_BLOCK = {"p", "div", "li", "tr", "br", "section", "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th"}
_SKIP = {"script", "style", "noscript", "svg", "head", "template"}


class _Extractor(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base, self.lines, self._buf, self._skip, self._heading = base, [], [], 0, 0
        self._href: str | None = None
        self._anchor: list[str] = []

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            self.lines.append(("#" * self._heading + " " if self._heading else "") + text)

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in _BLOCK:
            self._flush()
        if re.fullmatch(r"h[1-6]", tag):
            self._heading = int(tag[1])
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor = []

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "a" and self._href is not None:
            label = re.sub(r"\s+", " ", "".join(self._anchor)).strip()
            href = urljoin(self.base, self._href)
            if label and href.startswith(("http://", "https://", "mailto:", "tel:")) and not href.startswith("mailto:") or href.startswith(("mailto:", "tel:")):
                self._buf.append(f"[{label}]({href})" if label else href)
            else:
                self._buf.append(label)
            self._href = None
            self._anchor = []
            # the anchor text was collected separately; do not add it twice
            return
        if tag in _BLOCK or re.fullmatch(r"h[1-6]", tag):
            self._flush()
            if re.fullmatch(r"h[1-6]", tag):
                self._heading = 0

    def handle_data(self, data):
        if self._skip:
            return
        if self._href is not None:
            self._anchor.append(data)
        else:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def html_to_lines(page: str, base: str) -> list[str]:
    parser = _Extractor(base)
    parser.feed(page)
    parser.close()
    out, seen = [], set()
    for line in parser.lines:
        line = html.unescape(line).replace("​", "").strip()
        if len(line) < 2 or line in seen:
            continue
        seen.add(line)
        out.append(line)
    return out


def drop_boilerplate(pages: dict[str, list[str]], threshold: float = 0.25) -> dict[str, list[str]]:
    """Remove lines that appear on at least ``threshold`` of the pages (menus, footers)."""
    if len(pages) < 4:
        return pages
    counts = Counter(line for lines in pages.values() for line in set(lines))
    limit = max(2, threshold * len(pages))
    return {url: [l for l in lines if counts[l] < limit] for url, lines in pages.items()}


def file_name_for(url: str) -> str:
    path = urlsplit(url).path.strip("/")
    return "site_" + (re.sub(r"[^A-Za-z0-9._-]+", "_", path) or "home") + ".md"


def to_document(url: str, lines: list[str]) -> str:
    return f"# Source: {url}\n\n" + "\n".join(lines) + "\n"


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def sitemap_urls() -> list[str]:
    xml = _get(f"{SITE}/pages-sitemap.xml")
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    return sorted({u for u in urls if is_allowed(urlsplit(u).path)} | {SITE + "/"})


def main() -> None:
    urls = sitemap_urls()
    print(f"{len(urls)} information pages selected")

    def fetch(url: str):
        for attempt in range(3):
            try:
                return url, html_to_lines(_get(url), url)
            except Exception as exc:  # network hiccup: retry, then skip
                time.sleep(1 + attempt)
                last = exc
        print(f"  skipped {url}: {last}")
        return url, None

    with ThreadPoolExecutor(max_workers=5) as pool:
        pages = {u: lines for u, lines in pool.map(fetch, urls) if lines}
    pages = drop_boilerplate(pages)
    DOCS.mkdir(exist_ok=True)
    for old in DOCS.glob("site_*.md"):
        old.unlink()
    written = 0
    for url, lines in pages.items():
        if sum(len(l) for l in lines) < 80:  # nothing left after cleaning
            continue
        (DOCS / file_name_for(url)).write_text(to_document(url, lines), encoding="utf-8")
        written += 1
    print(f"wrote {written} documents to {DOCS}")


if __name__ == "__main__":
    sys.exit(main())

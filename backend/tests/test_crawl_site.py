"""Site crawler: which pages are fetched, how HTML becomes text, how menus are removed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import crawl_site  # noqa: E402


@pytest.mark.parametrize("path", ["/admissions", "/fee-structure", "/mbbs", "/faculty-of-law", "/vice-chancellor",
                                  "/anti-ragging", "/student-grievance-redressal-committe", "/computer-science-and-engineering",
                                  "/ph-d-computer-science-engineering", "/m-sc-biotechnology", "/amenities", "/about"])
def test_university_information_pages_are_crawled(path):
    assert crawl_site.is_allowed(path)


@pytest.mark.parametrize("path", ["/be-iii-sem-jan-feb-2024", "/ba-bsc-bcom-ist-sem-oct-2021", "/b-ed-ist-and-iiird-sem-result-sept-2021",
                                  "/mbbs-phase-i-august-2024", "/phd-course-work-exam-nov-2023", "/entrance-hallticket",
                                  "/students-admitted-1", "/blog", "/gallery", "/career-archive", "/engineering-question-bank",
                                  "/admissions-archive", "/notifications-circulars-archive", "/home1", "/items-1", "/online-form",
                                  "/model-question-papers", "/be1-2-jan-feb-2024", "/bscopto-ott-june-july2023"])
def test_results_student_lists_and_archives_are_never_crawled(path):
    # Result and admitted-student pages list named individuals; the helpdesk must not expose them.
    assert not crawl_site.is_allowed(path)


def test_html_becomes_markdown_text_without_scripts_or_styles():
    html = """<html><head><style>.x{color:red}</style><script>var secret=1;</script></head><body>
      <nav><a href='/a'>Menu item</a></nav>
      <h1>Fee Structure</h1><p>The <b>B.E.</b> fee is listed below.</p>
      <h3>Notes</h3><ul><li>Pay by DD</li><li>See <a href='/contact'>Contact</a></li></ul>
      <script>alert(1)</script></body></html>"""
    lines = crawl_site.html_to_lines(html, base="https://www.kbn.university/fee-structure")
    text = "\n".join(lines)
    assert "# Fee Structure" in text and "### Notes" in text
    assert "The B.E. fee is listed below." in text
    assert "Pay by DD" in text and "[Contact](https://www.kbn.university/contact)" in text
    assert "secret" not in text and "alert" not in text and "color:red" not in text


def test_menu_lines_repeated_on_most_pages_are_dropped():
    menu = ["About KBNU", "Administration", "Academics"]
    pages = {f"https://x/p{i}": [*menu, f"Unique content {i}", "Shared footer note"] for i in range(10)}
    pages["https://x/p0"].append("Only on p0")
    cleaned = crawl_site.drop_boilerplate(pages, threshold=0.5)
    assert cleaned["https://x/p3"] == ["Unique content 3"]
    assert "Only on p0" in cleaned["https://x/p0"]


def test_boilerplate_is_not_dropped_from_tiny_crawls():
    pages = {"https://x/a": ["Same line"], "https://x/b": ["Same line"]}
    assert crawl_site.drop_boilerplate(pages, threshold=0.5) == pages  # too few pages to judge


def test_file_name_comes_from_the_url_path():
    assert crawl_site.file_name_for("https://www.kbn.university/fee-structure") == "site_fee-structure.md"
    assert crawl_site.file_name_for("https://www.kbn.university/") == "site_home.md"
    assert crawl_site.file_name_for("https://www.kbn.university/a/b?x=1") == "site_a_b.md"


def test_document_has_the_source_header_the_retrieval_layer_expects():
    doc = crawl_site.to_document("https://www.kbn.university/mbbs", ["# MBBS", "Duration 5.5 years"])
    assert doc.startswith("# Source: https://www.kbn.university/mbbs\n\n")
    assert doc.endswith("Duration 5.5 years\n")

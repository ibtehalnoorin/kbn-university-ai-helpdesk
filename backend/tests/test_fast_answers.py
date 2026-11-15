"""Unit tests for the deterministic, record-grounded fast-answer extractor.

These tests use literal excerpts of the public KBN University pages so they are
stable without ChromaDB or Ollama. The extractor is only allowed to copy facts
that appear in the supplied text; anything else must return ``None`` so the
normal LLM path can run.
"""

from __future__ import annotations

from app.services.fast_answers import try_fast_answer
from app.services.grounding import verify_answer

CONTACT = """
# Khaja Bandanawaz University (KBN University) Location and Contact Details
**Location:** Khaja Bandanawaz University is located in Kalaburagi (Gulbarga), Karnataka, India.
### Administrative Office Address
Khaja Bandanawaz University
Administrative Building,
Khaja Bandanawaz University Campus,
Rauza-i Buzurg, KALABURAGI - 585104.
Tel : 08472 - 245555, 236041, 228540,
224591, 267657.
Fax: 08472 - 271111
Email : contact@kbn.university

### Prof. Ali Raza Moosvi
### The Vice Chancellor
vc@kbn.university
08472 - 245555,236041,228540

### Prof. Syed Ashfaq Ahmed
### Pro Vice Chancellor
pvc@kbn.university
08472 - 245555,236041,228540
"""

COURSES = """
###### Details of Academic Programs
UG Courses
PG Courses
##### Faculty of Medical Sciences
[MBBS](https://www.kbn.university/mbbs)
B.Sc. in Optometry
##### Faculty of Engineering & Technology
Civil Engineering
Computer Science and Engineering
"""

HOME = """
2018
Established
8+
Faculties
#27
National Rank (IIRF)
#13
State Rank
### Educating Humanity
Established 23rd August 2018
"""

ALLOWED_URLS = {"https://www.kbn.university"}


def _answer(question: str, context: str, documents: dict[str, str] | None = None):
    fast = try_fast_answer(question, context, [], documents or {})
    assert fast is not None, question
    supported, reason = verify_answer(fast.answer, context, ALLOWED_URLS)
    assert supported, reason
    return fast


def test_vice_chancellor_name_is_extracted_not_invented() -> None:
    fast = _answer("Who is the Vice Chancellor of KBN University?", CONTACT)
    assert "Prof. Ali Raza Moosvi" in fast.answer
    assert "Syed Ashfaq Ahmed" not in fast.answer


def test_vice_chancellor_email_uses_the_vc_record() -> None:
    fast = _answer("What is the email address of the Vice Chancellor?", CONTACT)
    assert "vc@kbn.university" in fast.answer


def test_location_and_contact_are_copied_from_records() -> None:
    location = _answer("Where is KBN University located?", CONTACT)
    assert "Kalaburagi" in location.answer

    contact = _answer("What is the official contact information?", CONTACT)
    assert "contact@kbn.university" in contact.answer
    assert "08472 - 245555" in contact.answer
    assert "+91" not in contact.answer


def test_establishment_and_ranking_come_from_home_page() -> None:
    established = _answer("When was KBN University established?", HOME)
    assert "23rd August 2018" in established.answer

    ranking = _answer("What is the ranking of KBN University?", HOME)
    assert "#27" in ranking.answer


def test_catalogue_answers_are_built_from_source_text() -> None:
    faculties = _answer(
        "Which faculties does KBN University have?",
        COURSES,
        {"kbn_clean_page_2.md": COURSES},
    )
    assert "Faculty of Medical Sciences" in faculties.answer
    assert "Faculty of Engineering & Technology" in faculties.answer

    courses = _answer(
        "What courses does KBN University offer?",
        COURSES,
        {"kbn_clean_page_2.md": COURSES},
    )
    assert "MBBS" in courses.answer
    assert "Computer Science and Engineering" in courses.answer


def test_unsupported_questions_fall_through_to_the_llm() -> None:
    assert try_fast_answer("Who is the Dean of the Law faculty?", CONTACT, [], {}) is None
    assert try_fast_answer("What is the hostel fee?", CONTACT, [], {}) is None
    assert try_fast_answer("Tell me a poem about the monsoon.", CONTACT, [], {}) is None
    assert try_fast_answer("What is the Vice Chancellor's phone number?", "", [], {}) is None

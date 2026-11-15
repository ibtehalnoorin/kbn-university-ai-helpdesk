"""Tests for scope detection, retrieval assessment and answer verification."""

from __future__ import annotations

import pytest

from app.services.grounding import (
    FALLBACK_MESSAGE,
    FALLBACK_SENTENCE,
    assess_retrieval,
    build_retrieval_query,
    grounding_policy,
    is_followup,
    is_refusal,
    is_university_query,
    term_present,
    verify_answer,
)

VC_CONTEXT = (
    "[KB Snippet 1]: Prof. Ali Raza Moosvi\n### The Vice Chancellor\n"
    "Email : vc@kbn.university\n08472 - 245555,236041,228540\n"
    "[KB Snippet 2]: The Registrar is Mir Vilayat Ali, registrar@kbn.university. "
    "Khaja Bandanawaz University is located in Kalaburagi, Karnataka. "
    "Established 23rd August 2018. 8+ Faculties. National Rank (IIRF) #27."
)
COURSES_CONTEXT = (
    "[KB Snippet 1]: Details of Academic Programs. UG Courses. "
    "Faculty of Medical Sciences: MBBS, B.Sc. in Optometry. "
    "Faculty of Engineering & Technology: Civil Engineering, Mechanical Engineering, "
    "Computer Science and Engineering, Aeronautical Engineering. Faculty of Law: LL.B."
)


@pytest.mark.parametrize(
    "query",
    [
        "Who is the Vice Chancellor of KBN University?",
        "What is the email address of the Vice Chancellor?",
        "Which faculties and departments does KBN University have?",
        "What courses are offered at KBN University?",
        "Where is KBN University located?",
        "When was KBN University established and how is it ranked?",
    ],
)
def test_university_questions_are_in_scope(query: str) -> None:
    assert is_university_query(query) is True


def test_general_questions_are_not_university_scoped() -> None:
    assert is_university_query("What is 2 + 2?") is False
    assert is_university_query("Write a short poem about the sea") is False


def test_valid_university_questions_pass_the_retrieval_guard() -> None:
    assert assess_retrieval(
        "Who is the Vice Chancellor of KBN University?",
        "Who is the Vice Chancellor of KBN University?",
        VC_CONTEXT,
    ).allowed


def test_course_question_passes_when_courses_are_present() -> None:
    decision = assess_retrieval(
        "What courses are offered at KBN University?",
        "What courses are offered at KBN University?",
        COURSES_CONTEXT,
    )
    assert decision.allowed, decision.reason


@pytest.mark.parametrize(
    ("query", "reason_fragment"),
    [
        ("Who is the Vice Chancellor of KBN University in 2027?", "2027"),
        ("What is the email of Prof. Zayn Fake at KBN University?", "Fake"),
        (
            "What is the admission process for the Department of Quantum Basket Weaving?",
            "Quantum",
        ),
        ("Does KBN University offer Astrobiology?", "Astrobiology"),
        ("What are the hostel fees at KBN University?", "fees"),
        ("Who is the HOD of the Department of Hoverboards?", "Hoverboards"),
    ],
)
def test_hallucination_questions_are_blocked(query: str, reason_fragment: str) -> None:
    decision = assess_retrieval(query, query, VC_CONTEXT)
    assert decision.allowed is False
    assert reason_fragment.lower() in decision.reason.lower()


def test_empty_context_is_blocked() -> None:
    decision = assess_retrieval("Who is the VC?", "Who is the VC?", "   ")
    assert decision.allowed is False


def test_answer_with_supported_details_is_accepted() -> None:
    answer = "The Vice Chancellor is Prof. Ali Raza Moosvi. Email: vc@kbn.university."
    ok, reason = verify_answer(answer, VC_CONTEXT)
    assert ok, reason


def test_real_model_answer_with_citation_is_accepted() -> None:
    answer = (
        "The Vice Chancellor of KBN University is **Prof. Ali Raza Moosvi**. "
        "This information is explicitly stated in the retrieved context. "
        '[KB Snippet 1]: "### The Vice Chancellor" '
        "Email: vc@kbn.university, phone 08472 - 245555."
    )
    ok, reason = verify_answer(answer, VC_CONTEXT)
    assert ok, reason


def test_dotted_initials_are_checked_as_part_of_the_name() -> None:
    context = "Dr. K.S. Srinivas Rao is the Dean of the Faculty of Commerce & Management."
    assert verify_answer("The Dean is Dr. K.S. Srinivas Rao.", context)[0] is True
    assert verify_answer("The Dean is Dr. K.S. Fake Rao.", context)[0] is False


def test_invented_surname_after_a_real_first_name_is_rejected() -> None:
    ok, reason = verify_answer("The Vice Chancellor is Prof. Ali Zayn.", VC_CONTEXT)
    assert ok is False
    assert "name" in reason.lower()


def test_answer_with_invented_email_is_rejected() -> None:
    answer = "The Vice Chancellor is Prof. Ali Raza Moosvi, email vc@fake-university.com."
    ok, reason = verify_answer(answer, VC_CONTEXT)
    assert ok is False
    assert "email" in reason.lower()


def test_answer_with_invented_name_is_rejected() -> None:
    answer = "The Vice Chancellor is Prof. Zayn Fake."
    ok, reason = verify_answer(answer, VC_CONTEXT)
    assert ok is False
    assert "name" in reason.lower()


def test_answer_with_invented_year_is_rejected() -> None:
    answer = "The university was established in 1999."
    ok, reason = verify_answer(answer, VC_CONTEXT)
    assert ok is False
    assert "1999" in reason


def test_answer_with_invented_phone_is_rejected() -> None:
    answer = "Call the Vice Chancellor on +91 99999 88888."
    ok, reason = verify_answer(answer, VC_CONTEXT)
    assert ok is False
    assert "phone" in reason.lower()


def test_model_refusal_is_accepted_and_detected() -> None:
    answer = f"{FALLBACK_SENTENCE} Please check the official website."
    assert is_refusal(answer) is True
    ok, _ = verify_answer(answer, VC_CONTEXT)
    assert ok is True


def test_public_fallback_is_exactly_the_required_sentence() -> None:
    assert FALLBACK_MESSAGE == FALLBACK_SENTENCE
    assert FALLBACK_MESSAGE == (
        "I couldn't find verified information about this in the available KBN University records."
    )


def test_empty_answer_is_rejected() -> None:
    ok, _ = verify_answer("   ", VC_CONTEXT)
    assert ok is False


def test_term_presence_handles_plurals_and_punctuation() -> None:
    assert term_present("faculties", "The university has 8+ Faculties.") is True
    assert term_present("vc@kbn.university", VC_CONTEXT) is True
    assert term_present("Astrobiology", VC_CONTEXT) is False


def test_followup_query_is_widened_with_previous_turn() -> None:
    assert is_followup("What about his email?") is True
    combined = build_retrieval_query(
        "What about his email?", ["Who is the Vice Chancellor of KBN University?"]
    )
    assert "Vice Chancellor" in combined
    assert "his email" in combined


def test_non_followup_query_is_unchanged() -> None:
    query = "What courses are offered at KBN University?"
    assert build_retrieval_query(query, ["Who is the Vice Chancellor?"]) == query


def test_grounding_policy_contains_exact_fallback_and_no_invention_rules() -> None:
    policy = grounding_policy()
    assert FALLBACK_SENTENCE in policy
    assert "Never invent" in policy
    assert "Retrieved Context" in policy

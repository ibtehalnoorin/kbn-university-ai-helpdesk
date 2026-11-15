"""Grounding, scope detection and answer verification.

These are application-level safeguards. They do not modify the existing
``helpers.py`` intent classifier or ``processor.py`` generation logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FALLBACK_SENTENCE = (
    "I couldn't find verified information about this in the available KBN University records."
)
# The public contract requires this exact sentence and nothing else. Extra
# "helpful" follow-up text makes an unverified answer look less deterministic
# and breaks the exact-match guarantee used by clients and tests.
FALLBACK_MESSAGE = FALLBACK_SENTENCE

# Words that make a query university-scoped even without the literal "KBN".
_UNIVERSITY_PATTERNS = [
    r"\bkbn\b",
    r"\bkbnU\b",
    r"\bkhaja\s+bandanawaz\b",
    r"\buniversity\b",
    r"\bcampus\b",
    r"\bvice[\s-]+chancellor\b",
    r"\bpro[\s-]+vice[\s-]+chancellor\b",
    r"\bregistrar\b",
    r"\bdean\b",
    r"\bhod\b",
    r"\bhead\s+of\s+(?:the\s+)?department\b",
    r"\bfaculty\b",
    r"\bfaculties\b",
    r"\bdepartment\b",
    r"\badmission\b",
    r"\badmissions\b",
    r"\bcourses?\b",
    r"\bprograms?\b",
    r"\bprogrammes?\b",
    r"\bhostel\b",
    r"\bscholarship\b",
    r"\bplacement\b",
    r"\bexam(?:ination)?s?\b",
    r"\bsyllabus\b",
    r"\bfees?\b",
    r"\btuition\b",
    r"\bkalaburagi\b",
    r"\bgulbarga\b",
    r"\bmbbs\b",
    r"\bb\.?\s?tech\b",
    r"\bb\.?\s?sc\b",
    r"\bb\.?\s?com\b",
    r"\bb\.?\s?a\b",
    r"\bll\.?\s?b\b",
    r"\bm\.?\s?d\b",
    r"\bm\.?\s?s\b",
    r"\bph\.?\s?d\b",
    r"\bvc\b",
    r"\bpvc\b",
]

_QUESTION_OR_FILLER = {
    "a", "about", "above", "after", "again", "against", "all", "also", "am", "an",
    "and", "any", "are", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "could", "did", "do", "does",
    "doing", "done", "down", "during", "each", "few", "for", "from", "further",
    "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself",
    "just", "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of",
    "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out",
    "over", "own", "please", "same", "she", "should", "so", "some", "such", "than",
    "that", "the", "their", "theirs", "them", "themselves", "then", "there", "these",
    "they", "this", "those", "through", "to", "too", "under", "until", "up", "very",
    "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "will", "with", "would", "you", "your", "yours", "yourself", "yourselves",
    "tell", "give", "show", "list", "explain", "describe", "know", "want", "need",
    "many", "much", "kind", "kinds", "type", "types", "available", "possible",
    "official", "exactly", "currently", "current", "details", "detail", "information",
    "info", "question", "answer", "regarding", "related", "concerning", "about",
}

_DOMAIN_WORDS = {
    "kbn", "kbnu", "khaja", "bandanawaz", "university", "college", "campus",
    "faculty", "faculties", "department", "departments", "course", "courses",
    "program", "programs", "programme", "programmes", "admission", "admissions",
    "fee", "fees", "tuition", "hostel", "hostels", "scholarship", "scholarships",
    "placement", "placements", "exam", "exams", "examination", "examinations",
    "semester", "semesters", "degree", "degrees", "undergraduate", "postgraduate",
    "diploma", "phd", "mbbs", "btech", "b.tech", "bsc", "b.sc", "bcom", "b.com",
    "ba", "b.a", "llb", "ll.b", "md", "m.d", "ms", "m.s", "engineering",
    "technology", "medical", "medicine", "law", "arts", "humanities", "social",
    "science", "sciences", "commerce", "management", "education", "nursing",
    "pharmacy", "sports", "religion", "vice", "chancellor", "pro", "dean", "deans",
    "registrar", "controller", "examinations", "hod", "head", "email", "emails",
    "contact", "contacts", "phone", "mobile", "number", "numbers", "address",
    "location", "located", "offer", "offers", "offered", "provide", "provides",
    "provided", "rank", "ranking", "rankings", "nirf", "iirf", "established",
    "establishment", "history", "vision", "mission", "about", "overview",
}

_EXCLUDED_PROPER_NOUNS = _DOMAIN_WORDS | _QUESTION_OR_FILLER | {
    "the", "who", "what", "where", "when", "why", "which", "how", "tell", "please",
    "does", "do", "is", "are", "can", "could", "would", "should", "may", "might",
    "professor", "prof", "dr", "mr", "ms", "mrs", "sir", "madam", "india",
    "karnataka", "kalaburagi", "gulbarga", "i", "ii", "iii", "iv", "v",
}

_SENSITIVE_GROUPS: dict[str, tuple[str, ...]] = {
    "fees": ("fee", "fees", "tuition", "cost", "costs", "charge", "charges", "price", "prices"),
    "deadline": (
        "deadline",
        "deadlines",
        "last date",
        "closing date",
        "application date",
        "admission date",
        "apply by",
    ),
    "policy": ("policy", "policies", "rule", "rules", "regulation", "regulations", "code of conduct"),
    "hostel": ("hostel", "hostels", "accommodation", "dormitory", "dormitories"),
    "scholarship": ("scholarship", "scholarships", "financial aid", "fee waiver"),
    "placement": ("placement", "placements", "recruitment", "internship", "internships"),
    "hod": ("hod", "head of department", "head of the department"),
}

_ADMISSION_DETAIL_TERMS = (
    "process",
    "procedure",
    "eligibility",
    "criteria",
    "document",
    "documents",
    "requirement",
    "requirements",
    "how to apply",
    "apply",
)

_REFUSAL_PHRASES = (
    "couldn't find verified information",
    "could not find verified information",
    "cannot find verified information",
    "can't find verified information",
    "not available in the provided",
    "not available in the retrieved",
    "no verified information",
    "not mentioned in the provided",
    "not mentioned in the available",
    "not listed in the provided",
    "not listed in the available",
    "information is not available",
    "information is not provided",
    "not enough information",
    "insufficient information",
)

_TITLED_NAME_RE = re.compile(
    r"\b(?:professor|prof\.?|dr\.?|mr\.?|ms\.?|mrs\.?)\s+"
    r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3})"
)
_ROLE_NAME_RE = re.compile(
    r"\b(?:vice\s+chancellor|pro\s+vice\s+chancellor|registrar|dean|controller\s+of\s+examinations)"
    r"\s*(?:is|was|:|-)\s*([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,4})",
    re.IGNORECASE,
)

_TITLE_TOKENS = {
    "professor", "prof", "dr", "mr", "ms", "mrs", "sir", "madam", "shri", "smt",
}
_INITIALS_TOKEN_RE = re.compile(r"^(?:[A-Z]\.)+[A-Z]?\.?$")
_NON_NAME_TOKENS = _TITLE_TOKENS | {
    "email", "emails", "phone", "mobile", "tel", "telephone", "fax", "contact",
    "address", "website", "web", "office", "department", "faculty", "university",
    "college", "kbn", "kbnu", "helpdesk", "source", "snippet", "information",
    "note", "this", "that", "these", "those", "the", "he", "she", "they", "it",
    "his", "her", "their", "its", "is", "was", "and", "or", "for", "from",
    "according", "records", "context", "answer", "location", "campus",
}


def _name_tokens(captured: str) -> list[str]:
    """Split a regex name capture, stopping at the end of the sentence.

    A title such as ``Prof.`` or a dotted initial such as ``K.S.`` may contain
    a period, but ``Moosvi. This information`` must stop at ``Moosvi`` so the
    next sentence is not mistaken for part of the name.
    """

    tokens: list[str] = []
    for raw in captured.split():
        token = raw.strip("*_()[]{};,:'\"")
        if not token:
            continue
        cleaned = token.strip(".,'-")
        if not cleaned or cleaned.lower() in _NON_NAME_TOKENS:
            continue
        tokens.append(cleaned)
        if token.endswith(".") and not _INITIALS_TOKEN_RE.match(token):
            break
    return tokens



def is_university_query(query: str) -> bool:
    text = query.lower()
    return any(re.search(pattern, text) for pattern in _UNIVERSITY_PATTERNS)


def is_followup(query: str) -> bool:
    text = query.strip().lower()
    words = re.findall(r"[a-z']+", text)
    if len(words) <= 6 and words:
        return True
    if re.match(r"^(and|what about|how about|also|tell me more|more about)\b", text):
        return True
    return bool(re.search(r"\b(it|its|he|him|his|she|her|hers|they|them|their|that|this)\b", text))


def build_retrieval_query(query: str, prior_user_messages: list[str]) -> str:
    """Add just enough session context to resolve pronoun-only follow-ups.

    The original query is still sent to the existing worker; only the embedding
    lookup is widened. This keeps the core RAG implementation untouched.
    """

    if not prior_user_messages:
        return query
    if not is_followup(query):
        return query
    previous = prior_user_messages[-1].strip()
    if not previous:
        return query
    combined = f"{previous} {query}".strip()
    return combined[:500]


def _normalise(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9@.&+'\-/\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_possessive(word: str) -> str:
    return re.sub(r"'s$", "", word)


def _is_excluded_proper_noun(token: str) -> bool:
    lowered = _strip_possessive(token).strip(".").lower()
    compact = lowered.replace(".", "")
    return (
        lowered in _EXCLUDED_PROPER_NOUNS
        or compact in _EXCLUDED_PROPER_NOUNS
        or compact in _DOMAIN_WORDS
        or compact in _QUESTION_OR_FILLER
    )


def _singular(word: str) -> str:
    word = _strip_possessive(word.strip(".,;:!?()[]{}'\"").lower())
    if word.endswith("ies") and len(word) > 4:
        return f"{word[:-3]}y"
    if word.endswith(("ches", "shes", "xes", "zes", "ses")) and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    return word


def _variants(term: str) -> set[str]:
    term = _singular(term)
    variants = {term}
    if term.endswith("y"):
        variants.add(f"{term[:-1]}ies")
    else:
        variants.add(f"{term}s")
    if term.endswith("e"):
        variants.add(f"{term}d")
    return variants


def term_present(term: str, context: str) -> bool:
    """Case/plural-insensitive word check against retrieved context."""

    context_norm = _normalise(context)
    if not term or not context_norm:
        return False

    term_norm = _normalise(term)
    if not term_norm:
        return False

    # Multi-word terms and identifiers (emails, URLs) use a substring check.
    if " " in term_norm or "@" in term_norm or "/" in term_norm:
        return term_norm in context_norm

    singular_context = " ".join(_singular(word) for word in context_norm.split())
    for variant in _variants(term_norm):
        if re.search(rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])", singular_context):
            return True
    return False


def _extract_critical_terms(query: str) -> set[str]:
    terms: set[str] = set()

    # Quoted phrases are always treated as required.
    for match in re.finditer(r'["\u201c]([^"\u201d]{3,})["\u201d]', query):
        terms.add(match.group(1).strip())

    # Years are high-risk when absent from records.
    for match in re.finditer(r"\b(?:19|20)\d{2}\b", query):
        terms.add(match.group(0))

    # Capitalised proper nouns, excluding university/domain vocabulary.
    for match in re.finditer(r"\b[A-Z][A-Za-z&'.\-]{2,}\b", query):
        token = _strip_possessive(match.group(0)).strip(".")
        if not _is_excluded_proper_noun(token):
            terms.add(token)

    # Named departments / faculties ("Department of Quantum Basket Weaving").
    for match in re.finditer(
        r"\b(?:department|faculty|school|college)\s+of\s+([A-Za-z][A-Za-z&'.\-\s]{1,80})",
        query,
        flags=re.IGNORECASE,
    ):
        phrase = re.split(
            r"[,.;?!]|\b(?:at|in|is|are|who|which|where|when)\b",
            match.group(1),
            maxsplit=1,
        )[0]
        for word in re.findall(r"[A-Za-z][A-Za-z&'.\-]{2,}", phrase):
            lowered = word.lower()
            if not _is_excluded_proper_noun(word):
                terms.add(word)

    # Person names supplied after a title.
    for match in _TITLED_NAME_RE.finditer(query):
        for token in match.group(1).split():
            token = token.strip(".,'-")
            if len(token) >= 2 and not _is_excluded_proper_noun(token):
                terms.add(token)

    # Acronyms (MBBS, IIRF, etc.), excluding the university's own names.
    for match in re.finditer(r"\b[A-Z]{2,}(?:\.[A-Z]+)*\b", query):
        token = match.group(0)
        if not _is_excluded_proper_noun(token):
            terms.add(token)

    return terms


def _anchor_terms(text: str) -> set[str]:
    words: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9&'.\-]{2,}", text):
        word = _strip_possessive(raw).lower()
        if word in _QUESTION_OR_FILLER:
            continue
        words.add(word)
    return words


def _topic_terms(text: str) -> set[str]:
    return {
        word
        for word in _anchor_terms(text)
        if word not in _DOMAIN_WORDS and word not in _QUESTION_OR_FILLER
    }


@dataclass(frozen=True)
class GroundingDecision:
    allowed: bool
    reason: str
    missing_terms: tuple[str, ...] = ()


def assess_retrieval(
    original_query: str,
    retrieval_query: str,
    context: str,
) -> GroundingDecision:
    """Decide whether retrieved context can support the question.

    This is intentionally conservative. A truthful "not found" is preferred to
    passing thin/irrelevant retrieval to the model.
    """

    if not context or not context.strip():
        return GroundingDecision(False, "No relevant KBN University context was retrieved.")

    missing: list[str] = []
    for term in sorted(_extract_critical_terms(original_query)):
        if not term_present(term, context):
            missing.append(term)
    if missing:
        return GroundingDecision(
            False,
            "The retrieved records do not mention: " + ", ".join(missing[:6]),
            tuple(missing),
        )

    query_norm = _normalise(original_query)
    context_norm = _normalise(context)

    for group, group_terms in _SENSITIVE_GROUPS.items():
        query_has_group = any(term_present(term, query_norm) for term in group_terms)
        if not query_has_group:
            continue
        if not any(term_present(term, context_norm) for term in group_terms):
            return GroundingDecision(
                False,
                f"The requested {group.replace('_', ' ')} information is not present in the records.",
            )

    if "admission" in query_norm:
        if any(term_present(term, query_norm) for term in _ADMISSION_DETAIL_TERMS):
            if not any(term_present(term, context_norm) for term in _ADMISSION_DETAIL_TERMS):
                return GroundingDecision(
                    False,
                    "Admission process/eligibility details are not present in the records.",
                )
        if re.search(r"\b(when|open|opens|opening|start|starts|begin|begins|deadline|last date|closing)\b", query_norm):
            if not re.search(
                r"\b(deadline|last date|closing date|apply by|admission open|applications open)\b",
                context_norm,
            ):
                return GroundingDecision(
                    False,
                    "Admission dates are not present in the records.",
                )

    # Allow domain vocabulary (VC, faculties, courses, contacts) but require any
    # non-domain topic word to actually appear in the retrieved records.
    topic_terms = _topic_terms(original_query)
    if topic_terms:
        if not any(term_present(term, context) for term in topic_terms):
            return GroundingDecision(
                False,
                "The retrieved records do not cover: " + ", ".join(sorted(topic_terms)[:6]),
            )

    anchors = _anchor_terms(retrieval_query) | _anchor_terms(original_query)
    if anchors and not any(term_present(term, context) for term in anchors):
        return GroundingDecision(
            False,
            "The retrieved records are not relevant to this question.",
        )

    return GroundingDecision(True, "Retrieved context covers the requested information.")


def is_refusal(answer: str) -> bool:
    lowered = answer.lower()
    return any(phrase in lowered for phrase in _REFUSAL_PHRASES)


def verify_answer(answer: str, context: str, allowed_urls: set[str] | None = None) -> tuple[bool, str]:
    """Reject answers containing identifiers/dates not present in retrieved records."""

    if not answer or not answer.strip():
        return False, "The model returned an empty answer."
    if FALLBACK_SENTENCE.lower() in answer.lower() or is_refusal(answer):
        return True, "The model reported that the information is unavailable."

    context_lower = context.lower()
    allowed_urls = allowed_urls or set()

    emails = set(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", answer))
    for email in emails:
        if email.lower() not in context_lower:
            return False, f"The answer contains an email address absent from the records: {email}"

    for raw_url in re.findall(r"https?://[^\s)\]>\"']+", answer):
        url = raw_url.rstrip(".,;:)]}")
        allowed_normalised = {item.rstrip("/") for item in allowed_urls}
        if (
            url.lower() not in context_lower
            and url.rstrip("/") not in allowed_normalised
        ):
            return False, f"The answer contains a URL absent from the records: {url}"

    context_digits = re.sub(r"\D", "", context)
    for raw_phone in re.findall(r"\+?\d[\d\s().-]{5,}\d", answer):
        digits = re.sub(r"\D", "", raw_phone)
        if len(digits) >= 6 and digits not in context_digits:
            return False, f"The answer contains a phone number absent from the records: {raw_phone}"

    for year in set(re.findall(r"\b(?:19|20)\d{2}\b", answer)):
        if year not in context:
            return False, f"The answer contains a year absent from the records: {year}"

    for match in _TITLED_NAME_RE.finditer(answer):
        tokens = _name_tokens(match.group(1))
        for token in tokens:
            if len(token) >= 2 and not term_present(token, context):
                return False, f"The answer contains a name absent from the records: {' '.join(tokens)}"

    for match in _ROLE_NAME_RE.finditer(answer):
        tokens = _name_tokens(match.group(1))
        for token in tokens:
            if len(token) >= 2 and not term_present(token, context):
                return False, f"The answer contains a name absent from the records: {' '.join(tokens)}"

    return True, "All names, contacts and dates in the answer are supported by the retrieved records."


def grounding_policy() -> str:
    return (
        "\n\nKBN UNIVERSITY HELPDESK GROUNDING POLICY (MANDATORY):\n"
        "1. For any question about KBN University (people, roles, faculties, departments, "
        "courses, fees, dates, policies, location, contacts), use ONLY the Retrieved Context.\n"
        "2. Never invent or guess a name, title, email, phone number, fee, date, policy, "
        "department, course, URL, or reference.\n"
        "3. If the Retrieved Context does not contain the answer, reply with exactly: "
        f'"{FALLBACK_SENTENCE}" Do not add any other sentence or unsupported fact.\n'
        "4. If the context only partly answers the question, state only the supported part and "
        "explicitly say that the remaining information is not available in the records.\n"
        "5. Clearly label any general knowledge that is not from the Retrieved Context as general "
        "knowledge, not official KBN University information.\n"
        "6. If retrieved sources conflict, disclose the conflict instead of inventing a resolution.\n"
        "7. Cite only [KB Snippet N] labels that appear in the context. Do not create citations. "
        "The application displays the source files separately.\n"
        "8. Answer directly and concisely. Do not use LaTeX or dollar-sign formatting."
    )


def general_policy() -> str:
    return (
        "\n\nHELPDESK ANSWER POLICY (MANDATORY):\n"
        "This question is not a request for KBN University records, so answer it using accurate "
        "general knowledge. Do not claim the answer is official KBN University information. "
        "Never invent KBN University names, contacts, fees, dates, policies, or URLs. "
        "Answer directly and concisely."
    )

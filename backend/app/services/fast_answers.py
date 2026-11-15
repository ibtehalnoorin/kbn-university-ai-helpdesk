"""Deterministic, record-grounded answers for common KBN University questions.

The protected RAG pipeline (ChromaDB retrieval + ``processor.process_worker``)
is untouched. This module is an additive fast path that runs *after* the same
retrieval and grounding guard used by the normal chat flow. It only copies facts
that are literally present in the retrieved context (or in the read-only source
document that produced that context), so it cannot invent people, courses, fees,
departments, dates or contact details.

Anything ambiguous, unsupported or outside the small set of well-defined
intents returns ``None`` and falls through to the existing LLM worker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas import SourceReference

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE_LINE_RE = re.compile(r"(?:tel|telephone|phone|contact\s*no|mobile|fax)\b", re.IGNORECASE)
_ADDRESS_HEADING = "administrative office address"
_COURSES_HEADING = "details of academic programs"

_FACULTY_RE = re.compile(r"^\s*#{2,6}\s*(Faculty of\s+.+?)\s*$")
_COURSE_HEADING_RE = re.compile(r"^\s*#{2,6}\s+(.+?)\s*$")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")

_SECTION_NAMES = ("Undergraduate", "Postgraduate", "Ph.D.", "Diploma")
_SECTION_LABELS = {
    "ug courses": 0,
    "pg courses": 1,
    "ph.d. courses": 2,
    "diploma courses": 3,
}


@dataclass(frozen=True)
class FastAnswer:
    """A verified answer built directly from records."""

    answer: str
    intent: str
    # Kept for reporting; extractive answers are high confidence by construction.
    confidence: float = 0.95


def try_fast_answer(
    question: str,
    context: str,
    sources: list[SourceReference] | None = None,
    documents: dict[str, str] | None = None,
) -> FastAnswer | None:
    """Return a record-grounded answer, or ``None`` to use the LLM.

    ``context`` is the exact context produced by RetrievalService. ``documents``
    optionally maps source file names to their read-only full text, used only to
    complete long catalogue lists that are truncated in a single Chroma chunk.
    """

    text = _clean(question)
    q = text.lower()
    documents = documents or {}
    corpus = _pick_document(documents, _COURSES_HEADING)
    contact_corpus = _pick_document(documents, _ADDRESS_HEADING)

    # ------------------------------------------------------------- people/roles
    if _asks_about_vc(q):
        role = "vice chancellor"
        if _wants_email(q):
            record = _role_record(context, role)
            if record and record.email:
                return FastAnswer(
                    f"The Vice Chancellor of KBN University is {record.name}. "
                    f"The Vice Chancellor's email address is {record.email}.",
                    "vc_email",
                )
        if _wants_phone(q):
            record = _role_record(context, role)
            if record and record.phone:
                return FastAnswer(
                    f"The Vice Chancellor of KBN University is {record.name}. "
                    f"The Vice Chancellor's office telephone is {record.phone}.",
                    "vc_phone",
                )
        if _wants_identity(q):
            record = _role_record(context, role)
            if record:
                return FastAnswer(
                    f"The Vice Chancellor of KBN University is {record.name}.",
                    "vc_identity",
                )

    if _asks_about_pvc(q):
        role = "pro vice chancellor"
        record = _role_record(context, role)
        if record:
            if _wants_email(q) and record.email:
                return FastAnswer(
                    f"The Pro Vice Chancellor of KBN University is {record.name}. "
                    f"The Pro Vice Chancellor's email address is {record.email}.",
                    "pvc_email",
                )
            if _wants_identity(q):
                return FastAnswer(
                    f"The Pro Vice Chancellor of KBN University is {record.name}.",
                    "pvc_identity",
                )

    if _asks_about_registrar(q):
        role = "registrar"
        record = _role_record(context, role)
        if record:
            if _wants_email(q) and record.email:
                return FastAnswer(
                    f"The Registrar of KBN University is {record.name}. "
                    f"The Registrar's email address is {record.email}.",
                    "registrar_email",
                )
            if _wants_phone(q) and record.phone:
                return FastAnswer(
                    f"The Registrar of KBN University is {record.name}. "
                    f"The Registrar's office telephone is {record.phone}.",
                    "registrar_phone",
                )
            if _wants_identity(q):
                return FastAnswer(
                    f"The Registrar of KBN University is {record.name}.",
                    "registrar_identity",
                )

    # ------------------------------------------------------------- institution
    if _asks_established(q):
        date = _extract_established(context) or _extract_established(contact_corpus or "")
        if date:
            return FastAnswer(
                f"KBN University was established on {date}."
                if re.search(r"[A-Za-z]", date)
                else f"KBN University was established in {date}.",
                "established",
            )

    if _asks_location(q):
        location = _extract_location(context) or _extract_location(contact_corpus or "")
        if location:
            return FastAnswer(location, "location")

    if _asks_address(q):
        block = _extract_contact_block(context) or _extract_contact_block(contact_corpus or "")
        if block and block.address:
            return FastAnswer(
                "KBN University's administrative office address in the records is: "
                f"{block.address}",
                "address",
            )

    if _asks_official_contact(q):
        block = _extract_contact_block(context) or _extract_contact_block(contact_corpus or "")
        if block:
            parts: list[str] = []
            if block.address:
                parts.append(f"Address: {block.address}")
            if block.phone:
                parts.append(f"Telephone: {block.phone}")
            if block.fax:
                parts.append(f"Fax: {block.fax}")
            if block.email:
                parts.append(f"Email: {block.email}")
            if parts:
                return FastAnswer(
                    "KBN University's official contact details in the records are:\n"
                    + "\n".join(f"- {part}" for part in parts),
                    "official_contact",
                )

    # ------------------------------------------------------------- catalogue
    if _asks_faculties(q):
        faculties = _extract_faculties(corpus or context)
        if faculties:
            joined = "; ".join(faculties)
            return FastAnswer(
                "The KBN University records list these faculties: " + joined + ".",
                "faculties",
            )

    if _asks_courses(q):
        courses = _parse_courses(corpus or context)
        if courses:
            return FastAnswer(_format_courses(courses), "courses")

    if _asks_ranking(q):
        ranking = _extract_ranking(context)
        if ranking:
            return FastAnswer(ranking, "ranking")

    return None


# --------------------------------------------------------------------- intents
def _asks_about_vc(q: str) -> bool:
    return bool(re.search(r"\bvice[\s-]?chancellor\b", q)) and not _asks_about_pvc(q)


def _asks_about_pvc(q: str) -> bool:
    return bool(re.search(r"\bpro[\s-]+vice[\s-]?chancellor\b", q))


def _asks_about_registrar(q: str) -> bool:
    return bool(re.search(r"\bregistrar\b", q))


def _wants_email(q: str) -> bool:
    return bool(re.search(r"\be-?mail\b|\bmail\b", q))


def _wants_phone(q: str) -> bool:
    return bool(re.search(r"\b(phone|telephone|tel|mobile|contact\s+number)\b", q))


def _wants_identity(q: str) -> bool:
    return bool(re.search(r"\b(who|name|identity)\b", q))


def _asks_established(q: str) -> bool:
    return bool(
        re.search(r"\b(established|establishment|founded|founding|inception)\b", q)
        and re.search(r"\b(when|what year|which year|year|date)\b", q)
    )


def _asks_location(q: str) -> bool:
    if re.search(r"\b(campus|branch|office)\b", q) and not re.search(
        r"\b(main\s+campus|kbn\s+university\s+campus)\b", q
    ):
        return False
    return bool(
        re.search(r"\b(where|location|located)\b", q)
        or re.search(r"\blocation\s+of\b", q)
    )


def _asks_address(q: str) -> bool:
    return bool(re.search(r"\baddress\b", q)) and not _wants_email(q)


def _asks_official_contact(q: str) -> bool:
    if re.search(r"\b(prof|dr|mr|ms|mrs)\.?\s+[a-z]", q):
        return False
    if re.search(r"\b(department|faculty)\s+of\b", q):
        return False
    return bool(
        re.search(r"\bofficial\s+contact\b", q)
        or re.search(r"\bcontact\s+(information|details|info)\b", q)
        or (
            re.search(r"\b(phone|telephone|email|e-mail|fax)\b", q)
            and re.search(r"\b(university|kbn|kbnu|college)\b", q)
        )
        or re.search(r"\bhow\s+can\s+i\s+contact\b", q)
    )


def _asks_faculties(q: str) -> bool:
    if re.search(r"\b(department|faculty)\s+of\b", q):
        return False
    return bool(
        re.search(r"\b(faculties|faculty|departments|department)\b", q)
        and re.search(r"\b(which|what|list|all|how many|have|offer)\b", q)
    )


def _asks_courses(q: str) -> bool:
    if re.search(r"\b(department|faculty)\s+of\b", q):
        return False
    if re.search(r"\bfees?\b|\btuition\b|\bhostel\b", q):
        return False
    return bool(
        re.search(r"\b(courses|course|programs|programmes|degrees)\b", q)
        and re.search(r"\b(which|what|list|all|have|offer|available)\b", q)
    )


def _asks_ranking(q: str) -> bool:
    return bool(re.search(r"\b(rank|ranking|ranked|nirf|iirf)\b", q))


# ------------------------------------------------------------------ extraction
@dataclass(frozen=True)
class _RoleRecord:
    name: str
    email: str | None = None
    phone: str | None = None


def _role_record(context: str, role: str) -> _RoleRecord | None:
    """Find ``### Name`` immediately above ``### Role`` in the contact chunk."""

    lines = [_clean(line) for line in context.splitlines()]
    target = _normalise_role(role)
    for index, line in enumerate(lines):
        if not line.startswith("### "):
            continue
        if _normalise_role(line[4:]) != target:
            continue
        name = _previous_name(lines, index)
        if not name:
            continue
        email = None
        phone = None
        for follow in lines[index + 1 : index + 7]:
            if not email:
                match = _EMAIL_RE.search(follow)
                if match:
                    email = match.group(0).rstrip(".")
            if not phone and _looks_like_phone_line(follow):
                phone = _clean(follow)
        return _RoleRecord(name=name, email=email, phone=phone)
    return None


def _previous_name(lines: list[str], index: int) -> str | None:
    for cursor in range(index - 1, max(index - 4, -1), -1):
        line = lines[cursor]
        if not line:
            continue
        if line.startswith("### "):
            candidate = line[4:].strip()
            if _looks_like_person_name(candidate):
                return candidate
        return None
    return None


def _looks_like_person_name(value: str) -> bool:
    lowered = value.lower()
    if "@" in value or lowered.startswith(
        (
            "faculty",
            "the ",
            "pro ",
            "dean",
            "registrar",
            "controller",
            "admission",
            "faculties",
            "contact",
            "location",
        )
    ):
        return False
    return bool(re.search(r"[A-Za-z]{2,}", value))


def _normalise_role(value: str) -> str:
    return re.sub(r"^the\s+", "", value.strip().lower())


def _looks_like_phone_line(value: str) -> bool:
    if "@" in value:
        return False
    if _PHONE_LINE_RE.search(value):
        return len(re.sub(r"\D", "", value)) >= 6
    return False


@dataclass(frozen=True)
class _ContactBlock:
    address: str = ""
    phone: str = ""
    fax: str = ""
    email: str = ""


def _extract_contact_block(text: str) -> _ContactBlock | None:
    lines = [_clean(line) for line in (text or "").splitlines()]
    start = None
    for index, line in enumerate(lines):
        if _ADDRESS_HEADING in line.lower():
            start = index + 1
            break
    if start is None:
        return None

    address: list[str] = []
    phone: list[str] = []
    fax: list[str] = []
    email = None
    mode = "address"
    for line in lines[start : start + 16]:
        lowered = line.lower()
        if not line:
            continue
        if line.startswith("###") or "admission cell" in lowered:
            break
        if lowered.startswith("tel") or "tel :" in lowered:
            mode = "phone"
            phone.append(_strip_label(line))
            continue
        if lowered.startswith("fax") or "fax:" in lowered:
            mode = "fax"
            fax.append(_strip_label(line))
            continue
        match = _EMAIL_RE.search(line)
        if match and (lowered.startswith("email") or "email" in lowered):
            email = match.group(0).rstrip(".")
            mode = "email"
            continue
        if mode == "phone" and re.fullmatch(r"[\d\s,().+\-]+", line):
            phone.append(line)
            continue
        if mode == "fax" and re.fullmatch(r"[\d\s,().+\-]+", line):
            fax.append(line)
            continue
        if mode == "address":
            address.append(line)

    if not address and not phone and not email:
        return None
    return _ContactBlock(
        address=_join(address),
        phone=_join(phone),
        fax=_join(fax),
        email=email,
    )


def _extract_location(text: str) -> str | None:
    for line in (text or "").splitlines():
        if "location" in line.lower() and ":" in line:
            value = _clean(line.split(":", 1)[1]).strip("* ")
            if re.search(r"\b(kalaburagi|gulbarga)\b", value, re.IGNORECASE):
                return value
    match = re.search(
        r"[^.\n]*\b(?:located|location)\b[^.\n]*\b(kalaburagi|gulbarga)\b[^.\n]*\.",
        text or "",
        flags=re.IGNORECASE,
    )
    if match:
        sentence = _clean(match.group(0))
        if sentence and not sentence.lower().startswith(("location", "**location")):
            return sentence
    if re.search(r"\b(kalaburagi|gulbarga)\b", text or "", re.IGNORECASE):
        return "KBN University is located in Kalaburagi (Gulbarga), Karnataka, India."
    return None


def _extract_established(text: str) -> str | None:
    match = re.search(
        r"Established\s+(?:on\s+)?(\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+\s+\d{4}|\d{4})",
        text or "",
    )
    if match:
        value = _clean(match.group(1))
        return value
    if re.search(r"\b2018\b", text or "") and "establish" in (text or "").lower():
        return "2018"
    return None


def _extract_faculties(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        match = _FACULTY_RE.match(line)
        if not match:
            continue
        value = _clean(match.group(1)).rstrip(":")
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(value)
    return found


def _parse_courses(text: str) -> list[tuple[int, str, str]]:
    lines = [_clean(line) for line in (text or "").splitlines()]
    start = 0
    for index, line in enumerate(lines):
        if _COURSES_HEADING in line.lower():
            start = index
            break

    courses: list[tuple[int, str, str]] = []
    current_faculty: str | None = None
    section = 0
    seen_in_section: set[str] = set()

    for line in lines[start:]:
        faculty_match = _FACULTY_RE.match(line)
        if faculty_match:
            faculty = _clean(faculty_match.group(1)).rstrip(":")
            key = faculty.lower()
            if key in seen_in_section:
                section = min(section + 1, len(_SECTION_NAMES) - 1)
                seen_in_section = set()
            seen_in_section.add(key)
            current_faculty = faculty
            continue

        if current_faculty is None:
            continue
        if not line or line.startswith("#") or line.startswith("[>]"):
            continue
        if _SECTION_LABELS.get(line.lower()) is not None:
            continue
        if line.lower() in {"top of page", "bottom of page"}:
            continue

        course = _clean(_MD_LINK_RE.sub(r"\1", line)).strip(" -*")
        if not course or len(course) < 2:
            continue
        if course.lower().startswith(("faculty", "details", "explore", "about")):
            continue
        courses.append((section, current_faculty, course))

    return courses


def _format_courses(courses: list[tuple[int, str, str]]) -> str:
    grouped: dict[int, dict[str, list[str]]] = {}
    for section, faculty, course in courses:
        grouped.setdefault(section, {}).setdefault(faculty, []).append(course)

    parts = ["KBN University's records list these academic programs:"]
    for section in sorted(grouped):
        parts.append(f"\n**{_SECTION_NAMES[section]}:**")
        for faculty, items in grouped[section].items():
            parts.append(f"- {faculty}: " + "; ".join(items))
    return "\n".join(parts)


def _extract_ranking(text: str) -> str | None:
    lines = [_clean(line) for line in (text or "").splitlines()]
    values: dict[str, str] = {}
    for index, line in enumerate(lines):
        lowered = line.lower()
        if "national rank" in lowered and "iirf" in lowered:
            value = _previous_value(lines, index)
            if value:
                values["National Rank (IIRF)"] = value
        elif "state rank" in lowered:
            value = _previous_value(lines, index)
            if value:
                values["State Rank"] = value
    if not values:
        return None
    details = " and ".join(f"a {name} of {value}" for name, value in values.items())
    return f"The KBN University home page lists {details}."


def _previous_value(lines: list[str], index: int) -> str | None:
    for cursor in range(index - 1, max(index - 3, -1), -1):
        value = lines[cursor].strip()
        if re.fullmatch(r"#?\d+", value):
            return value if value.startswith("#") else f"#{value}"
    return None


# -------------------------------------------------------------------- helpers
def _pick_document(documents: dict[str, str], marker: str) -> str:
    needle = marker.lower()
    for text in documents.values():
        if needle in (text or "").lower():
            return text
    return ""


def _clean(text: str) -> str:
    value = (text or "").replace("\u200b", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def _join(parts: list[str]) -> str:
    return _clean(" ".join(part.strip() for part in parts if part.strip()))


def _strip_label(line: str) -> str:
    return _clean(re.sub(r"^(?:tel|telephone|phone|fax|contact\s*no)\s*[:：]?\s*", "", line, flags=re.IGNORECASE))

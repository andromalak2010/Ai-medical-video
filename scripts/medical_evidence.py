#!/usr/bin/env python3
"""Shared types for the medical evidence search scripts.

Used by pubmed_search.py, cochrane_search.py, who_guidelines.py and
medical_search.py. Standard library only.

What lives here
- MedicalRecord: one result, with the same fields for every source.
- Evidence classification: systematic review, clinical guideline, clinical
  trial, observational study, or general publication. Each source maps its own
  publication-type field onto these five groups.
- Relevance checking: a result must match the main medical concept of the
  query, so unrelated hits are dropped and counted, not shown.
- Markdown formatting grouped by evidence type.

What it never does
- Invent authors, dates, DOIs, abstracts or recommendations. A missing field
  stays None and is left out of the output.
- Treat a newer publication as higher-quality evidence. Dates are shown and
  used only to order items inside a group, never to rank evidence.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

# Evidence groups, in the order the user asked for them.
SYSTEMATIC = "Systematic review / meta-analysis"
GUIDELINE = "Clinical guideline"
OBSERVATIONAL = "Observational study"
TRIAL = "Clinical trial"
GENERAL = "General publication"

EVIDENCE_ORDER = [SYSTEMATIC, GUIDELINE, OBSERVATIONAL, TRIAL, GENERAL]

# Abstract scope: what kind of text, if any, the record carries.
SCOPE_ABSTRACT = "abstract"  # official abstract from the source
SCOPE_SUMMARY = "summary"  # official short text from the source
SCOPE_NONE = "none"  # the source gives no abstract; nothing is generated

# ---------------------------------------------------------------- record


@dataclass
class MedicalRecord:
    source: str  # e.g. "PubMed", "Cochrane Library", "WHO IRIS", "NCBI Bookshelf"
    source_id: Optional[str]  # PMID, NBK id, repository UUID
    title: str
    authors: list[str]
    publication_date: Optional[str]  # as the source states it
    last_updated: Optional[str]  # revision date, labelled by the source in notes
    document_type: str
    evidence_type: str
    url: Optional[str]  # official link to the record
    doi: Optional[str]
    abstract: Optional[str]  # official text only
    abstract_scope: str = SCOPE_NONE
    container: Optional[str] = None  # journal, book or publisher
    relevance: float = 0.0
    relevant: bool = False
    citation: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchOutcome:
    """What one source returned for one query."""

    source: str
    query: str
    total_reported: int = 0  # count the source reports, which can exceed the shown list
    records: list[MedicalRecord] = field(default_factory=list)
    dropped_irrelevant: int = 0
    error: Optional[str] = None
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- classification

_PUBTYPE_SYSTEMATIC = ("systematic review", "meta-analysis", "meta analysis")
_PUBTYPE_GUIDELINE = ("practice guideline", "guideline")
_PUBTYPE_TRIAL = (
    "randomized controlled trial",
    "controlled clinical trial",
    "clinical trial",
    "pragmatic clinical trial",
)
_PUBTYPE_OBSERVATIONAL = (
    "observational study",
    "cohort studies",
    "case-control studies",
    "cross-sectional studies",
    "prospective studies",
    "retrospective studies",
)


def classify_pubmed(publication_types: Iterable[str]) -> str:
    """Map PubMed PublicationType values to one evidence group.

    Priority when several apply: guideline, systematic review, trial,
    observational. A record that matches none is a general publication.
    """
    types = [t.lower() for t in publication_types if t]

    def has(words: tuple[str, ...]) -> bool:
        return any(w in t for t in types for w in words)

    if has(_PUBTYPE_GUIDELINE):
        return GUIDELINE
    if has(_PUBTYPE_SYSTEMATIC):
        return SYSTEMATIC
    if has(_PUBTYPE_TRIAL):
        return TRIAL
    if has(_PUBTYPE_OBSERVATIONAL):
        return OBSERVATIONAL
    return GENERAL


def pubmed_document_type(publication_types: list[str], evidence: str) -> str:
    """The most specific publication-type label to show for a record."""
    for label in publication_types:
        low = label.lower()
        if evidence == SYSTEMATIC and any(w in low for w in _PUBTYPE_SYSTEMATIC):
            return label
        if evidence == GUIDELINE and any(w in low for w in _PUBTYPE_GUIDELINE):
            return label
        if evidence == TRIAL and any(w in low for w in _PUBTYPE_TRIAL):
            return label
        if evidence == OBSERVATIONAL and any(w in low for w in _PUBTYPE_OBSERVATIONAL):
            return label
    for label in publication_types:
        if label and label.lower() != "journal article":
            return label
    return "Journal article" if publication_types else "Publication"


_GUIDELINE_TITLE = re.compile(r"\bguidelines?\b", re.IGNORECASE)
_CLINICAL_HINT = re.compile(
    r"\b(treatment|management|prevention|diagnos[ie]s?|diagnostic|therapy|therapeutic|"
    r"clinical|care|vaccin\w*|immuni[sz]\w*|dosing|antibiotic\w*|medicines?|drugs?)\b",
    re.IGNORECASE,
)


def classify_who(dc_type: Optional[str], title: str, extra_text: str = "") -> tuple[str, str, str]:
    """Return (evidence_group, document_type, evidence_basis) for a WHO IRIS item.

    evidence_basis says where the decision came from, so the user can judge it:
    - "repository type field": the item's own dc.type value.
    - "title keywords (heuristic)": inferred from the title. This is weaker.
    """
    dtype = (dc_type or "").strip()
    low = dtype.lower()
    if "guideline" in low:
        return GUIDELINE, dtype, "repository type field"
    if "systematic review" in low or "meta-analysis" in low:
        return SYSTEMATIC, dtype, "repository type field"
    if "trial" in low:
        return TRIAL, dtype, "repository type field"
    if any(w in low for w in ("cohort", "cross-sectional", "case-control", "observational")):
        return OBSERVATIONAL, dtype, "repository type field"
    if _GUIDELINE_TITLE.search(title) and _CLINICAL_HINT.search(f"{title} {extra_text}"):
        return GUIDELINE, dtype or "Guideline (inferred from title)", "title keywords (heuristic)"
    return GENERAL, dtype or "Publication", "repository type field" if dtype else "no type given"


# ---------------------------------------------------------------- relevance

# Words that describe the kind of document, not the medical topic.
_STOP_WORDS = {
    "a", "an", "and", "or", "of", "the", "in", "for", "with", "on", "to", "by",
    "vs", "versus", "at", "from", "is", "are", "as", "about", "what", "how",
    "guideline", "guidelines", "guidance", "recommendation", "recommendations",
    "review", "reviews", "meta", "analysis", "trial", "trials", "study", "studies",
    "evidence", "update", "updates", "latest", "new",
}

# Generic clinical words. They can support a match but never anchor one:
# "acute asthma management" must still match asthma, not just "acute".
_GENERIC_TERMS = {
    "acute", "chronic", "severe", "clinical", "care", "management", "treatment",
    "treat", "therapy", "therap", "prevention", "diagnosis", "diagnostic", "adult",
    "adults", "children", "emergency", "guideline", "guidelines", "outcome", "outcomes",
}

# Common medical abbreviations and near-synonyms. Each value is a list of word
# prefixes; a term matches if a word in the text starts with any of them.
_SYNONYMS = {
    "dm": ["diabet"],
    "diabetes": ["diabet"],
    "diabetic": ["diabet"],
    "mellitus": ["mellitus", "diabet"],
    "dka": ["ketoacidosis", "diabetic ketoacidosis"],
    "ketoacidosis": ["ketoacidosis"],
    "htn": ["hypertens", "blood pressure"],
    "hypertension": ["hypertens", "blood pressure"],
    "hypertensive": ["hypertens", "blood pressure"],
    "sepsis": ["sepsis", "septic"],
    "septic": ["sepsis", "septic"],
    "asthma": ["asthma"],
    "treatment": ["treat", "therap", "manag"],
    "treat": ["treat", "therap", "manag"],
    "therapy": ["therap", "treat"],
    "management": ["manag", "treat", "therap"],
}


def _concepts(query: str) -> list[tuple[str, list[str]]]:
    """(original word, alternatives) for each medical concept in the query."""
    concepts: list[tuple[str, list[str]]] = []
    for token in re.findall(r"[a-z][a-z0-9\-]*", query.lower()):
        token = token.strip("-")
        if len(token) < 3 or token in _STOP_WORDS:
            continue
        terms = _SYNONYMS.get(token, [token])
        if all(terms != existing for _, existing in concepts):
            concepts.append((token, terms))
    return concepts


def concept_groups(query: str) -> list[list[str]]:
    """Split a query into medical concepts. Each concept is a list of alternatives."""
    return [terms for _, terms in _concepts(query)]


def require_concepts(query: str) -> None:
    """Refuse queries with no medical term, such as 'guidelines' on its own."""
    if not concept_groups(query):
        raise ValueError(
            f"Query {query!r} has no medical term to match. Add a disease, drug or clinical topic."
        )


def assess_relevance(query: str, texts: Iterable[Optional[str]]) -> tuple[float, bool]:
    """Score how well a record's text matches the query's medical concepts.

    An anchor concept (a disease or drug, not a generic word such as "acute" or
    "management") must appear. Short queries (one or two concepts) need every
    concept; longer ones need two thirds. Returns (score between 0 and 1, passed).
    """
    concepts = _concepts(query)
    if not concepts:
        return 0.0, False
    haystack = " \n".join(t for t in texts if t)
    matched = []
    for _, terms in concepts:
        hit = any(
            re.search(r"\b" + re.escape(term), haystack, re.IGNORECASE) for term in terms
        )
        matched.append(hit)
    anchors = [i for i, (word, _) in enumerate(concepts) if word not in _GENERIC_TERMS]
    if not anchors:  # every word is generic: fall back to requiring all of them
        anchors = list(range(len(concepts)))
    hits = sum(matched)
    needed = len(concepts) if len(concepts) <= 2 else math.ceil(len(concepts) * 2 / 3)
    anchor_hit = any(matched[i] for i in anchors)
    return round(hits / len(concepts), 2), bool(anchor_hit and hits >= needed)


def apply_relevance(query: str, record: MedicalRecord, extra_texts: Iterable[Optional[str]] = ()) -> None:
    """Set relevance fields on a record in place."""
    texts = [record.title, record.abstract, *extra_texts]
    record.relevance, record.relevant = assess_relevance(query, texts)


# ---------------------------------------------------------------- citation

def make_citation(
    authors: list[str],
    title: str,
    container: Optional[str],
    date: Optional[str],
    doi: Optional[str],
    url: Optional[str],
) -> str:
    """Build a citation from the fields that exist. Missing fields are left out."""
    parts: list[str] = []
    if authors:
        text = "; ".join(authors[:3]) + (", et al" if len(authors) > 3 else "")
        parts.append(_end(text))
    if title:
        parts.append(_end(title))
    if container:
        parts.append(_end(container))
    if date:
        parts.append(_end(date))
    if doi:
        parts.append(f"doi:{doi}.")
    elif url:
        parts.append(f"Available from: {url}")
    return " ".join(parts)


def _end(text: str) -> str:
    text = text.rstrip()
    return text if text.endswith((".", "?", "!")) else text + "."


# ---------------------------------------------------------------- output

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def date_parts(text: Optional[str]) -> tuple[int, int, int]:
    """(year, month, day) from '2026 Jun 19', '2026-11-28', '2026/01/01' or '2026'."""
    if not text:
        return (0, 0, 0)
    year_match = re.search(r"(\d{4})", text)
    if not year_match:
        return (0, 0, 0)
    year = int(year_match.group(1))
    rest = text[year_match.end():].lower()
    month_name = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*", rest)
    if month_name:
        month = _MONTHS[month_name.group(1)]
        after = rest[month_name.end():]
        digits = re.findall(r"\d{1,2}", after)
    else:
        digits = re.findall(r"\d{1,2}", rest)
        month = int(digits[0]) if digits and 1 <= int(digits[0]) <= 12 else 0
        digits = digits[1:] if month else digits
    day = int(digits[0]) if digits and 1 <= int(digits[0]) <= 31 else 0
    return (year, month, day)


def sort_key_newest_first(record: MedicalRecord) -> tuple[int, int, int]:
    """Display order inside a group: newest first. Only a tie-breaker, never quality."""
    return date_parts(record.publication_date)


def group_by_evidence(records: list[MedicalRecord]) -> dict[str, list[MedicalRecord]]:
    """Group records by evidence type. Keeps the incoming order (relevance first)."""
    groups: dict[str, list[MedicalRecord]] = {name: [] for name in EVIDENCE_ORDER}
    for record in records:
        groups.setdefault(record.evidence_type, []).append(record)
    return {name: items for name, items in groups.items() if items}


def record_lines(index: int, record: MedicalRecord) -> list[str]:
    """Markdown lines for one record. Only fields that exist are printed."""
    lines = [f"{index}. **{record.title or '(untitled)'}**  _({record.document_type})_"]
    if record.authors:
        lines.append(f"   - Authors: {'; '.join(record.authors)}")
    if record.container:
        lines.append(f"   - Source: {record.container}")
    if record.publication_date:
        lines.append(f"   - Published: {record.publication_date}")
    if record.last_updated:
        lines.append(f"   - Last updated: {record.last_updated}")
    if record.doi:
        lines.append(f"   - DOI: {record.doi}")
    if record.url:
        lines.append(f"   - Official link: {record.url}")
    if record.abstract:
        label = "Official abstract" if record.abstract_scope == SCOPE_ABSTRACT else "Official summary"
        lines.append(f"   - {label}: {record.abstract}")
    else:
        lines.append("   - Abstract: not available from this source")
    for note in record.notes:
        lines.append(f"   - Note: {note}")
    lines.append(f"   - Citation: {record.citation}")
    return lines


def format_records_markdown(title: str, outcome_notes: list[str], records: list[MedicalRecord]) -> str:
    lines = [f"# {title}", ""]
    lines.extend(f"> {note}" for note in outcome_notes)
    if outcome_notes:
        lines.append("")
    if not records:
        lines.append("No relevant records returned.")
        return "\n".join(lines)
    for evidence, items in group_by_evidence(records).items():
        lines.append(f"## {evidence} ({len(items)})")
        lines.append("")
        for index, record in enumerate(items, start=1):
            lines.extend(record_lines(index, record))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"

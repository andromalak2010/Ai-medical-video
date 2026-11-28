#!/usr/bin/env python3
"""Search four medical evidence sources with one command.

Sources
- PubMed            (pubmed_search.py)      NCBI E-utilities, db=pubmed
- NCBI Bookshelf    (ncbi_bookshelf.py)     NCBI E-utilities, db=books
- Cochrane Library  (cochrane_search.py)    Cochrane reviews, via NCBI PubMed records
- WHO IRIS          (who_guidelines.py)     WHO publications and guidelines, DSpace REST

Each source runs on its own. If one source fails (network, proxy, rate limit),
the others still return results, and the failure is shown in its section.

Output is grouped by source, then by evidence type. Inside each group, items
are listed newest first. That order is a display choice and does not rank
evidence quality: a newer publication is not treated as higher-quality.

Usage
    python3 scripts/medical_search.py "diabetic ketoacidosis" --max 5
    python3 scripts/medical_search.py "sepsis guidelines" --sources who,cochrane
    python3 scripts/medical_search.py "hypertension treatment" --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Callable, Optional

import medical_evidence as ev
import cochrane_search as cs
import who_guidelines as who
from ncbi_bookshelf import EutilsClient, NCBIError, search_bookshelf
from pubmed_search import CANDIDATE_MULTIPLIER, PubMedClient, search_pubmed

ALL_SOURCES = ["pubmed", "bookshelf", "cochrane", "who"]
SOURCE_LABELS = {
    "pubmed": "PubMed",
    "bookshelf": "NCBI Bookshelf",
    "cochrane": "Cochrane Library",
    "who": "WHO IRIS",
}


@dataclass
class Clients:
    pubmed: PubMedClient
    bookshelf: EutilsClient
    iris: who.IrisClient


def make_clients() -> Clients:
    api_key = os.environ.get("NCBI_API_KEY")
    email = os.environ.get("NCBI_TOOL_EMAIL")
    return Clients(
        pubmed=PubMedClient(api_key=api_key, tool_email=email),
        bookshelf=EutilsClient(api_key=api_key, tool_email=email),
        iris=who.IrisClient(),
    )


# ---------------------------------------------------------------- Bookshelf adapter


def bookshelf_outcome(client: EutilsClient, query: str, max_results: int) -> ev.SearchOutcome:
    """Run the existing Bookshelf search and convert it to the shared record type."""
    outcome = ev.SearchOutcome(source="NCBI Bookshelf", query=query)
    pool = min(max_results * CANDIDATE_MULTIPLIER, 100)
    total, items = search_bookshelf(client, query, pool)
    outcome.total_reported = total
    records: list[ev.MedicalRecord] = []
    for item in items:
        kind = {"book": "Book", "chapter": "Book chapter", "section": "Book section"}.get(
            item.record_type, "Bookshelf record"
        )
        record = ev.MedicalRecord(
            source="NCBI Bookshelf",
            source_id=item.accession or item.uid,
            title=item.title or "(untitled)",
            authors=item.authors,
            publication_date=item.publication_date,
            last_updated=None,  # Bookshelf gives no revision date in this search
            document_type=kind,
            evidence_type=ev.GENERAL,  # textbook content, not a study design
            url=item.url,
            doi=None,
            abstract=item.summary,
            abstract_scope=ev.SCOPE_SUMMARY if item.summary else ev.SCOPE_NONE,
            container=item.book_title or item.chapter_title,
            citation=item.citation,
        )
        ev.apply_relevance(query, record, extra_texts=[item.chapter_title, item.book_title])
        records.append(record)
    outcome.dropped_irrelevant = sum(1 for r in records if not r.relevant)
    kept = [r for r in records if r.relevant]
    kept.sort(key=ev.sort_key_newest_first, reverse=True)
    kept.sort(key=lambda r: r.relevance, reverse=True)
    outcome.records = kept[:max_results]
    outcome.notes.append("Textbook and reference content: classed as a general publication.")
    return outcome


# ---------------------------------------------------------------- orchestration

Runner = Callable[[str, str, int], ev.SearchOutcome]


def _runners(clients: Clients) -> dict[str, Runner]:
    return {
        "pubmed": lambda q, _s, n: search_pubmed(clients.pubmed, q, n),
        "bookshelf": lambda q, _s, n: bookshelf_outcome(clients.bookshelf, q, n),
        "cochrane": lambda q, _s, n: cs.search_cochrane(clients.pubmed, q, n),
        "who": lambda q, _s, n: who.search_who(clients.iris, q, n),
    }


def search_all(
    query: str,
    max_results: int = 5,
    sources: Optional[list[str]] = None,
    clients: Optional[Clients] = None,
    runners: Optional[dict[str, Runner]] = None,
) -> list[ev.SearchOutcome]:
    """Search each selected source. A source failure is recorded, not raised."""
    query = query.strip()
    if not query:
        raise ValueError("Query must not be empty")
    ev.require_concepts(query)
    chosen = sources or ALL_SOURCES
    unknown = [s for s in chosen if s not in ALL_SOURCES]
    if unknown:
        raise ValueError(f"Unknown source(s): {', '.join(unknown)}. Choose from: {', '.join(ALL_SOURCES)}")
    table = runners or _runners(clients or make_clients())

    outcomes: list[ev.SearchOutcome] = []
    for key in chosen:
        label = SOURCE_LABELS[key]
        try:
            outcome = table[key](query, key, max_results)
        except (NCBIError, OSError, ValueError) as exc:
            outcome = ev.SearchOutcome(source=label, query=query, error=str(exc) or type(exc).__name__)
        outcomes.append(outcome)
    return outcomes


# ---------------------------------------------------------------- output


def format_unified(query: str, outcomes: list[ev.SearchOutcome]) -> str:
    lines = [f"# Unified medical search: {query}", ""]
    lines.append(
        "Results are grouped by source, then by evidence type. Newest first inside each group. "
        "Date order does not rank quality."
    )
    lines.append("")
    for outcome in outcomes:
        lines.append(f"## Source: {outcome.source}")
        if outcome.error:
            lines.append(f"_Unavailable: {outcome.error}_")
            lines.append("")
            continue
        lines.append(
            f"Reported {outcome.total_reported} matches; showing {len(outcome.records)}"
            + (f"; {outcome.dropped_irrelevant} dropped as unrelated." if outcome.dropped_irrelevant else ".")
        )
        for note in outcome.notes:
            lines.append(f"> {note}")
        lines.append("")
        if not outcome.records:
            lines.append("No relevant records returned.")
            lines.append("")
            continue
        for evidence, items in ev.group_by_evidence(outcome.records).items():
            lines.append(f"### {evidence} ({len(items)})")
            lines.append("")
            for index, record in enumerate(items, start=1):
                lines.extend(ev.record_lines(index, record))
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_json(query: str, outcomes: list[ev.SearchOutcome]) -> str:
    payload = {
        "query": query,
        "sources": [
            {
                "source": o.source,
                "error": o.error,
                "total_reported": o.total_reported,
                "dropped_irrelevant": o.dropped_irrelevant,
                "notes": o.notes,
                "results": [r.to_dict() for r in o.records],
            }
            for o in outcomes
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------- CLI


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Search PubMed, NCBI Bookshelf, Cochrane and WHO together.")
    parser.add_argument("query", help="Disease, drug, mechanism or clinical question")
    parser.add_argument("--max", type=int, default=5, dest="max_results",
                        help="Max results per source (default 5)")
    parser.add_argument("--sources", default=",".join(ALL_SOURCES),
                        help=f"Comma-separated subset of: {', '.join(ALL_SOURCES)}")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    sources = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
    try:
        outcomes = search_all(args.query, args.max_results, sources)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(to_json(args.query, outcomes) if args.json else format_unified(args.query, outcomes))
    return 0


if __name__ == "__main__":
    sys.exit(main())

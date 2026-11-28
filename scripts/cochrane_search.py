#!/usr/bin/env python3
"""Search Cochrane systematic reviews through the official route that needs no login.

How it works
- Cochrane Database of Systematic Reviews records are indexed in PubMed under
  the journal "Cochrane Database Syst Rev". This script runs a PubMed search
  limited to that journal (see pubmed_search.py). The titles, authors, DOIs,
  dates and abstracts are NCBI's official records.
- Each review links to its DOI (https://doi.org/...), the official Cochrane
  identifier.

What it does not do, and why
- It does not call the Cochrane API (api.cochrane.org). That API is protected
  by OAuth2 / OpenID Connect client credentials, which are issued by Cochrane.
  The token endpoint and endpoint paths are not public. See references/cochrane.md.
- It does not retrieve Plain Language Summaries or full text. Neither is
  part of the PubMed record. Nothing here bypasses a subscription.
- It never claims full-text access. Records carry only the abstract.

Official references
- Cochrane Library:            https://www.cochranelibrary.com/
- Cochrane documentation:      https://documentation.cochrane.org/
- Cochrane API docs (login):   https://api.cochrane.org/api-docs/index.html

Usage
    python3 scripts/cochrane_search.py search "diabetic ketoacidosis" --max 5
    python3 scripts/cochrane_search.py search "hypertension treatment" --json
    python3 scripts/cochrane_search.py link "sepsis"     # Cochrane Library search link only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from typing import Optional

from ncbi_bookshelf import NCBIError
import medical_evidence as ev
from pubmed_search import PubMedClient, search_pubmed

COCHRANE_LIBRARY_BASE = "https://www.cochranelibrary.com/"
DOI_BASE = "https://doi.org/"
COCHRANE_JOURNAL_FILTER = '"Cochrane Database Syst Rev"[Journal]'
SOURCE_NAME = "Cochrane Library"
CONTAINER = "Cochrane Database of Systematic Reviews"

PLS_NOTE = "Plain Language Summary not retrieved: it is not part of the PubMed record. Open the DOI link."
ABSTRACT_NOTE = "Abstract only. Full text was not retrieved."
NOT_A_REVIEW_NOTE = (
    "PubMed does not tag this Cochrane record as a systematic review. It may be a protocol "
    "or another Cochrane item. Check the DOI page before citing it as a review."
)


def cochrane_library_search_url(query: str) -> str:
    """Link to the public Cochrane Library search page. The page is not scraped."""
    return COCHRANE_LIBRARY_BASE + "search?" + urllib.parse.urlencode({"q": query.strip()})


def search_cochrane(client: PubMedClient, query: str, max_results: int = 10) -> ev.SearchOutcome:
    """Find Cochrane reviews for a query, using NCBI's official PubMed records."""
    outcome = search_pubmed(client, query, max_results, article_filter=COCHRANE_JOURNAL_FILTER)
    outcome.source = SOURCE_NAME
    outcome.notes.append(
        "Records come from PubMed, which indexes the Cochrane Database of Systematic Reviews."
    )
    for record in outcome.records:
        record.source = SOURCE_NAME
        record.container = CONTAINER
        if record.doi:
            record.url = DOI_BASE + record.doi  # the official Cochrane DOI link
        record.notes.extend([PLS_NOTE, ABSTRACT_NOTE])
        if record.evidence_type != ev.SYSTEMATIC:
            record.notes.append(NOT_A_REVIEW_NOTE)
        record.citation = ev.make_citation(
            record.authors, record.title, CONTAINER, record.publication_date, record.doi, record.url
        )
    return outcome


# ---------------------------------------------------------------- CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search Cochrane systematic reviews.")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="Search Cochrane reviews (via PubMed)")
    search.add_argument("query")
    search.add_argument("--max", type=int, default=10, dest="max_results")
    search.add_argument("--json", action="store_true")
    link = sub.add_parser("link", help="Print the Cochrane Library search link only")
    link.add_argument("query")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "link":
        print(cochrane_library_search_url(args.query))
        return 0

    client = PubMedClient(
        api_key=os.environ.get("NCBI_API_KEY"),
        tool_email=os.environ.get("NCBI_TOOL_EMAIL"),
    )
    try:
        outcome = search_cochrane(client, args.query, args.max_results)
    except (NCBIError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "query": outcome.query,
            "source": "PubMed (journal: Cochrane Database Syst Rev)",
            "total_reported": outcome.total_reported,
            "dropped_irrelevant": outcome.dropped_irrelevant,
            "cochrane_library_search": cochrane_library_search_url(outcome.query),
            "results": [r.to_dict() for r in outcome.records],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        notes = outcome.notes + [
            f"PubMed reports {outcome.total_reported} Cochrane-indexed records; showing {len(outcome.records)}.",
            f"Cochrane Library search (official page): {cochrane_library_search_url(outcome.query)}",
        ]
        if outcome.dropped_irrelevant:
            notes.append(f"{outcome.dropped_irrelevant} result(s) dropped by the relevance check.")
        print(ev.format_records_markdown(f"Cochrane Library: {outcome.query}", notes, outcome.records))
    return 0


if __name__ == "__main__":
    sys.exit(main())

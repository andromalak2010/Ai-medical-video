#!/usr/bin/env python3
"""Search PubMed through the official NCBI E-utilities API (db=pubmed).

What it does
- ESearch: finds PubMed PMIDs for a query.
- EFetch (XML): returns each PubMed record's title, authors, journal, dates,
  DOI, publication types and official abstract.
- Each result keeps its official PubMed link and a citation built only from
  fields that exist.

What it never does
- Download full text. It never asks for PMC or any full-text database.
- Scrape HTML. Only the documented E-utilities API is used.
- Generate or paraphrase abstracts, dates, authors or DOIs.

Official references
- E-utilities help / usage: https://www.ncbi.nlm.nih.gov/books/NBK25497/
- PubMed help:              https://pubmed.ncbi.nlm.nih.gov/help/

Optional environment variables (same as ncbi_bookshelf.py)
- NCBI_API_KEY      Raises the rate limit from 3 to 10 requests per second.
- NCBI_TOOL_EMAIL   Contact address NCBI may use if traffic causes problems.
                    Not set by default and never hard-coded.

Standard library only.

Usage
    python3 scripts/pubmed_search.py search "diabetic ketoacidosis" --max 5
    python3 scripts/pubmed_search.py search "sepsis" --filter "systematic review[pt]" --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable, Optional

from ncbi_bookshelf import (
    MAX_RETRIES,
    RATE_NO_KEY,
    RATE_WITH_KEY,
    RETRYABLE_HTTP,
    NCBIError,
    RateLimiter,
)
import medical_evidence as ev

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
PUBMED_BASE = "https://pubmed.ncbi.nlm.nih.gov/"
DATABASE = "pubmed"
TOOL_NAME = "ai-medical-video-pubmed"
SOURCE_NAME = "PubMed"

MAX_RESULTS_CAP = 100
PAGE_SIZE = 100
CANDIDATE_MULTIPLIER = 3  # fetch extra candidates, then keep the best relevant ones

Transport = Callable[[str], bytes]


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": TOOL_NAME})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class PubMedClient:
    """E-utilities client for db=pubmed: ESearch and EFetch only."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        tool_email: Optional[str] = None,
        transport: Optional[Transport] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self.api_key = api_key or None
        self.tool_email = tool_email or None
        rate = RATE_WITH_KEY if self.api_key else RATE_NO_KEY
        self._limiter = RateLimiter(rate, clock=clock, sleep=sleep)
        self._transport: Transport = transport or _http_get
        self._sleep = sleep
        self._max_retries = max_retries
        self.requests_made = 0

    def _get(self, endpoint: str, params: dict) -> bytes:
        full = {"db": DATABASE, "tool": TOOL_NAME, **params}
        if self.api_key:
            full["api_key"] = self.api_key
        if self.tool_email:
            full["email"] = self.tool_email
        url = EUTILS_BASE + endpoint + "?" + urllib.parse.urlencode(full)

        attempt = 0
        while True:
            self._limiter.wait()
            self.requests_made += 1
            try:
                return self._transport(url)
            except urllib.error.HTTPError as exc:  # must come before URLError
                retryable = exc.code in RETRYABLE_HTTP
                reason = f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError) as exc:
                retryable = True
                reason = type(exc).__name__
            # The URL is never included in messages: it may carry the API key.
            if not retryable or attempt >= self._max_retries:
                raise NCBIError(f"PubMed request to {endpoint} failed: {reason}")
            attempt += 1
            self._sleep(2**attempt)

    def esearch(self, term: str, retstart: int = 0, retmax: int = PAGE_SIZE) -> tuple[int, list[str]]:
        body = self._get(
            "esearch.fcgi",
            {"term": term, "retstart": retstart, "retmax": retmax, "retmode": "json"},
        )
        data = _load_json(body, "esearch.fcgi")
        result = data.get("esearchresult")
        if not isinstance(result, dict):
            raise NCBIError("PubMed ESearch response has no esearchresult object")
        if "ERROR" in result:
            raise NCBIError(f"PubMed ESearch error: {result['ERROR']}")
        try:
            count = int(result.get("count", 0))
        except (TypeError, ValueError) as exc:
            raise NCBIError("PubMed ESearch returned an unexpected count") from exc
        return count, [str(pmid) for pmid in result.get("idlist", [])]

    def efetch_xml(self, pmids: list[str]) -> bytes:
        """Full PubMed XML for these PMIDs. It carries abstracts, never full text."""
        if not pmids:
            return b""
        return self._get("efetch.fcgi", {"id": ",".join(pmids), "retmode": "xml"})


def _load_json(body: bytes, endpoint: str) -> dict:
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        raise NCBIError(f"PubMed returned non-JSON data for {endpoint}") from exc
    if not isinstance(data, dict):
        raise NCBIError(f"PubMed returned an unexpected payload for {endpoint}")
    return data


# ---------------------------------------------------------------- XML parsing


def _text(element: Optional[ET.Element]) -> Optional[str]:
    if element is None:
        return None
    value = " ".join("".join(element.itertext()).split())
    return value or None


def _author_name(author: ET.Element) -> Optional[str]:
    collective = _text(author.find("CollectiveName"))
    if collective:
        return collective
    last = _text(author.find("LastName"))
    initials = _text(author.find("Initials"))
    if last and initials:
        return f"{last} {initials}"
    return last or _text(author.find("ForeName"))


def _abstract(article: ET.Element) -> Optional[str]:
    parts: list[str] = []
    for node in article.findall("Abstract/AbstractText"):
        text = _text(node)
        if not text:
            continue
        label = node.get("Label")
        parts.append(f"{label.capitalize()}: {text}" if label else text)
    return " ".join(parts) or None


def _publication_date(article: ET.Element) -> Optional[str]:
    pub = article.find("Journal/JournalIssue/PubDate")
    if pub is None:
        return None
    medline = _text(pub.find("MedlineDate"))
    if medline:
        return medline
    pieces = [_text(pub.find(tag)) for tag in ("Year", "Month", "Day")]
    value = " ".join(p for p in pieces if p)
    return value or None


def _revised(citation: ET.Element) -> Optional[str]:
    node = citation.find("DateRevised")
    if node is None:
        return None
    year, month, day = (_text(node.find(t)) for t in ("Year", "Month", "Day"))
    if not year:
        return None
    return "-".join(p for p in (year, month, day) if p)


def _doi(pubmed_data: Optional[ET.Element], article: ET.Element) -> Optional[str]:
    if pubmed_data is not None:
        for node in pubmed_data.findall("ArticleIdList/ArticleId"):
            if node.get("IdType") == "doi" and _text(node):
                return _text(node)
    for node in article.findall("ELocationID"):
        if node.get("EIdType") == "doi" and node.get("ValidYN") != "N" and _text(node):
            return _text(node)
    return None


def parse_pubmed_xml(xml_bytes: bytes) -> list[ev.MedicalRecord]:
    """Turn EFetch XML into MedicalRecord objects. Missing fields stay None."""
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise NCBIError("PubMed returned XML that cannot be parsed") from exc

    records: list[ev.MedicalRecord] = []
    for entry in root.iter("PubmedArticle"):
        citation = entry.find("MedlineCitation")
        if citation is None:
            continue
        article = citation.find("Article")
        if article is None:
            continue
        pmid = _text(citation.find("PMID"))
        title = _text(article.find("ArticleTitle")) or "(untitled)"
        authors = [name for name in (_author_name(a) for a in article.findall("AuthorList/Author")) if name]
        journal = _text(article.find("Journal/Title")) or _text(article.find("Journal/ISOAbbreviation"))
        publication_types = [t for t in (_text(n) for n in article.findall("PublicationTypeList/PublicationType")) if t]
        evidence = ev.classify_pubmed(publication_types)
        date = _publication_date(article)
        doi = _doi(entry.find("PubmedData"), article)
        abstract = _abstract(article)
        url = f"{PUBMED_BASE}{pmid}/" if pmid else None

        record = ev.MedicalRecord(
            source=SOURCE_NAME,
            source_id=pmid,
            title=title,
            authors=authors,
            publication_date=date,
            last_updated=_revised(citation),
            document_type=ev.pubmed_document_type(publication_types, evidence),
            evidence_type=evidence,
            url=url,
            doi=doi,
            abstract=abstract,
            abstract_scope=ev.SCOPE_ABSTRACT if abstract else ev.SCOPE_NONE,
            container=journal,
            citation=ev.make_citation(authors, title, journal, date, doi, url),
        )
        if record.last_updated:
            record.notes.append("Last updated is the PubMed record revision date.")
        records.append(record)
    return records


# ---------------------------------------------------------------- search


def search_pubmed(
    client: PubMedClient,
    query: str,
    max_results: int = 10,
    article_filter: Optional[str] = None,
) -> ev.SearchOutcome:
    """Search PubMed. Results that fail the relevance check are dropped and counted."""
    query = query.strip()
    if not query:
        raise ValueError("Query must not be empty")
    ev.require_concepts(query)
    limit = max(1, min(int(max_results), MAX_RESULTS_CAP))
    term = f"({query}) AND ({article_filter})" if article_filter else query

    outcome = ev.SearchOutcome(source=SOURCE_NAME, query=query)
    pool = min(limit * CANDIDATE_MULTIPLIER, MAX_RESULTS_CAP)
    count, pmids = client.esearch(term, retmax=pool)
    outcome.total_reported = count
    pmids = pmids[:pool]

    records: list[ev.MedicalRecord] = []
    for start in range(0, len(pmids), PAGE_SIZE):
        records.extend(parse_pubmed_xml(client.efetch_xml(pmids[start : start + PAGE_SIZE])))

    for record in records:
        ev.apply_relevance(query, record)
    outcome.dropped_irrelevant = sum(1 for r in records if not r.relevant)
    kept = [r for r in records if r.relevant]
    # Newest first is only a tie-breaker; relevance is the main order.
    kept.sort(key=ev.sort_key_newest_first, reverse=True)
    kept.sort(key=lambda r: r.relevance, reverse=True)
    outcome.records = kept[:limit]
    return outcome


# ---------------------------------------------------------------- CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search PubMed via NCBI E-utilities.")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="Search PubMed")
    search.add_argument("query", help="Disease, drug, mechanism or clinical question")
    search.add_argument("--max", type=int, default=10, dest="max_results",
                        help=f"Max results (1-{MAX_RESULTS_CAP}, default 10)")
    search.add_argument("--filter", dest="article_filter",
                        help='Optional PubMed filter, e.g. "systematic review[pt]"')
    search.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    client = PubMedClient(
        api_key=os.environ.get("NCBI_API_KEY"),
        tool_email=os.environ.get("NCBI_TOOL_EMAIL"),
    )
    try:
        outcome = search_pubmed(client, args.query, args.max_results, args.article_filter)
    except (NCBIError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "query": outcome.query,
            "source": "NCBI E-utilities, db=pubmed",
            "total_reported": outcome.total_reported,
            "dropped_irrelevant": outcome.dropped_irrelevant,
            "results": [r.to_dict() for r in outcome.records],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        notes = [f"PubMed reports {outcome.total_reported} matching records; showing {len(outcome.records)}."]
        if outcome.dropped_irrelevant:
            notes.append(f"{outcome.dropped_irrelevant} result(s) dropped by the relevance check.")
        print(ev.format_records_markdown(f"PubMed: {outcome.query}", notes, outcome.records))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Search WHO publications and guidelines in the WHO Institutional Repository (IRIS).

Source
- WHO IRIS, https://iris.who.int/ : WHO's repository of its scientific and
  technical publications, which includes WHO guidelines.
- Search uses the DSpace REST discovery endpoint, which WHO IRIS is expected
  to expose at https://iris.who.int/server/api/ :
      GET /discover/search/objects?query=...&dsoType=ITEM&page=0&size=N

Status: NOT YET VERIFIED LIVE
- The endpoint follows the DSpace 7 REST contract. It could not be reached
  from the build environment, so the response shapes are tested offline
  against fixtures that follow that contract. Run the live tests from a
  machine that can reach iris.who.int before relying on this module.
- The WHO website publications API (who.int/api/.../sfhelp) was not used: its
  documentation could not be retrieved, so its behaviour is unknown.

What it does
- Returns title, authors, issue date, repository last-modified date, WHO
  document type, DOI (if the item has one), official IRIS link and abstract
  (only when IRIS supplies dc.description.abstract).
- Separates real clinical guidelines from other WHO documents. Each decision
  states its basis: the repository type field, or a title-keyword heuristic.

What it never does
- Download PDFs or full text. It never fabricates a field.

Usage
    python3 scripts/who_guidelines.py search "diabetes" --max 5
    python3 scripts/who_guidelines.py search "sepsis" --guidelines-only
    python3 scripts/who_guidelines.py search "vaccination" --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from ncbi_bookshelf import MAX_RETRIES, RETRYABLE_HTTP, NCBIError, RateLimiter
import medical_evidence as ev

IRIS_API_BASE = os.environ.get("WHO_IRIS_API_BASE", "https://iris.who.int/server/api/")
IRIS_HANDLE_BASE = "https://iris.who.int/handle/"
IRIS_HOME = "https://iris.who.int/"
WHO_GUIDELINES_PAGE = "https://www.who.int/publications/guidelines"
WHO_PUBLICATIONS_PAGE = "https://www.who.int/publications"
SOURCE_NAME = "WHO IRIS"
TOOL_NAME = "ai-medical-video-who"

MAX_RESULTS_CAP = 100
CANDIDATE_MULTIPLIER = 3
RATE_PER_SECOND = 2.0  # conservative; WHO publishes no rate limit for this endpoint

Transport = Callable[[str], bytes]


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": TOOL_NAME, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class IrisClient:
    """Minimal client for the DSpace REST discovery endpoint on WHO IRIS."""

    def __init__(
        self,
        base: str = IRIS_API_BASE,
        transport: Optional[Transport] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._base = base if base.endswith("/") else base + "/"
        self._limiter = RateLimiter(RATE_PER_SECOND, clock=clock, sleep=sleep)
        self._transport: Transport = transport or _http_get
        self._sleep = sleep
        self._max_retries = max_retries
        self.requests_made = 0

    def search_objects(self, query: str, page: int = 0, size: int = 20) -> dict:
        params = {"query": query, "dsoType": "ITEM", "page": page, "size": size}
        url = self._base + "discover/search/objects?" + urllib.parse.urlencode(params)
        body = self._get(url)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError, TypeError) as exc:
            raise NCBIError("WHO IRIS returned non-JSON data") from exc
        if not isinstance(data, dict):
            raise NCBIError("WHO IRIS returned an unexpected payload")
        return data

    def _get(self, url: str) -> bytes:
        attempt = 0
        while True:
            self._limiter.wait()
            self.requests_made += 1
            try:
                return self._transport(url)
            except urllib.error.HTTPError as exc:
                retryable = exc.code in RETRYABLE_HTTP
                reason = f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError) as exc:
                retryable = True
                reason = type(exc).__name__
            # The URL is not included in the message, to keep messages short.
            if not retryable or attempt >= self._max_retries:
                raise NCBIError(f"WHO IRIS request failed: {reason}")
            attempt += 1
            self._sleep(2**attempt)


# ---------------------------------------------------------------- parsing


def _values(metadata: dict, key: str) -> list[str]:
    items = metadata.get(key) or []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("value"), str):
            value = " ".join(item["value"].split())
            if value:
                out.append(value)
    return out


def _first(metadata: dict, key: str) -> Optional[str]:
    values = _values(metadata, key)
    return values[0] if values else None


_DOI_IN_VALUE = re.compile(r"10\.\d{4,9}/\S+")


def _doi(metadata: dict) -> Optional[str]:
    for key, items in metadata.items():
        if "doi" in key.lower():
            for value in _values(metadata, key):
                match = _DOI_IN_VALUE.search(value)
                if match:
                    return match.group(0).rstrip(".,;")
    return None


def _versions(metadata: dict) -> list[str]:
    found: list[str] = []
    for key in metadata:
        if "version" in key.lower():
            found.extend(_values(metadata, key))
    return found


def _objects(data: dict) -> list[dict]:
    search = (data.get("_embedded") or {}).get("searchResult") or {}
    objects = (search.get("_embedded") or {}).get("objects") or []
    items: list[dict] = []
    for obj in objects:
        indexable = ((obj or {}).get("_embedded") or {}).get("indexableObject")
        if isinstance(indexable, dict):
            items.append(indexable)
    return items


def _total(data: dict) -> int:
    search = (data.get("_embedded") or {}).get("searchResult") or {}
    page = search.get("page") or data.get("page") or {}
    try:
        return int(page.get("totalElements", 0))
    except (TypeError, ValueError):
        return 0


def normalize_item(item: dict) -> tuple[ev.MedicalRecord, list[str]]:
    """Return (record, subject_terms). Subjects feed the relevance check only."""
    metadata = item.get("metadata") or {}
    title = _first(metadata, "dc.title") or item.get("name") or "(untitled)"
    authors = _values(metadata, "dc.contributor.author") or _values(metadata, "dc.contributor")
    issued = _first(metadata, "dc.date.issued")
    dc_type = _first(metadata, "dc.type")
    abstract = _first(metadata, "dc.description.abstract")
    subjects = _values(metadata, "dc.subject")
    publisher = _first(metadata, "dc.publisher")
    handle = item.get("handle")
    url = (IRIS_HANDLE_BASE + handle) if handle else _first(metadata, "dc.identifier.uri")
    doi = _doi(metadata)

    evidence, document_type, basis = ev.classify_who(
        dc_type, title, " ".join(subjects + ([abstract] if abstract else []))
    )
    repo_modified = item.get("lastModified")
    last_updated = repo_modified[:10] if isinstance(repo_modified, str) and repo_modified else None

    record = ev.MedicalRecord(
        source=SOURCE_NAME,
        source_id=item.get("uuid"),
        title=title,
        authors=authors,
        publication_date=issued,
        last_updated=last_updated,
        document_type=document_type,
        evidence_type=evidence,
        url=url,
        doi=doi,
        abstract=abstract,
        abstract_scope=ev.SCOPE_ABSTRACT if abstract else ev.SCOPE_NONE,
        container=publisher,
        citation=ev.make_citation(authors, title, publisher or SOURCE_NAME, issued, doi, url),
    )
    versions = _versions(metadata)
    if versions:
        record.notes.append("Document version as recorded by IRIS: " + "; ".join(versions))
    if last_updated:
        record.notes.append("Last updated is the IRIS repository modification date, not a WHO revision date.")
    record.notes.append(f"Type basis: {basis}.")
    return record, subjects


# ---------------------------------------------------------------- search


def search_who(
    client: IrisClient,
    query: str,
    max_results: int = 10,
    guidelines_only: bool = False,
) -> ev.SearchOutcome:
    query = query.strip()
    if not query:
        raise ValueError("Query must not be empty")
    ev.require_concepts(query)
    limit = max(1, min(int(max_results), MAX_RESULTS_CAP))

    outcome = ev.SearchOutcome(source=SOURCE_NAME, query=query)
    pool = min(limit * CANDIDATE_MULTIPLIER, MAX_RESULTS_CAP)
    data = client.search_objects(query, page=0, size=pool)
    outcome.total_reported = _total(data)

    records: list[ev.MedicalRecord] = []
    for item in _objects(data):
        record, subjects = normalize_item(item)
        ev.apply_relevance(query, record, extra_texts=subjects)
        records.append(record)
    outcome.dropped_irrelevant = sum(1 for r in records if not r.relevant)
    kept = [r for r in records if r.relevant]
    if guidelines_only:
        kept = [r for r in kept if r.evidence_type == ev.GUIDELINE]
    kept.sort(key=ev.sort_key_newest_first, reverse=True)
    kept.sort(key=lambda r: r.relevance, reverse=True)
    outcome.records = kept[:limit]
    outcome.notes.append(f"Search uses the IRIS discovery endpoint ({IRIS_API_BASE}); live verification pending.")
    return outcome


# ---------------------------------------------------------------- CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search WHO IRIS publications and guidelines.")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="Search WHO IRIS")
    search.add_argument("query")
    search.add_argument("--max", type=int, default=10, dest="max_results")
    search.add_argument("--guidelines-only", action="store_true",
                        help="Keep only items classified as clinical guidelines")
    search.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    client = IrisClient()
    try:
        outcome = search_who(client, args.query, args.max_results, args.guidelines_only)
    except (NCBIError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "query": outcome.query,
            "source": "WHO IRIS (DSpace REST discovery)",
            "total_reported": outcome.total_reported,
            "dropped_irrelevant": outcome.dropped_irrelevant,
            "guidelines_page": WHO_GUIDELINES_PAGE,
            "results": [r.to_dict() for r in outcome.records],
        }
        for item in payload["results"]:
            item.pop("relevant", None)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        notes = outcome.notes + [
            f"IRIS reports {outcome.total_reported} matching items; showing {len(outcome.records)}.",
            f"WHO guidelines page: {WHO_GUIDELINES_PAGE}",
        ]
        if outcome.dropped_irrelevant:
            notes.append(f"{outcome.dropped_irrelevant} result(s) dropped by the relevance check.")
        print(ev.format_records_markdown(f"WHO IRIS: {outcome.query}", notes, outcome.records))
    return 0


if __name__ == "__main__":
    sys.exit(main())

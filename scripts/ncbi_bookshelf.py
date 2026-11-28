#!/usr/bin/env python3
"""Search NCBI Bookshelf through the official NCBI E-utilities API (db=books).

What it does
- ESearch: finds Bookshelf records (books and chapters) for a query.
- ESummary: returns metadata for those records (title, authors, date, NBK id).
- Each result keeps its official Bookshelf link and a citation string.
- A summary is shown only when NCBI returns an official text field for the
  record. Nothing is generated or paraphrased by this script.

What it never does
- Download book text: no EFetch, and no requests to book pages.
- Scrape HTML. Only the documented JSON API is used.

Official references
- Bookshelf:                https://www.ncbi.nlm.nih.gov/books/
- Bookshelf help:           https://www.ncbi.nlm.nih.gov/books/NBK25500/
- E-utilities help / usage: https://www.ncbi.nlm.nih.gov/books/NBK25497/

Optional environment variables
- NCBI_API_KEY      Raises the rate limit from 3 to 10 requests per second.
- NCBI_TOOL_EMAIL   Contact address NCBI may use if traffic causes problems.
                    Not set by default and never hard-coded.

Standard library only. No installs are needed.

Usage
    python3 scripts/ncbi_bookshelf.py topics
    python3 scripts/ncbi_bookshelf.py search --topic pharmacology --max 5
    python3 scripts/ncbi_bookshelf.py search "emergency medicine" --json
    python3 scripts/ncbi_bookshelf.py search --topic anatomy --raw   # raw ESummary JSON
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
from dataclasses import asdict, dataclass
from typing import Callable, Optional

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
BOOKSHELF_BASE = "https://www.ncbi.nlm.nih.gov/books/"
DATABASE = "books"
TOOL_NAME = "ai-medical-video-bookshelf"

MAX_RESULTS_CAP = 100  # hard cap per run
PAGE_SIZE = 100  # ids per ESearch / ESummary request
RATE_NO_KEY = 3.0  # requests per second, no API key
RATE_WITH_KEY = 10.0  # requests per second, with API key
MAX_RETRIES = 3
RETRYABLE_HTTP = {429, 500, 502, 503, 504}
SUMMARY_MAX_CHARS = 400

TOPIC_PRESETS = {
    "pathophysiology": "pathophysiology",
    "pharmacology": "pharmacology",
    "anatomy": "anatomy",
    "physiology": "physiology",
    "emergency-medicine": '"emergency medicine"',
}

NBK_PATTERN = re.compile(r"\bNBK\d+\b")

Transport = Callable[[str], bytes]


class NCBIError(RuntimeError):
    """Raised when NCBI returns an error or a response cannot be used."""


# ---------------------------------------------------------------- topics


def normalize_topic(name: str) -> str:
    """'Emergency Medicine' -> 'emergency-medicine'."""
    return re.sub(r"[\s_]+", "-", name.strip().lower())


def topic_query(name: str) -> str:
    key = normalize_topic(name)
    if key not in TOPIC_PRESETS:
        raise ValueError(
            f"Unknown topic {name!r}. Choose from: {', '.join(TOPIC_PRESETS)}"
        )
    return TOPIC_PRESETS[key]


# ---------------------------------------------------------------- HTTP


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": TOOL_NAME})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class RateLimiter:
    """Spaces requests so the rate never exceeds `per_second`."""

    def __init__(
        self,
        per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = 1.0 / per_second
        self._clock = clock
        self._sleep = sleep
        self._next_allowed = 0.0

    def wait(self) -> None:
        now = self._clock()
        delay = self._next_allowed - now
        if delay > 0:
            self._sleep(delay)
            now = self._clock()
        self._next_allowed = now + self._interval


class EutilsClient:
    """Minimal E-utilities client: ESearch and ESummary only."""

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

    def _get_json(self, endpoint: str, params: dict) -> dict:
        full = {"db": DATABASE, "tool": TOOL_NAME, "retmode": "json", **params}
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
                body = self._transport(url)
                break
            except urllib.error.HTTPError as exc:  # must come before URLError
                retryable = exc.code in RETRYABLE_HTTP
                reason = f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError) as exc:
                retryable = True
                reason = type(exc).__name__
            # The URL is never included in messages: it may carry the API key.
            if not retryable or attempt >= self._max_retries:
                raise NCBIError(f"NCBI request to {endpoint} failed: {reason}")
            attempt += 1
            self._sleep(2**attempt)  # 2 s, 4 s, 8 s

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise NCBIError(f"NCBI returned non-JSON data for {endpoint}") from exc
        if not isinstance(data, dict):
            raise NCBIError(f"NCBI returned an unexpected payload for {endpoint}")
        return data

    def esearch(self, term: str, retstart: int = 0, retmax: int = PAGE_SIZE):
        """Return (total_count, [uid, ...]) for a query in db=books."""
        data = self._get_json(
            "esearch.fcgi",
            {"term": term, "retstart": retstart, "retmax": retmax},
        )
        result = data.get("esearchresult")
        if not isinstance(result, dict):
            raise NCBIError("ESearch response has no esearchresult object")
        if "ERROR" in result:
            raise NCBIError(f"ESearch error: {result['ERROR']}")
        try:
            count = int(result.get("count", 0))
        except (TypeError, ValueError) as exc:
            raise NCBIError("ESearch returned an unexpected count") from exc
        ids = [str(uid) for uid in result.get("idlist", [])]
        return count, ids

    def esummary(self, ids: list[str]) -> dict[str, dict]:
        """Return {uid: raw_summary_dict} for the given uids."""
        if not ids:
            return {}
        data = self._get_json("esummary.fcgi", {"id": ",".join(ids)})
        result = data.get("result")
        if not isinstance(result, dict):
            raise NCBIError("ESummary response has no result object")
        out: dict[str, dict] = {}
        for uid in result.get("uids", ids):
            raw = result.get(str(uid))
            if isinstance(raw, dict) and "error" not in raw:
                out[str(uid)] = raw
        return out


# ---------------------------------------------------------------- records


@dataclass
class BookshelfResult:
    uid: str
    title: Optional[str]
    record_type: str  # "book", "chapter" or "unknown"
    book_title: Optional[str]
    authors: list[str]
    publication_date: Optional[str]
    accession: Optional[str]  # NBK id, e.g. "NBK12345"
    url: Optional[str]
    summary: Optional[str]  # official NCBI text only, never generated
    citation: str


def _first_text(raw: dict, *keys: str) -> Optional[str]:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return None


def _authors(raw: dict) -> list[str]:
    items = raw.get("authors") or raw.get("authorlist") or []
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        name: Optional[str] = None
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("name") or ", ".join(
                p for p in (item.get("lastname"), item.get("firstname")) if p
            )
        if name and name.strip():
            names.append(" ".join(name.split()))
    return names


def _accession(raw: dict) -> Optional[str]:
    for key in ("bookaccession", "accessionid", "accession", "nbk", "ncbi_id"):
        value = raw.get(key)
        if isinstance(value, str):
            match = NBK_PATTERN.search(value)
            if match:
                return match.group(0)
    for value in raw.values():
        if isinstance(value, str):
            match = NBK_PATTERN.search(value)
            if match:
                return match.group(0)
    return None


def shorten(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    cut = clean[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "..."


def _end(text: str) -> str:
    text = text.rstrip()
    return text if text.endswith((".", "?", "!")) else text + "."


def build_citation(
    authors: list[str],
    title: Optional[str],
    book_title: Optional[str],
    date: Optional[str],
    url: Optional[str],
) -> str:
    """Build a citation from the fields that exist. Missing fields are omitted."""
    parts: list[str] = []
    if authors:
        author_text = "; ".join(authors[:3])
        if len(authors) > 3:
            author_text += ", et al"
        parts.append(_end(author_text))
    if title:
        parts.append(_end(title))
    if book_title:
        parts.append(_end(f"In: {book_title}"))
    parts.append("NCBI Bookshelf.")
    if date:
        parts.append(_end(date))
    if url:
        parts.append(f"Available from: {url}")
    return " ".join(parts)


def normalize_record(uid: str, raw: dict) -> BookshelfResult:
    title = _first_text(raw, "title", "chaptertitle")
    book_title = _first_text(raw, "booktitle", "bookname")
    if book_title and title and book_title.lower() == title.lower():
        book_title = None  # a book whose title is repeated, not a chapter
    if book_title:
        record_type = "chapter"
    elif title:
        record_type = "book"
    else:
        record_type = "unknown"

    authors = _authors(raw)
    date = _first_text(raw, "pubdate", "epubdate", "sortpubdate")
    accession = _accession(raw)
    url = f"{BOOKSHELF_BASE}{accession}/" if accession else None

    summary_text = _first_text(raw, "summary", "abstract", "description")
    summary = shorten(summary_text) if summary_text else None

    return BookshelfResult(
        uid=uid,
        title=title,
        record_type=record_type,
        book_title=book_title,
        authors=authors,
        publication_date=date,
        accession=accession,
        url=url,
        summary=summary,
        citation=build_citation(authors, title, book_title, date, url),
    )


# ---------------------------------------------------------------- search


def search_bookshelf(
    client: EutilsClient, query: str, max_results: int = 10
) -> tuple[int, list[BookshelfResult]]:
    """Return (total_matches_reported_by_ncbi, normalized_results)."""
    query = query.strip()
    if not query:
        raise ValueError("Query must not be empty")
    limit = max(1, min(int(max_results), MAX_RESULTS_CAP))

    uids: list[str] = []
    total: Optional[int] = None
    retstart = 0
    while len(uids) < limit:
        count, page = client.esearch(
            query,
            retstart=retstart,
            retmax=min(PAGE_SIZE, limit - len(uids)),
        )
        if total is None:
            total = count
        if not page:
            break
        uids.extend(page)
        retstart += len(page)
        if retstart >= count:
            break
    uids = uids[:limit]

    summaries: dict[str, dict] = {}
    for start in range(0, len(uids), PAGE_SIZE):
        summaries.update(client.esummary(uids[start : start + PAGE_SIZE]))

    results = [normalize_record(uid, summaries[uid]) for uid in uids if uid in summaries]
    return total or 0, results


# ---------------------------------------------------------------- output


def format_markdown(label: str, total: int, results: list[BookshelfResult]) -> str:
    lines = [f"# NCBI Bookshelf: {label}", "", f"NCBI reports {total} matching records. Showing {len(results)}.", ""]
    if not results:
        lines.append("No records returned.")
    for index, item in enumerate(results, start=1):
        kind = item.record_type.capitalize()
        lines.append(f"{index}. **{item.title or '(untitled)'}** ({kind})")
        if item.book_title:
            lines.append(f"   - Book: {item.book_title}")
        if item.authors:
            lines.append(f"   - Authors: {'; '.join(item.authors)}")
        if item.publication_date:
            lines.append(f"   - Date: {item.publication_date}")
        if item.url:
            lines.append(f"   - Link: {item.url}")
        if item.summary:
            lines.append(f"   - Official summary: {item.summary}")
        lines.append(f"   - Citation: {item.citation}")
        lines.append("")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search NCBI Bookshelf via the official E-utilities API."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("topics", help="List preset medical topics")

    search = sub.add_parser("search", help="Search Bookshelf")
    search.add_argument("query", nargs="?", help="Free-text query, e.g. 'heart failure'")
    search.add_argument(
        "--topic", help="Preset topic: " + ", ".join(TOPIC_PRESETS)
    )
    search.add_argument(
        "--max", type=int, default=10, dest="max_results",
        help=f"Max results (1-{MAX_RESULTS_CAP}, default 10)",
    )
    search.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    search.add_argument(
        "--raw", action="store_true",
        help="Print the raw ESummary JSON (use once to confirm field names)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "topics":
        for name in TOPIC_PRESETS:
            print(name)
        return 0

    if args.topic and args.query:
        _parser().error("Use either a query or --topic, not both")
    if args.topic:
        label = normalize_topic(args.topic)
        query = topic_query(args.topic)
    elif args.query:
        label = args.query
        query = args.query
    else:
        _parser().error("Give a query or --topic")

    client = EutilsClient(
        api_key=os.environ.get("NCBI_API_KEY"),
        tool_email=os.environ.get("NCBI_TOOL_EMAIL"),
    )
    try:
        if args.raw:
            limit = max(1, min(args.max_results, MAX_RESULTS_CAP))
            _, ids = client.esearch(query, retmax=limit)
            summaries = client.esummary(ids)
            print(json.dumps(summaries, indent=2, ensure_ascii=False))
            return 0

        total, results = search_bookshelf(client, query, args.max_results)
    except NCBIError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "query": query,
            "label": label,
            "total_matches": total,
            "results": [asdict(item) for item in results],
            "source": "NCBI E-utilities, db=books",
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_markdown(label, total, results))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Search NCBI Bookshelf through the official NCBI E-utilities API (db=books).

What it does
- ESearch: finds Bookshelf records for a query, with field tags where NCBI supports them.
- ESummary: returns metadata for those records (title, chapter, book, date, NBK id).
- Each result keeps its official Bookshelf link and a citation string.
- A summary is shown only when NCBI returns an official text field for the record.
- Each result gets a document kind (heuristic, from title keywords) and a lexical
  relevance check against the query, based on titles only.

What it never does
- Download book text: no EFetch, and no requests to book pages.
- Scrape HTML. Only the documented JSON API is used.
- Invent metadata. Authors, dates and summaries are shown only when NCBI returns them.

NCBI query limits (verified against the live books database)
- [Title] and [Book] are supported. [Chapter] and [ti] are silently ignored, and
  [Book Title] matches nothing. Chapter searches therefore use [Title].
- [Subheading] filters only work with untagged phrases. Combined with [Title],
  they return zero results.
- MeSH terms work only when the exact term exists, e.g. "adrenergic beta-antagonists".

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
    python3 scripts/ncbi_bookshelf.py search "diabetic ketoacidosis" --subheading pathophysiology
    python3 scripts/ncbi_bookshelf.py search "acute inflammation" --in title
    python3 scripts/ncbi_bookshelf.py search "insulin" --in book --max 5 --json
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
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
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

# Topic presets are plain text queries.
TOPIC_PRESETS = {
    "pathophysiology": "pathophysiology",
    "pharmacology": "pharmacology",
    "anatomy": "anatomy",
    "physiology": "physiology",
    "emergency-medicine": "emergency medicine",
}

# Field tags verified on the books database. [Chapter] is not a real tag, so
# chapter searches use [Title] (chapter titles sit in the title field).
FIELD_TAGS = {"any": "", "title": "[Title]", "book": "[Book]", "chapter": "[Title]"}

# Subheading filters verified with untagged phrases.
SUBHEADINGS = {
    "pathophysiology": "physiopathology[Subheading]",
    "pharmacology": "pharmacology[Subheading]",
}

# Synonyms verified to return results as MeSH terms.
MESH_SYNONYMS = {
    "beta blockers": '"adrenergic beta-antagonists"[MeSH Terms]',
    "beta-blockers": '"adrenergic beta-antagonists"[MeSH Terms]',
    "beta blocker": '"adrenergic beta-antagonists"[MeSH Terms]',
}

# Title keywords for document kind. Heuristic only: Bookshelf has no subject field.
HTA_MARKERS = (
    "reimbursement review", "evidence review", "health technolog", "cda-amc",
    "common drug review", "cadth", "evidence profile", "technical appendix",
    "therapeutic area:", "cost-effectiveness", "economic evaluation",
)
GUIDELINE_MARKERS = ("guideline", "recommendation", "consensus statement")
REPORT_MARKERS = ("report", "proceedings", "workshop", "meeting summary")

STOPWORDS = {
    "of", "the", "and", "in", "for", "with", "a", "an", "to", "on", "by",
    "or", "vs", "versus", "at", "from", "as",
}
RELEVANCE_RANK = {"likely_relevant": 0, "weak": 1, "not_relevant": 2, "unassessed": 3}
PLACEHOLDER_DATE = "2000/01/01"

NBK_PATTERN = re.compile(r"\bNBK\d+\b")

Transport = Callable[[str], bytes]


class NCBIError(RuntimeError):
    """Raised when NCBI returns an error or a response cannot be used."""


# ---------------------------------------------------------------- queries


def normalize_topic(name: str) -> str:
    """'Emergency Medicine' -> 'emergency-medicine'."""
    return re.sub(r"[\s_]+", "-", name.strip().lower())


def topic_text(name: str) -> str:
    key = normalize_topic(name)
    if key not in TOPIC_PRESETS:
        raise ValueError(
            f"Unknown topic {name!r}. Choose from: {', '.join(TOPIC_PRESETS)}"
        )
    return TOPIC_PRESETS[key]


def build_query(
    text: str, field: str = "any", subheading: Optional[str] = None
) -> str:
    """Build a verified ESearch term from plain text.

    field: any | title | book | chapter (chapter searches use [Title]).
    subheading: pathophysiology | pharmacology. Only with field "any".
    """
    clean = " ".join(text.replace('"', " ").split())
    if not clean:
        raise ValueError("Query must not be empty")
    if field not in FIELD_TAGS:
        raise ValueError(f"Unknown field {field!r}. Choose from: {', '.join(FIELD_TAGS)}")

    if field == "any" and clean.lower() in MESH_SYNONYMS:
        core = MESH_SYNONYMS[clean.lower()]
    else:
        core = f'"{clean}"' if " " in clean else clean
        core += FIELD_TAGS[field]

    if subheading:
        if field != "any":
            raise ValueError(
                "Subheading filters only work with untagged searches; "
                "NCBI returns zero results when they are combined with a field tag."
            )
        if subheading not in SUBHEADINGS:
            raise ValueError(
                f"Unknown subheading {subheading!r}. Choose from: {', '.join(SUBHEADINGS)}"
            )
        core = f"{core} AND {SUBHEADINGS[subheading]}"
    return core


def query_terms(text: str) -> list[str]:
    """Content words of the user's text, used for the relevance check."""
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if token not in STOPWORDS and token not in terms:
            terms.append(token)
    return terms


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
    record_type: str  # book, chapter, section, table, appendix, part or unknown
    book_title: Optional[str]
    chapter_title: Optional[str]
    authors: list[str]  # empty unless NCBI returns authors; never invented
    publication_date: Optional[str]
    date_note: Optional[str]
    accession: Optional[str]  # NBK id, e.g. "NBK12345"
    url: Optional[str]
    summary: Optional[str]  # official NCBI text only, never generated
    citation: str
    document_kind: str = "unclassified"
    kind_basis: str = "title keywords (heuristic)"
    relevance: str = "unassessed"  # likely_relevant | weak | not_relevant | unassessed
    relevance_reason: str = ""
    matched_terms: list[str] = field(default_factory=list)


# rtype values seen on the live books database: book, chapter, sec, table, appendix, part.
RTYPE_MAP = {
    "book": "book",
    "chapter": "chapter",
    "sec": "section",
    "table": "table",
    "appendix": "appendix",
    "part": "part",
}


def _first_text(raw: dict, *keys: str) -> Optional[str]:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return None


def _clean(value: Optional[str]) -> Optional[str]:
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


def _bookinfo(raw: dict) -> dict:
    """Parse the `bookinfo` XML: parent book and chapter titles for a record."""
    info = {"book_title": None, "chapter_title": None}
    text = raw.get("bookinfo")
    if not isinstance(text, str) or not text.strip():
        return info
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return info
    for parent in root.iter("Parent"):
        title = _clean(parent.findtext("Title"))
        if parent.get("type") == "book" and title:
            info["book_title"] = title
        elif parent.get("type") == "chapter" and title:
            info["chapter_title"] = title
    return info


def _record_type(raw: dict, book_title: Optional[str], title: Optional[str]) -> str:
    rtype = str(raw.get("rtype", "")).lower()
    if rtype in RTYPE_MAP:
        return RTYPE_MAP[rtype]
    if book_title:  # no explicit type: a title inside a book is treated as a chapter
        return "chapter"
    return "book" if title else "unknown"


def _accession(raw: dict, record_type: str) -> Optional[str]:
    """NBK id for the page a result links to: the book for books, the chapter otherwise."""
    if record_type == "book":
        keys = ("bookaccessionid", "bookaccession", "accessionid", "accession")
    else:
        keys = ("chapteraccessionid", "accessionid", "accession", "bookaccessionid")
    for key in keys:
        value = raw.get(key)
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
    chapter_title: Optional[str] = None,
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
    if chapter_title and chapter_title != title:
        parts.append(_end(f"In: {chapter_title}"))
    if book_title and book_title != title:
        parts.append(_end(book_title))
    parts.append("NCBI Bookshelf.")
    if date:
        parts.append(_end(date))
    if url:
        parts.append(f"Available from: {url}")
    return " ".join(parts)


def normalize_record(uid: str, raw: dict) -> BookshelfResult:
    info = _bookinfo(raw)
    title = _first_text(raw, "title", "chaptertitle")
    book_title = info["book_title"] or _first_text(raw, "booktitle", "bookname")
    if book_title and title and book_title.lower() == title.lower():
        book_title = None  # a book repeating its own title is not a chapter
    chapter_title = info["chapter_title"] or _first_text(raw, "chaptertitle")
    record_type = _record_type(raw, book_title, title)

    authors = _authors(raw)
    date = _first_text(raw, "pubdate", "epubdate", "sortpubdate")
    if date:
        date = date.replace(" 00:00", "")  # NCBI adds a midnight time to dates
    date_note = None
    if date and date.startswith(PLACEHOLDER_DATE):
        date_note = "date may be a placeholder (NCBI returns 2000/01/01 for some records)"
    accession = _accession(raw, record_type)
    url = f"{BOOKSHELF_BASE}{accession}/" if accession else None

    summary_text = _first_text(raw, "summary", "abstract", "description")
    summary = shorten(summary_text) if summary_text else None

    return BookshelfResult(
        uid=uid,
        title=title,
        record_type=record_type,
        book_title=book_title,
        chapter_title=chapter_title,
        authors=authors,
        publication_date=date,
        date_note=date_note,
        accession=accession,
        url=url,
        summary=summary,
        citation=build_citation(
            authors, title, book_title, date, url, chapter_title=chapter_title
        ),
    )


# ---------------------------------------------------------------- relevance


def classify_kind(item: BookshelfResult) -> str:
    """Heuristic document kind from title keywords. Labeled as heuristic in output."""
    text = " ".join(filter(None, [item.title, item.book_title, item.chapter_title])).lower()
    if any(marker in text for marker in HTA_MARKERS):
        return "health_technology_assessment"
    if any(marker in text for marker in GUIDELINE_MARKERS):
        return "guideline"
    if any(marker in text for marker in REPORT_MARKERS):
        return "report"
    return "unclassified"


def _token_set(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS}


def _has_term(term: str, tokens: set[str]) -> bool:
    if term in tokens:
        return True
    return len(term) >= 5 and any(tok.startswith(term) for tok in tokens)


def assess_relevance(terms: list[str], item: BookshelfResult) -> tuple[str, str, list[str]]:
    """Lexical relevance from metadata only. Not a judgment of medical content."""
    if not terms:
        return "unassessed", "no query terms", []

    primary = _token_set(" ".join(filter(None, [item.title, item.chapter_title])))
    everything = primary | _token_set(item.book_title or "")
    in_primary = [t for t in terms if _has_term(t, primary)]
    in_any = [t for t in terms if _has_term(t, everything)]
    missing = [t for t in terms if t not in in_any]

    if len(in_primary) == len(terms):
        label, reason = "likely_relevant", "all query terms in the title"
    elif len(in_any) == len(terms):
        label, reason = "weak", "query terms only in the book title"
    elif in_any:
        label, reason = "weak", "partial match; missing: " + ", ".join(missing)
    else:
        label, reason = "not_relevant", "no query term in the title, chapter or book title"

    if item.document_kind == "health_technology_assessment" and label == "likely_relevant":
        label = "weak"
        reason = "reimbursement or HTA document; not a clinical textbook source"

    return label, reason, in_any


# ---------------------------------------------------------------- search


def search_bookshelf(
    client: EutilsClient,
    text: str,
    max_results: int = 10,
    field_name: str = "any",
    subheading: Optional[str] = None,
) -> tuple[int, str, list[BookshelfResult]]:
    """Return (total_matches, query_sent, results sorted by relevance, NCBI order kept)."""
    text = text.strip()
    if not text:
        raise ValueError("Query must not be empty")
    query = build_query(text, field=field_name, subheading=subheading)
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

    terms = query_terms(text)
    for item in results:
        item.document_kind = classify_kind(item)
        item.relevance, item.relevance_reason, item.matched_terms = assess_relevance(
            terms, item
        )
    results.sort(key=lambda item: RELEVANCE_RANK[item.relevance])  # stable: keeps NCBI order
    return total or 0, query, results


# ---------------------------------------------------------------- output


def _kind_label(kind: str) -> str:
    return {
        "health_technology_assessment": "Health technology assessment / reimbursement",
        "guideline": "Guideline",
        "report": "Report",
        "unclassified": "Unclassified",
    }.get(kind, kind)


def format_markdown(
    label: str, query: str, total: int, results: list[BookshelfResult]
) -> str:
    lines = [
        f"# NCBI Bookshelf: {label}",
        "",
        f"Query sent to NCBI: `{query}`",
        f"NCBI reports {total} matching records. Showing {len(results)}.",
        "",
        "Relevance is a lexical check on titles only. It is not a judgment of medical content.",
        "",
    ]
    if not results:
        lines.append("No records returned.")
    for index, item in enumerate(results, start=1):
        lines.append(
            f"{index}. **{item.title or '(untitled)'}** "
            f"({item.record_type}; {_kind_label(item.document_kind)}, heuristic)"
        )
        lines.append(f"   - Relevance: {item.relevance} ({item.relevance_reason})")
        if item.chapter_title and item.chapter_title != item.title:
            lines.append(f"   - Chapter: {item.chapter_title}")
        if item.book_title and item.book_title != item.title:
            lines.append(f"   - Book: {item.book_title}")
        if item.authors:
            lines.append(f"   - Authors: {'; '.join(item.authors)}")
        else:
            lines.append("   - Authors: not returned by NCBI")
        if item.publication_date:
            note = f" ({item.date_note})" if item.date_note else ""
            lines.append(f"   - Date: {item.publication_date}{note}")
        else:
            lines.append("   - Date: not returned by NCBI")
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
    search.add_argument("query", nargs="?", help="Text query, e.g. 'diabetic ketoacidosis'")
    search.add_argument("--topic", help="Preset topic: " + ", ".join(TOPIC_PRESETS))
    search.add_argument(
        "--in", dest="field_name", default="any", choices=list(FIELD_TAGS),
        help="Where to match: any field, book title, or title (chapter titles use the title field)",
    )
    search.add_argument(
        "--subheading", choices=list(SUBHEADINGS),
        help="NCBI subject heading filter (works only with --in any)",
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
    parser = _parser()
    args = parser.parse_args(argv)

    if args.command == "topics":
        for name in TOPIC_PRESETS:
            print(name)
        return 0

    if args.topic and args.query:
        parser.error("Use either a query or --topic, not both")
    if args.topic:
        label = normalize_topic(args.topic)
        text = topic_text(args.topic)
    elif args.query:
        label = args.query
        text = args.query
    else:
        parser.error("Give a query or --topic")

    client = EutilsClient(
        api_key=os.environ.get("NCBI_API_KEY"),
        tool_email=os.environ.get("NCBI_TOOL_EMAIL"),
    )
    try:
        if args.raw:
            query = build_query(text, field=args.field_name, subheading=args.subheading)
            limit = max(1, min(args.max_results, MAX_RESULTS_CAP))
            _, ids = client.esearch(query, retmax=limit)
            summaries = client.esummary(ids)
            print(json.dumps(summaries, indent=2, ensure_ascii=False))
            return 0

        total, query, results = search_bookshelf(
            client, text, args.max_results,
            field_name=args.field_name, subheading=args.subheading,
        )
    except (NCBIError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "label": label,
            "query_text": text,
            "query_sent": query,
            "field": args.field_name,
            "subheading": args.subheading,
            "terms": query_terms(text),
            "total_matches": total,
            "results": [asdict(item) for item in results],
            "source": "NCBI E-utilities, db=books",
            "caveats": [
                "Relevance is lexical, from titles only.",
                "Document kind is a heuristic from title keywords.",
                "Authors and summaries are shown only when NCBI returns them.",
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_markdown(label, query, total, results))
    return 0


if __name__ == "__main__":
    sys.exit(main())

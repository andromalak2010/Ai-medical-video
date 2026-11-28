"""Offline tests for scripts/ncbi_bookshelf.py. No network access is needed.

Run:  python3 scripts/test_ncbi_bookshelf.py
"""

import json
import os
import sys
import unittest
import urllib.error
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ncbi_bookshelf as nb  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeNCBI:
    """Answers ESearch/ESummary from canned data and records every URL."""

    def __init__(self, ids=None, summaries=None, errors=None):
        self.ids = ids or []
        self.summaries = summaries or {}
        self.errors = list(errors or [])  # exceptions raised before a success
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if self.errors:
            raise self.errors.pop(0)
        query = parse_qs(urlparse(url).query)
        if "esearch.fcgi" in url:
            start = int(query["retstart"][0])
            n = int(query["retmax"][0])
            page = self.ids[start : start + n]
            return json.dumps(
                {"esearchresult": {"count": str(len(self.ids)), "idlist": page}}
            )
        if "esummary.fcgi" in url:
            uids = query["id"][0].split(",")
            result = {"uids": uids}
            for uid in uids:
                if uid in self.summaries:
                    result[uid] = self.summaries[uid]
            return json.dumps({"result": result})
        raise AssertionError(f"unexpected endpoint: {url}")


BOOK_RAW = {
    "uid": "1001",
    "title": "Pharmacology: Principles of Drug Action",
    "authors": [{"name": "Smith J"}, {"name": "Lee K"}],
    "pubdate": "2021 Mar",
    "bookaccession": "NBK99999",
}

CHAPTER_RAW = {
    "uid": "1002",
    "title": "Beta-Blockers",
    "booktitle": "Pharmacology: Principles of Drug Action",
    "authors": [{"lastname": "Doe", "firstname": "A"}],
    "pubdate": "2020",
    "accessionid": "NBK12345",
    "summary": "Beta-blockers   reduce heart rate and  contractility. " * 20,
}


class ParsingTests(unittest.TestCase):
    def test_book_record_has_link_and_citation(self):
        item = nb.normalize_record("1001", BOOK_RAW)
        self.assertEqual(item.record_type, "book")
        self.assertEqual(item.accession, "NBK99999")
        self.assertEqual(item.url, "https://www.ncbi.nlm.nih.gov/books/NBK99999/")
        self.assertEqual(item.authors, ["Smith J", "Lee K"])
        self.assertIn("NBK99999", item.citation.replace("/", " "))
        self.assertIn("Available from: https://www.ncbi.nlm.nih.gov/books/NBK99999/", item.citation)

    def test_chapter_detected_and_cited_within_book(self):
        item = nb.normalize_record("1002", CHAPTER_RAW)
        self.assertEqual(item.record_type, "chapter")
        self.assertEqual(item.book_title, "Pharmacology: Principles of Drug Action")
        self.assertIn("In: Pharmacology: Principles of Drug Action.", item.citation)
        self.assertIn("Doe, A", item.authors)

    def test_no_summary_is_invented(self):
        item = nb.normalize_record("1001", BOOK_RAW)
        self.assertIsNone(item.summary)

    def test_official_summary_is_shortened_not_rewritten(self):
        item = nb.normalize_record("1002", CHAPTER_RAW)
        self.assertLessEqual(len(item.summary), nb.SUMMARY_MAX_CHARS + 3)
        self.assertTrue(item.summary.startswith("Beta-blockers reduce heart rate"))
        self.assertTrue(item.summary.endswith("..."))

    def test_citation_omits_missing_fields(self):
        item = nb.normalize_record("9", {"title": "Only A Title"})
        self.assertEqual(item.citation, "Only A Title. NCBI Bookshelf.")
        self.assertIsNone(item.url)
        self.assertIsNone(item.publication_date)
        self.assertEqual(item.authors, [])

    def test_many_authors_use_et_al(self):
        citation = nb.build_citation(["A", "B", "C", "D"], "T", None, None, None)
        self.assertTrue(citation.startswith("A; B; C, et al. T."))

    def test_book_repeating_its_title_is_not_a_chapter(self):
        raw = {"title": "Same", "booktitle": "same"}
        self.assertEqual(nb.normalize_record("3", raw).record_type, "book")


class TopicTests(unittest.TestCase):
    def test_topic_names_normalize(self):
        self.assertEqual(nb.topic_query("Emergency Medicine"), '"emergency medicine"')
        self.assertEqual(nb.topic_query("pharmacology"), "pharmacology")

    def test_unknown_topic_raises(self):
        with self.assertRaises(ValueError):
            nb.topic_query("cardiology-xyz")


class ClientTests(unittest.TestCase):
    def test_esearch_parses_count_and_ids(self):
        fake = FakeNCBI(ids=["1", "2", "3"])
        client = nb.EutilsClient(transport=fake, sleep=lambda s: None)
        count, ids = client.esearch("anatomy")
        self.assertEqual((count, ids), (3, ["1", "2", "3"]))

    def test_esearch_error_field_raises(self):
        def transport(url):
            return json.dumps({"esearchresult": {"ERROR": "bad term"}})

        client = nb.EutilsClient(transport=transport)
        with self.assertRaises(nb.NCBIError):
            client.esearch("x")

    def test_non_json_response_raises(self):
        client = nb.EutilsClient(transport=lambda url: b"<html>oops</html>")
        with self.assertRaises(nb.NCBIError):
            client.esearch("x")

    def test_retries_on_429_then_succeeds(self):
        clock = FakeClock()
        err = urllib.error.HTTPError("u", 429, "Too Many", None, None)
        fake = FakeNCBI(ids=["7"], errors=[err])
        client = nb.EutilsClient(
            transport=fake, clock=clock.clock, sleep=clock.sleep
        )
        count, ids = client.esearch("x")
        self.assertEqual(ids, ["7"])
        self.assertEqual(client.requests_made, 2)
        self.assertIn(2, clock.sleeps)  # first backoff is 2 seconds

    def test_non_retryable_http_error_fails_fast(self):
        err = urllib.error.HTTPError("u", 400, "Bad", None, None)
        fake = FakeNCBI(errors=[err])
        client = nb.EutilsClient(transport=fake, sleep=lambda s: None)
        with self.assertRaises(nb.NCBIError):
            client.esearch("x")
        self.assertEqual(client.requests_made, 1)

    def test_gives_up_after_max_retries(self):
        err = lambda: urllib.error.HTTPError("u", 503, "Busy", None, None)  # noqa: E731
        fake = FakeNCBI(errors=[err() for _ in range(10)])
        client = nb.EutilsClient(transport=fake, sleep=lambda s: None, max_retries=2)
        with self.assertRaises(nb.NCBIError):
            client.esearch("x")
        self.assertEqual(client.requests_made, 3)

    def test_api_key_is_sent_but_never_in_errors(self):
        seen = []

        def transport(url):
            seen.append(url)
            raise urllib.error.HTTPError(url, 400, "Bad", None, None)

        client = nb.EutilsClient(api_key="SECRETKEY123", transport=transport)
        with self.assertRaises(nb.NCBIError) as ctx:
            client.esearch("x")
        self.assertIn("api_key=SECRETKEY123", seen[0])
        self.assertNotIn("SECRETKEY123", str(ctx.exception))

    def test_no_email_sent_unless_configured(self):
        fake = FakeNCBI(ids=[])
        nb.EutilsClient(transport=fake).esearch("x")
        self.assertNotIn("email=", fake.urls[0])
        fake2 = FakeNCBI(ids=[])
        nb.EutilsClient(transport=fake2, tool_email="dev@example.org").esearch("x")
        self.assertIn("email=dev%40example.org", fake2.urls[0])


class RateLimitTests(unittest.TestCase):
    def test_without_key_spaces_requests_at_three_per_second(self):
        clock = FakeClock()
        client = nb.EutilsClient(
            transport=FakeNCBI(ids=[]), clock=clock.clock, sleep=clock.sleep
        )
        for _ in range(4):
            client.esearch("x")
        # First request is free, then one wait of 1/3 s before each of the next three.
        self.assertEqual(len(clock.sleeps), 3)
        for s in clock.sleeps:
            self.assertAlmostEqual(s, 1 / 3, places=6)

    def test_with_key_allows_ten_per_second(self):
        clock = FakeClock()
        client = nb.EutilsClient(
            api_key="k", transport=FakeNCBI(ids=[]), clock=clock.clock, sleep=clock.sleep
        )
        for _ in range(3):
            client.esearch("x")
        for s in clock.sleeps:
            self.assertAlmostEqual(s, 0.1, places=6)


class SearchTests(unittest.TestCase):
    def test_max_results_is_capped_at_100(self):
        ids = [str(i) for i in range(1, 300)]
        summaries = {uid: {"title": f"T{uid}"} for uid in ids}
        fake = FakeNCBI(ids=ids, summaries=summaries)
        client = nb.EutilsClient(transport=fake, sleep=lambda s: None)
        total, results = nb.search_bookshelf(client, "anatomy", max_results=500)
        self.assertEqual(total, 299)
        self.assertEqual(len(results), 100)
        for url in fake.urls:
            if "esummary" in url:
                self.assertLessEqual(len(parse_qs(urlparse(url).query)["id"][0].split(",")), 100)

    def test_search_returns_normalized_results(self):
        fake = FakeNCBI(
            ids=["1001", "1002"],
            summaries={"1001": BOOK_RAW, "1002": CHAPTER_RAW},
        )
        client = nb.EutilsClient(transport=fake, sleep=lambda s: None)
        total, results = nb.search_bookshelf(client, "pharmacology", max_results=5)
        self.assertEqual(total, 2)
        self.assertEqual([r.uid for r in results], ["1001", "1002"])
        self.assertEqual(results[1].record_type, "chapter")

    def test_only_esearch_and_esummary_are_ever_called(self):
        # Guards the "do not download entire books" rule: no EFetch, no book pages.
        fake = FakeNCBI(ids=["1001"], summaries={"1001": BOOK_RAW})
        client = nb.EutilsClient(transport=fake, sleep=lambda s: None)
        nb.search_bookshelf(client, "pathophysiology", max_results=3)
        for url in fake.urls:
            self.assertTrue(
                url.startswith(nb.EUTILS_BASE + "esearch.fcgi?")
                or url.startswith(nb.EUTILS_BASE + "esummary.fcgi?"),
                url,
            )
            self.assertNotIn("efetch", url)
            self.assertNotIn("rettype", url)

    def test_empty_query_rejected(self):
        client = nb.EutilsClient(transport=FakeNCBI())
        with self.assertRaises(ValueError):
            nb.search_bookshelf(client, "   ")

    def test_markdown_output_contains_link_and_citation(self):
        item = nb.normalize_record("1001", BOOK_RAW)
        text = nb.format_markdown("pharmacology", 1, [item])
        self.assertIn("https://www.ncbi.nlm.nih.gov/books/NBK99999/", text)
        self.assertIn("Citation:", text)
        self.assertNotIn("Official summary", text)  # none was returned


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Live tests against the real sources. Needs network access.

Covers the five required topics. A source that cannot be reached from the
machine running the tests is SKIPPED with the reason printed, never reported
as a pass. Check the skip reasons in the output.

Run: python3 scripts/test_live_medical_sources.py
Optional: NCBI_API_KEY (faster PubMed/Bookshelf), NCBI_TOOL_EMAIL.
"""

import os
import unittest
import urllib.error
import urllib.request

import cochrane_search as cs
import medical_evidence as ev
import medical_search as ms
import pubmed_search as ps
import who_guidelines as who
from ncbi_bookshelf import NCBIError

TOPICS = [
    "diabetes mellitus guidelines",
    "diabetic ketoacidosis",
    "hypertension treatment",
    "sepsis guidelines",
    "acute asthma management",
]

COCHRANE_DOI_PREFIX = "10.1002/14651858"


def _head_status(url: str) -> int:
    """HTTP status for a link, or raise OSError if the host cannot be reached."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ai-medical-video-linkcheck"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


class LiveSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api_key = os.environ.get("NCBI_API_KEY")
        cls.email = os.environ.get("NCBI_TOOL_EMAIL")

    def _pubmed(self):
        return ps.PubMedClient(api_key=self.api_key, tool_email=self.email)

    def _skip_if_unreachable(self, source, call):
        """Run a live call. Skip (with the reason) only when the host cannot be reached.

        Real API errors, such as a bad request, still fail the test.
        """
        connectivity = ("URLError", "Timeout", "ConnectionError", "HTTP 429", "HTTP 5")
        try:
            return call()
        except OSError as exc:  # urllib errors are OSError subclasses
            self.skipTest(f"{source} not reachable from this machine: {type(exc).__name__}")
        except NCBIError as exc:
            if any(word in str(exc) for word in connectivity):
                self.skipTest(f"{source} not reachable from this machine: {exc}")
            raise

    def test_pubmed_all_topics(self):
        client = self._pubmed()
        for topic in TOPICS:
            with self.subTest(topic=topic):
                outcome = self._skip_if_unreachable("PubMed", lambda: ps.search_pubmed(client, topic, 5))
                self.assertTrue(outcome.records, f"no relevant PubMed records for {topic!r}")
                for record in outcome.records:
                    self.assertTrue(record.relevant)
                    self.assertTrue(record.url.startswith("https://pubmed.ncbi.nlm.nih.gov/"))
                    self.assertTrue(record.title)
                    if record.abstract:
                        self.assertEqual(record.abstract_scope, ev.SCOPE_ABSTRACT)

    def test_bookshelf_all_topics(self):
        from ncbi_bookshelf import EutilsClient

        client = EutilsClient(api_key=self.api_key, tool_email=self.email)
        for topic in TOPICS:
            with self.subTest(topic=topic):
                outcome = self._skip_if_unreachable("NCBI Bookshelf", lambda: ms.bookshelf_outcome(client, topic, 5))
                for record in outcome.records:
                    self.assertTrue(record.relevant)
                    self.assertTrue(record.url.startswith("https://www.ncbi.nlm.nih.gov/books/NBK"))
                    self.assertEqual(record.evidence_type, ev.GENERAL)

    def test_cochrane_reviews_for_topics_with_known_reviews(self):
        client = self._pubmed()
        for topic in ["diabetes mellitus guidelines", "hypertension treatment", "sepsis guidelines", "acute asthma management"]:
            with self.subTest(topic=topic):
                outcome = self._skip_if_unreachable("Cochrane (via PubMed)", lambda: cs.search_cochrane(client, topic, 5))
                self.assertTrue(outcome.records, f"no Cochrane reviews returned for {topic!r}")
                self.assertTrue(outcome.records)
                for record in outcome.records:
                    self.assertTrue(record.doi and record.doi.startswith(COCHRANE_DOI_PREFIX), record.doi)
                    self.assertEqual(record.url, cs.DOI_BASE + record.doi)
                    self.assertIn(record.evidence_type, ev.EVIDENCE_ORDER)
                    if record.evidence_type != ev.SYSTEMATIC:
                        # Not every Cochrane-journal item is a review (e.g. protocols).
                        self.assertTrue(any("does not tag" in n for n in record.notes), record.doi)

    def test_who_all_topics(self):
        # No subTest here: if WHO is unreachable, the first call skips the whole test once.
        client = who.IrisClient()
        for topic in TOPICS:
            outcome = self._skip_if_unreachable("WHO IRIS", lambda: who.search_who(client, topic, 5))
            for record in outcome.records:
                self.assertTrue(record.relevant, topic)
                self.assertTrue(record.url.startswith("https://iris.who.int/handle/"))

    def test_official_links_resolve(self):
        client = self._pubmed()
        outcome = self._skip_if_unreachable("PubMed", lambda: ps.search_pubmed(client, "sepsis guidelines", 2))
        for record in outcome.records:
            status = self._skip_if_unreachable("PubMed link host", lambda: _head_status(record.url))
            self.assertLess(status, 400, record.url)

    def test_unified_search_returns_four_sections(self):
        outcomes = self._skip_if_unreachable(
            "unified search",
            lambda: ms.search_all("diabetic ketoacidosis", 3, clients=ms.make_clients()),
        )
        self.assertEqual([o.source for o in outcomes], ["PubMed", "NCBI Bookshelf", "Cochrane Library", "WHO IRIS"])
        text = ms.format_unified("diabetic ketoacidosis", outcomes)
        for label in ["## Source: PubMed", "## Source: NCBI Bookshelf", "## Source: Cochrane Library", "## Source: WHO IRIS"]:
            self.assertIn(label, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

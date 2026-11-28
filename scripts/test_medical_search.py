"""Offline tests for medical_search.py. No network needed.

Run: python3 scripts/test_medical_search.py
"""

import json
import unittest

import medical_evidence as ev
import medical_search as ms
from ncbi_bookshelf import BookshelfResult, NCBIError


def _record(source, title, evidence, date="2024", relevance=1.0):
    return ev.MedicalRecord(
        source=source, source_id=title, title=title, authors=[], publication_date=date,
        last_updated=None, document_type="Test", evidence_type=evidence, url=None,
        doi=None, abstract=None, relevance=relevance, relevant=True, citation=title,
    )


def _runner(records, total=None, dropped=0):
    def run(query, key, n):
        outcome = ev.SearchOutcome(source=ms.SOURCE_LABELS[key], query=query)
        outcome.total_reported = len(records) if total is None else total
        outcome.records = list(records)
        outcome.dropped_irrelevant = dropped
        return outcome

    return run


def _failing(message):
    def run(query, key, n):
        raise NCBIError(message)

    return run


class UnifiedSearchTests(unittest.TestCase):
    def test_all_four_sources_are_queried_by_default(self):
        runners = {k: _runner([]) for k in ms.ALL_SOURCES}
        outcomes = ms.search_all("sepsis", 3, runners=runners)
        self.assertEqual([o.source for o in outcomes], ["PubMed", "NCBI Bookshelf", "Cochrane Library", "WHO IRIS"])

    def test_one_source_failing_does_not_stop_the_others(self):
        runners = {
            "pubmed": _runner([_record("PubMed", "Sepsis trial", ev.TRIAL)]),
            "bookshelf": _failing("NCBI request to esearch.fcgi failed: HTTP 500"),
            "cochrane": _runner([]),
            "who": _failing("WHO IRIS request failed: URLError"),
        }
        outcomes = ms.search_all("sepsis", 3, runners=runners)
        by_source = {o.source: o for o in outcomes}
        self.assertEqual(len(by_source["PubMed"].records), 1)
        self.assertIsNone(by_source["PubMed"].error)
        self.assertIn("HTTP 500", by_source["NCBI Bookshelf"].error)
        self.assertIn("URLError", by_source["WHO IRIS"].error)

    def test_unavailable_source_is_shown_in_output(self):
        runners = {"pubmed": _runner([]), "bookshelf": _runner([]), "cochrane": _runner([]),
                   "who": _failing("WHO IRIS request failed: URLError")}
        text = ms.format_unified("sepsis", ms.search_all("sepsis", 3, runners=runners))
        self.assertIn("## Source: WHO IRIS", text)
        self.assertIn("_Unavailable: WHO IRIS request failed: URLError_", text)

    def test_subset_of_sources(self):
        runners = {k: _runner([]) for k in ms.ALL_SOURCES}
        outcomes = ms.search_all("sepsis", 3, sources=["who", "cochrane"], runners=runners)
        self.assertEqual([o.source for o in outcomes], ["WHO IRIS", "Cochrane Library"])

    def test_unknown_source_is_refused(self):
        with self.assertRaises(ValueError):
            ms.search_all("sepsis", 3, sources=["google"], runners={})

    def test_query_without_medical_term_is_refused_before_any_request(self):
        called = []

        def spy(query, key, n):
            called.append(key)
            return ev.SearchOutcome(source="x", query=query)

        with self.assertRaises(ValueError):
            ms.search_all("guidelines", 3, runners={k: spy for k in ms.ALL_SOURCES})
        self.assertEqual(called, [])

    def test_output_groups_by_source_then_evidence_type(self):
        runners = {
            "pubmed": _runner([
                _record("PubMed", "PubMed trial", ev.TRIAL, "2020"),
                _record("PubMed", "PubMed review", ev.SYSTEMATIC, "2023"),
            ]),
            "bookshelf": _runner([]),
            "cochrane": _runner([]),
            "who": _runner([]),
        }
        text = ms.format_unified("sepsis", ms.search_all("sepsis", 3, runners=runners))
        self.assertLess(text.index("## Source: PubMed"), text.index("## Source: NCBI Bookshelf"))
        self.assertLess(text.index("### Systematic review"), text.index("### Clinical trial"))

    def test_relevance_order_is_kept_and_recency_does_not_reorder(self):
        # Newer is not treated as better: the more relevant older record stays first.
        runners = {"pubmed": _runner([_record("PubMed", "Older, more relevant", ev.GENERAL, "2010", relevance=1.0),
                                      _record("PubMed", "Newer, less relevant", ev.GENERAL, "2024", relevance=0.5)]),
                   "bookshelf": _runner([]), "cochrane": _runner([]), "who": _runner([])}
        text = ms.format_unified("sepsis", ms.search_all("sepsis", 3, runners=runners))
        self.assertLess(text.index("Older, more relevant"), text.index("Newer, less relevant"))

    def test_json_output_includes_every_source(self):
        runners = {k: _runner([]) for k in ms.ALL_SOURCES}
        payload = json.loads(ms.to_json("sepsis", ms.search_all("sepsis", 3, runners=runners)))
        self.assertEqual(len(payload["sources"]), 4)
        self.assertEqual(payload["query"], "sepsis")


class BookshelfAdapterTests(unittest.TestCase):
    def test_bookshelf_items_become_general_publications_with_official_summary_only(self):
        from unittest import mock

        item = BookshelfResult(
            uid="1", title="Sepsis and septic shock", record_type="chapter", book_title="Critical care",
            chapter_title=None, authors=["Smith J"], publication_date="2024", accession="NBK1",
            url="https://www.ncbi.nlm.nih.gov/books/NBK1/", summary=None, citation="Smith J. Sepsis.",
        )
        with mock.patch.object(ms, "search_bookshelf", return_value=(1, [item])):
            outcome = ms.bookshelf_outcome(None, "sepsis", 3)
        record = outcome.records[0]
        self.assertEqual(record.evidence_type, ev.GENERAL)
        self.assertEqual(record.document_type, "Book chapter")
        self.assertIsNone(record.abstract)  # no official summary, so none is shown
        self.assertEqual(record.abstract_scope, ev.SCOPE_NONE)
        self.assertEqual(record.url, "https://www.ncbi.nlm.nih.gov/books/NBK1/")


class OutputTests(unittest.TestCase):
    def test_empty_source_says_no_relevant_records(self):
        outcome = ev.SearchOutcome(source="PubMed", query="sepsis", total_reported=50, dropped_irrelevant=3)
        text = ms.format_unified("sepsis", [outcome])
        self.assertIn("No relevant records returned.", text)
        self.assertIn("3 dropped as unrelated", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Offline tests for cochrane_search.py. No network needed.

Run: python3 scripts/test_cochrane_search.py
"""

import unittest

import cochrane_search as cs
import medical_evidence as ev
from test_pubmed_search import EFETCH_XML, FakePubMed, make_client


class CochraneTests(unittest.TestCase):
    def test_search_is_limited_to_the_cochrane_journal(self):
        fake = FakePubMed()
        cs.search_cochrane(make_client(fake), "insulin type 1 diabetes", 5)
        self.assertIn("Cochrane+Database+Syst+Rev", fake.urls[0].replace("%22", "").replace('"', ""))
        self.assertIn("Journal%5D", fake.urls[0])

    def test_never_calls_the_cochrane_api_host(self):
        fake = FakePubMed()
        cs.search_cochrane(make_client(fake), "insulin type 1 diabetes", 5)
        for url in fake.urls:
            self.assertNotIn("cochrane.org", url)
            self.assertIn("eutils.ncbi.nlm.nih.gov", url)

    def test_records_are_labelled_cochrane_and_link_to_doi(self):
        outcome = cs.search_cochrane(make_client(FakePubMed()), "insulin type 1 diabetes", 5)
        self.assertEqual(outcome.records[0].source, "Cochrane Library")
        self.assertEqual(outcome.records[0].url, "https://doi.org/10.1002/14651858.CD012161.pub2")

    def test_abstract_only_and_pls_limitation_are_stated(self):
        outcome = cs.search_cochrane(make_client(FakePubMed()), "insulin type 1 diabetes", 5)
        notes = " ".join(outcome.records[0].notes)
        self.assertIn("Abstract only. Full text was not retrieved.", notes)
        self.assertIn("Plain Language Summary not retrieved", notes)

    def test_no_plain_language_summary_is_ever_invented(self):
        # The fixture abstract has no plain-language text; the record must not gain one.
        outcome = cs.search_cochrane(make_client(FakePubMed()), "insulin type 1 diabetes", 5)
        self.assertNotIn("plain language", outcome.records[0].abstract.lower())

    def test_dates_and_revision_are_kept_with_source_labels(self):
        record = cs.search_cochrane(make_client(FakePubMed()), "insulin type 1 diabetes", 5).records[0]
        self.assertEqual(record.publication_date, "2026 Jun 19")
        self.assertEqual(record.last_updated, "2026-11-24")
        self.assertIn("Last updated is the PubMed record revision date.", record.notes)

    def test_library_search_link_is_built_without_scraping(self):
        url = cs.cochrane_library_search_url("sepsis guidelines")
        self.assertTrue(url.startswith("https://www.cochranelibrary.com/search?"))
        self.assertIn("q=sepsis+guidelines", url)

    def test_empty_query_refused(self):
        with self.assertRaises(ValueError):
            cs.search_cochrane(make_client(FakePubMed()), "  ", 5)

    def test_non_review_cochrane_item_gets_a_check_note(self):
        # Same record, but PubMed tags it only as a journal article (e.g. a protocol).
        protocol_xml = EFETCH_XML.replace(
            b"<PublicationType>Systematic Review</PublicationType>", b""
        ).replace(b"<PublicationType>Meta-Analysis</PublicationType>", b"")
        outcome = cs.search_cochrane(make_client(FakePubMed(efetch=protocol_xml)), "insulin type 1 diabetes", 5)
        record = outcome.records[0]
        self.assertEqual(record.evidence_type, ev.GENERAL)
        self.assertTrue(any("does not tag" in n for n in record.notes))

    def test_systematic_review_has_no_check_note(self):
        outcome = cs.search_cochrane(make_client(FakePubMed()), "insulin type 1 diabetes", 5)
        self.assertFalse(any("does not tag" in n for n in outcome.records[0].notes))

    def test_constants_point_to_official_hosts(self):
        self.assertEqual(cs.DOI_BASE, "https://doi.org/")
        self.assertEqual(cs.COCHRANE_LIBRARY_BASE, "https://www.cochranelibrary.com/")


if __name__ == "__main__":
    unittest.main(verbosity=2)

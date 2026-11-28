"""Offline tests for pubmed_search.py. No network needed.

Run: python3 scripts/test_pubmed_search.py
"""

import unittest
import urllib.error

import medical_evidence as ev
import pubmed_search as ps
from ncbi_bookshelf import NCBIError

ESEARCH_JSON = b'{"esearchresult": {"count": "2", "idlist": ["1001", "1002"]}}'

EFETCH_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
 <PubmedArticle>
  <MedlineCitation>
   <PMID>1001</PMID>
   <DateRevised><Year>2026</Year><Month>11</Month><Day>24</Day></DateRevised>
   <Article>
    <Journal><Title>The Cochrane database of systematic reviews</Title>
     <JournalIssue><PubDate><Year>2026</Year><Month>Jun</Month><Day>19</Day></PubDate></JournalIssue></Journal>
    <ArticleTitle>Insulin analogues for type 1 diabetes.</ArticleTitle>
    <Abstract>
     <AbstractText Label="RATIONALE">Insulin analogues may help <i>adults</i> with diabetes.</AbstractText>
     <AbstractText Label="AUTHORS' CONCLUSIONS">Results are uncertain.</AbstractText>
    </Abstract>
    <AuthorList>
     <Author><LastName>Guo</LastName><ForeName>Y</ForeName><Initials>Y</Initials></Author>
     <Author><CollectiveName>Cochrane Metabolic Group</CollectiveName></Author>
    </AuthorList>
    <ELocationID EIdType="doi" ValidYN="Y">10.1002/14651858.CD012161.pub2</ELocationID>
    <PublicationTypeList>
     <PublicationType>Journal Article</PublicationType>
     <PublicationType>Systematic Review</PublicationType>
     <PublicationType>Meta-Analysis</PublicationType>
    </PublicationTypeList>
   </Article>
  </MedlineCitation>
  <PubmedData>
   <ArticleIdList>
    <ArticleId IdType="pubmed">1001</ArticleId>
    <ArticleId IdType="doi">10.1002/14651858.CD012161.pub2</ArticleId>
   </ArticleIdList>
  </PubmedData>
  <ReferenceList><Reference><ArticleIdList><ArticleId IdType="doi">10.9999/REFERENCE.ONLY</ArticleId></ArticleIdList></Reference></ReferenceList>
 </PubmedArticle>
 <PubmedArticle>
  <MedlineCitation>
   <PMID>1002</PMID>
   <Article>
    <Journal><Title>Lancet</Title>
     <JournalIssue><PubDate><MedlineDate>2019 Spring</MedlineDate></PubDate></JournalIssue></Journal>
    <ArticleTitle>Unrelated hip fracture outcomes.</ArticleTitle>
    <AuthorList><Author><LastName>Roe</LastName><Initials>A</Initials></Author></AuthorList>
    <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
   </Article>
  </MedlineCitation>
  <PubmedData><ArticleIdList><ArticleId IdType="pubmed">1002</ArticleId></ArticleIdList></PubmedData>
 </PubmedArticle>
</PubmedArticleSet>
"""


class FakePubMed:
    """Routes E-utilities URLs to canned bodies. Records every URL it saw."""

    def __init__(self, esearch=ESEARCH_JSON, efetch=EFETCH_XML):
        self.esearch = esearch
        self.efetch = efetch
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if "esearch.fcgi" in url:
            return self.esearch
        if "efetch.fcgi" in url:
            return self.efetch
        raise AssertionError("unexpected URL: " + url)


def make_client(transport, **kwargs):
    return ps.PubMedClient(transport=transport, sleep=lambda s: None, **kwargs)


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.records = ps.parse_pubmed_xml(EFETCH_XML)

    def test_fields_come_from_the_record_not_references(self):
        first = self.records[0]
        self.assertEqual(first.source_id, "1001")
        # The reference-list DOI must not be used as this record's DOI.
        self.assertEqual(first.doi, "10.1002/14651858.CD012161.pub2")
        self.assertEqual(first.url, "https://pubmed.ncbi.nlm.nih.gov/1001/")

    def test_abstract_keeps_labels_and_inline_markup_text(self):
        first = self.records[0]
        self.assertEqual(first.abstract_scope, ev.SCOPE_ABSTRACT)
        self.assertIn("Rationale: Insulin analogues may help adults with diabetes.", first.abstract)
        self.assertIn("Authors' conclusions: Results are uncertain.", first.abstract)

    def test_authors_and_collective_names(self):
        self.assertEqual(self.records[0].authors, ["Guo Y", "Cochrane Metabolic Group"])

    def test_dates_and_revision(self):
        self.assertEqual(self.records[0].publication_date, "2026 Jun 19")
        self.assertEqual(self.records[0].last_updated, "2026-11-24")
        self.assertEqual(self.records[1].publication_date, "2019 Spring")
        self.assertIsNone(self.records[1].last_updated)

    def test_evidence_and_document_type(self):
        self.assertEqual(self.records[0].evidence_type, ev.SYSTEMATIC)
        self.assertEqual(self.records[0].document_type, "Systematic Review")
        self.assertEqual(self.records[1].evidence_type, ev.GENERAL)

    def test_record_without_abstract_or_doi_stays_empty(self):
        second = self.records[1]
        self.assertIsNone(second.abstract)
        self.assertEqual(second.abstract_scope, ev.SCOPE_NONE)
        self.assertIsNone(second.doi)
        self.assertNotIn("doi:", second.citation)

    def test_bad_xml_is_reported(self):
        with self.assertRaises(NCBIError):
            ps.parse_pubmed_xml(b"<not-closed>")


class SearchTests(unittest.TestCase):
    def test_only_esearch_and_efetch_are_called(self):
        fake = FakePubMed()
        ps.search_pubmed(make_client(fake), "diabetic ketoacidosis", 5)
        self.assertEqual(len(fake.urls), 2)
        for url in fake.urls:
            self.assertTrue(
                "/esearch.fcgi?" in url or "/efetch.fcgi?" in url, url
            )
            self.assertNotIn("pmc", url.lower())
            self.assertNotIn("rettype=full", url)

    def test_unrelated_record_is_dropped_and_counted(self):
        outcome = ps.search_pubmed(make_client(FakePubMed()), "diabetic ketoacidosis", 5)
        self.assertEqual(outcome.total_reported, 2)
        # Neither fixture record mentions ketoacidosis, so both are dropped.
        self.assertEqual(outcome.dropped_irrelevant, 2)
        self.assertEqual(outcome.records, [])

    def test_relevant_record_is_kept(self):
        outcome = ps.search_pubmed(make_client(FakePubMed()), "insulin type 1 diabetes", 5)
        self.assertEqual([r.source_id for r in outcome.records], ["1001"])

    def test_article_filter_is_added_to_term(self):
        fake = FakePubMed()
        ps.search_pubmed(make_client(fake), "sepsis", 5, article_filter="systematic review[pt]")
        self.assertIn("systematic+review%5Bpt%5D", fake.urls[0])

    def test_query_without_medical_term_is_refused(self):
        with self.assertRaises(ValueError):
            ps.search_pubmed(make_client(FakePubMed()), "guidelines", 5)

    def test_empty_query_is_refused(self):
        with self.assertRaises(ValueError):
            ps.search_pubmed(make_client(FakePubMed()), "   ", 5)

    def test_max_results_is_capped(self):
        fake = FakePubMed()
        ps.search_pubmed(make_client(fake), "sepsis", 10_000)
        self.assertIn("retmax=100", fake.urls[0])


class ClientTests(unittest.TestCase):
    def test_api_key_never_appears_in_error_messages(self):
        def failing(url):
            raise urllib.error.HTTPError(url, 400, "bad", None, None)

        client = make_client(failing, api_key="SECRETKEY123", max_retries=0)
        with self.assertRaises(NCBIError) as ctx:
            client.esearch("sepsis")
        self.assertNotIn("SECRETKEY123", str(ctx.exception))

    def test_retries_on_server_error_then_succeeds(self):
        calls = {"n": 0}

        def flaky(url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.HTTPError(url, 503, "busy", None, None)
            return ESEARCH_JSON

        count, ids = make_client(flaky).esearch("sepsis")
        self.assertEqual((count, ids), (2, ["1001", "1002"]))
        self.assertEqual(calls["n"], 2)

    def test_non_json_is_reported(self):
        with self.assertRaises(NCBIError):
            make_client(lambda url: b"<html>oops</html>").esearch("sepsis")


if __name__ == "__main__":
    unittest.main(verbosity=2)

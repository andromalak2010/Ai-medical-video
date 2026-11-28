"""Offline tests for scripts/ncbi_bookshelf.py. No network access is needed.

The four query tests replay ESummary responses captured from the live books
database (scripts/fixtures/ncbi_bookshelf/*.json).

Run:  python3 scripts/test_ncbi_bookshelf.py
"""

import json
import os
import sys
import unittest
import urllib.error
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FIXTURES = os.path.join(HERE, "fixtures", "ncbi_bookshelf")

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


def load_fixture(name):
    with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as handle:
        return json.load(handle)


def replay(name):
    """Return a FakeNCBI that serves one captured live response."""
    data = load_fixture(name)
    summaries = data["esummary"]
    uids = summaries.get("uids", [])
    return FakeNCBI(ids=uids, summaries={u: summaries[u] for u in uids})


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

LACTMED_SECTION_RAW = {
    "uid": "5939282",
    "title": "Substance Identification",
    "pubdate": "2006/01/01 00:00",
    "accessionid": "NBK501304",
    "rtype": "sec",
    "bookaccessionid": "NBK501922",
    "chapteraccessionid": "NBK501304",
    "bookinfo": (
        '<Info><Path><Parent id="lactmed" role="source" type="book" uid="4565728">'
        "<Title>Drugs and Lactation Database (LactMed&#xAE;)</Title></Parent>"
        '<Parent id="LM430" role="document" type="chapter" uid="4569248">'
        "<Title>Clofazimine</Title></Parent>"
        '<Self id="LM430.s2" role="object" type="sec" uid="5939282">'
        "<Title>Substance Identification</Title></Self></Path></Info>"
    ),
}


class ParsingTests(unittest.TestCase):
    def test_book_record_has_link_and_citation(self):
        item = nb.normalize_record("1001", BOOK_RAW)
        self.assertEqual(item.record_type, "book")
        self.assertEqual(item.accession, "NBK99999")
        self.assertEqual(item.url, "https://www.ncbi.nlm.nih.gov/books/NBK99999/")
        self.assertEqual(item.authors, ["Smith J", "Lee K"])
        self.assertIn("Available from: https://www.ncbi.nlm.nih.gov/books/NBK99999/", item.citation)

    def test_chapter_detected_and_cited_within_book(self):
        item = nb.normalize_record("1002", CHAPTER_RAW)
        self.assertEqual(item.record_type, "chapter")
        self.assertEqual(item.book_title, "Pharmacology: Principles of Drug Action")
        self.assertIn("Pharmacology: Principles of Drug Action.", item.citation)
        self.assertIn("Doe, A", item.authors)

    def test_real_section_record_uses_bookinfo_context(self):
        item = nb.normalize_record("5939282", LACTMED_SECTION_RAW)
        self.assertEqual(item.record_type, "section")
        self.assertEqual(item.title, "Substance Identification")
        self.assertEqual(item.chapter_title, "Clofazimine")
        self.assertEqual(item.book_title, "Drugs and Lactation Database (LactMed®)")
        self.assertEqual(item.publication_date, "2006/01/01")
        self.assertEqual(item.url, "https://www.ncbi.nlm.nih.gov/books/NBK501304/")
        self.assertIn("In: Clofazimine.", item.citation)

    def test_table_appendix_part_are_not_labelled_as_chapters(self):
        # Regression: table/appendix/part records were previously typed as chapter or book.
        for rtype in ("table", "appendix", "part"):
            raw = {"title": "Table 2. Severity", "rtype": rtype, "accessionid": "NBK1"}
            self.assertEqual(nb.normalize_record("t", raw).record_type, rtype)

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

    def test_placeholder_date_is_flagged_not_removed(self):
        item = nb.normalize_record("5", {"title": "T", "pubdate": "2000/01/01 00:00"})
        self.assertEqual(item.publication_date, "2000/01/01")
        self.assertIn("placeholder", item.date_note)

    def test_many_authors_use_et_al(self):
        citation = nb.build_citation(["A", "B", "C", "D"], "T", None, None, None)
        self.assertTrue(citation.startswith("A; B; C, et al. T."))

    def test_book_repeating_its_title_is_not_a_chapter(self):
        raw = {"title": "Same", "booktitle": "same"}
        self.assertEqual(nb.normalize_record("3", raw).record_type, "book")

    def test_bookinfo_parse_error_falls_back_safely(self):
        item = nb.normalize_record("8", {"title": "T", "bookinfo": "<broken"})
        self.assertEqual(item.title, "T")
        self.assertIsNone(item.chapter_title)


class QueryBuilderTests(unittest.TestCase):
    def test_multiword_phrase_is_quoted(self):
        self.assertEqual(nb.build_query("diabetic ketoacidosis"), '"diabetic ketoacidosis"')

    def test_single_word_is_not_quoted(self):
        self.assertEqual(nb.build_query("insulin"), "insulin")

    def test_title_field_tag(self):
        self.assertEqual(nb.build_query("acute inflammation", field="title"),
                         '"acute inflammation"[Title]')

    def test_book_field_tag(self):
        self.assertEqual(nb.build_query("pharmacology", field="book"), "pharmacology[Book]")

    def test_chapter_field_uses_title_tag_because_chapter_tag_is_ignored_by_ncbi(self):
        self.assertEqual(nb.build_query("insulin", field="chapter"), "insulin[Title]")

    def test_subheading_is_added_to_untagged_query(self):
        self.assertEqual(
            nb.build_query("diabetic ketoacidosis", subheading="pathophysiology"),
            '"diabetic ketoacidosis" AND physiopathology[Subheading]',
        )

    def test_subheading_with_title_tag_is_rejected(self):
        # Verified: combining [Subheading] with [Title] returns zero results.
        with self.assertRaises(ValueError):
            nb.build_query("diabetic ketoacidosis", field="title", subheading="pathophysiology")

    def test_beta_blocker_synonym_maps_to_verified_mesh_term(self):
        self.assertEqual(nb.build_query("beta blockers"),
                         '"adrenergic beta-antagonists"[MeSH Terms]')

    def test_unknown_field_and_subheading_rejected(self):
        with self.assertRaises(ValueError):
            nb.build_query("x", field="ti")  # [ti] is silently ignored by NCBI
        with self.assertRaises(ValueError):
            nb.build_query("x", subheading="anatomy")

    def test_quotes_in_user_text_are_removed(self):
        self.assertEqual(nb.build_query('"acute inflammation"'), '"acute inflammation"')

    def test_query_terms_drop_stopwords_and_keep_word_forms(self):
        # No stemming: "ketoacidosis" must not become "ketoacidosi" in output.
        self.assertEqual(nb.query_terms("mechanism of action of insulin"),
                         ["mechanism", "action", "insulin"])
        self.assertEqual(nb.query_terms("diabetic ketoacidosis"),
                         ["diabetic", "ketoacidosis"])

    def test_plural_titles_match_singular_terms_by_prefix(self):
        self.assertTrue(nb._has_term("mechanism", nb._token_set("Cellular Mechanisms of Insulin")))
        self.assertTrue(nb._has_term("blockers", nb._token_set("Beta-Blockers")))


class ClassificationTests(unittest.TestCase):
    def test_reimbursement_review_is_health_technology_assessment(self):
        item = nb.normalize_record("x", {
            "title": "Tirzepatide (Mounjaro): Therapeutic area: Diabetes mellitus, type 2: Reimbursement Review",
            "rtype": "book", "bookaccessionid": "NBK1",
        })
        self.assertEqual(nb.classify_kind(item), "health_technology_assessment")

    def test_guideline_and_report_kinds(self):
        guide = nb.normalize_record("g", {"title": "Guidelines on insulin use for type 1 diabetes"})
        report = nb.normalize_record("r", {"title": "Workshop report on sepsis"})
        self.assertEqual(nb.classify_kind(guide), "guideline")
        self.assertEqual(nb.classify_kind(report), "report")

    def test_unmatched_title_is_unclassified_not_guessed(self):
        item = nb.normalize_record("u", {"title": "Diabetic Ketoacidosis", "rtype": "chapter"})
        self.assertEqual(nb.classify_kind(item), "unclassified")


class RelevanceTests(unittest.TestCase):
    def _assess(self, text, **raw):
        item = nb.normalize_record("x", raw)
        item.document_kind = nb.classify_kind(item)
        label, reason, _ = nb.assess_relevance(nb.query_terms(text), item)
        return label, reason

    def test_all_terms_in_title_is_likely_relevant(self):
        label, _ = self._assess("diabetic ketoacidosis", title="Diabetic Ketoacidosis", rtype="chapter")
        self.assertEqual(label, "likely_relevant")

    def test_no_term_in_title_is_not_relevant(self):
        label, reason = self._assess("diabetic ketoacidosis", title="Hypoglycemia management", rtype="chapter")
        self.assertEqual(label, "not_relevant")
        self.assertIn("no query term", reason)

    def test_terms_only_in_book_title_are_weak(self):
        label, _ = self._assess("insulin", title="Dosing overview", booktitle="Insulin Therapy", rtype="chapter")
        self.assertEqual(label, "weak")

    def test_reimbursement_document_never_counts_as_likely_relevant(self):
        label, reason = self._assess(
            "beta blockers",
            title="Beta-Blockers for hypertension: Reimbursement Review",
            rtype="book",
        )
        self.assertEqual(label, "weak")
        self.assertIn("HTA", reason)


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
        client = nb.EutilsClient(transport=fake, clock=clock.clock, sleep=clock.sleep)
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
        client = nb.EutilsClient(transport=FakeNCBI(ids=[]), clock=clock.clock, sleep=clock.sleep)
        for _ in range(4):
            client.esearch("x")
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
        total, _, results = nb.search_bookshelf(client, "anatomy", max_results=500)
        self.assertEqual(total, 299)
        self.assertEqual(len(results), 100)
        for url in fake.urls:
            if "esummary" in url:
                self.assertLessEqual(len(parse_qs(urlparse(url).query)["id"][0].split(",")), 100)

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

    def test_markdown_output_shows_query_relevance_and_missing_authors(self):
        item = nb.normalize_record("1001", BOOK_RAW)
        item.document_kind = nb.classify_kind(item)
        item.relevance, item.relevance_reason, _ = nb.assess_relevance(["pharmacology"], item)
        text = nb.format_markdown("pharmacology", "pharmacology[Title]", 1, [item])
        self.assertIn("Query sent to NCBI: `pharmacology[Title]`", text)
        self.assertIn("Relevance: likely_relevant", text)
        self.assertIn("https://www.ncbi.nlm.nih.gov/books/NBK99999/", text)
        self.assertIn("Citation:", text)


# ---------------------------------------------------------------- live-captured queries
#
# Each test replays a live ESummary response captured for the query named in
# the test. The assertions check the rules applied to that real metadata.


class DiabeticKetoacidosisPathophysiologyTests(unittest.TestCase):
    TEXT = "diabetic ketoacidosis"

    def test_query_uses_verified_subheading_filter(self):
        self.assertEqual(nb.build_query(self.TEXT, subheading="pathophysiology"),
                         '"diabetic ketoacidosis" AND physiopathology[Subheading]')

    def test_live_capture_returns_records_with_metadata_and_links(self):
        client = nb.EutilsClient(transport=replay("dka_pathophysiology"), sleep=lambda s: None)
        total, query, results = nb.search_bookshelf(
            client, self.TEXT, 10, subheading="pathophysiology")
        self.assertEqual(total, 4)
        self.assertEqual(len(results), 4)
        for item in results:
            self.assertTrue(item.url.startswith("https://www.ncbi.nlm.nih.gov/books/NBK"))
            self.assertEqual(item.authors, [])  # NCBI returned no authors; none invented

    def test_no_result_is_marked_likely_relevant_without_the_term_in_its_title(self):
        client = nb.EutilsClient(transport=replay("dka_pathophysiology"), sleep=lambda s: None)
        _, _, results = nb.search_bookshelf(client, self.TEXT, 10, subheading="pathophysiology")
        for item in results:
            if item.relevance == "likely_relevant":
                self.assertIn("ketoacidosis", (item.title or "").lower())


class InsulinMechanismOfActionTests(unittest.TestCase):
    TEXT = "insulin mechanism of action"

    def test_terms_drop_stopwords(self):
        self.assertEqual(nb.query_terms(self.TEXT), ["insulin", "mechanism", "action"])

    def test_live_capture_ranks_full_term_matches_first(self):
        client = nb.EutilsClient(transport=replay("insulin_mechanism"), sleep=lambda s: None)
        _, _, results = nb.search_bookshelf(client, "insulin mechanism of action", 20, field_name="title")
        labels = [item.relevance for item in results]
        self.assertEqual(labels, sorted(labels, key=lambda l: nb.RELEVANCE_RANK[l]))

    def test_title_missing_action_is_not_likely_relevant(self):
        client = nb.EutilsClient(transport=replay("insulin_mechanism"), sleep=lambda s: None)
        _, _, results = nb.search_bookshelf(client, "insulin mechanism of action", 20)
        for item in results:
            if "action" not in (item.title or "").lower():
                self.assertNotEqual(item.relevance, "likely_relevant")


class AcuteInflammationTests(unittest.TestCase):
    TEXT = "acute inflammation"

    def test_title_field_query_is_quoted_and_tagged(self):
        self.assertEqual(nb.build_query(self.TEXT, field="title"), '"acute inflammation"[Title]')

    def test_live_capture_flags_title_matches(self):
        client = nb.EutilsClient(transport=replay("acute_inflammation"), sleep=lambda s: None)
        total, query, results = nb.search_bookshelf(client, self.TEXT, 10, field_name="title")
        self.assertEqual(total, 3)
        self.assertEqual(query, '"acute inflammation"[Title]')
        by_title = {item.title: item for item in results}
        self.assertEqual(
            by_title["RESOLUTION OF ACUTE INFLAMMATION STIMULATES TISSUE REGENERATION"].relevance,
            "likely_relevant",
        )

    def test_title_match_is_lexical_only_and_says_so(self):
        client = nb.EutilsClient(transport=replay("acute_inflammation"), sleep=lambda s: None)
        _, _, results = nb.search_bookshelf(client, self.TEXT, 10, field_name="title")
        for item in results:
            self.assertIn("title", item.relevance_reason)


class BetaBlockersPharmacologyTests(unittest.TestCase):
    TEXT = "beta blockers"

    def test_synonym_query_is_verified_mesh_term(self):
        self.assertEqual(
            nb.build_query(self.TEXT, subheading="pharmacology"),
            '"adrenergic beta-antagonists"[MeSH Terms] AND pharmacology[Subheading]',
        )

    def test_live_capture_flags_health_technology_documents_as_weak(self):
        client = nb.EutilsClient(transport=replay("beta_blockers_pharmacology"), sleep=lambda s: None)
        _, _, results = nb.search_bookshelf(client, self.TEXT, 20, subheading="pharmacology")
        hta = [item for item in results if item.document_kind == "health_technology_assessment"]
        self.assertTrue(hta, "expected reimbursement or HTA documents in the live capture")
        for item in hta:
            self.assertNotEqual(item.relevance, "likely_relevant")

    def test_no_authors_are_invented_for_live_capture(self):
        client = nb.EutilsClient(transport=replay("beta_blockers_pharmacology"), sleep=lambda s: None)
        _, _, results = nb.search_bookshelf(client, self.TEXT, 20, subheading="pharmacology")
        for item in results:
            self.assertEqual(item.authors, [])
            self.assertNotIn("et al", item.citation)


if __name__ == "__main__":
    unittest.main(verbosity=2)

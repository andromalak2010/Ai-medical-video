"""Offline tests for the shared evidence helpers. No network needed.

Run: python3 scripts/test_medical_evidence.py
"""

import unittest

import medical_evidence as ev


class ClassificationTests(unittest.TestCase):
    def test_pubmed_systematic_review_and_meta_analysis(self):
        self.assertEqual(ev.classify_pubmed(["Systematic Review", "Meta-Analysis"]), ev.SYSTEMATIC)

    def test_pubmed_guideline_wins_over_other_types(self):
        self.assertEqual(
            ev.classify_pubmed(["Practice Guideline", "Systematic Review"]), ev.GUIDELINE
        )

    def test_pubmed_randomized_trial(self):
        self.assertEqual(
            ev.classify_pubmed(["Journal Article", "Randomized Controlled Trial"]), ev.TRIAL
        )

    def test_pubmed_observational(self):
        self.assertEqual(ev.classify_pubmed(["Cohort Studies"]), ev.OBSERVATIONAL)
        self.assertEqual(ev.classify_pubmed(["Case-Control Studies"]), ev.OBSERVATIONAL)

    def test_pubmed_case_report_and_editorial_are_general(self):
        self.assertEqual(ev.classify_pubmed(["Case Reports"]), ev.GENERAL)
        self.assertEqual(ev.classify_pubmed(["Editorial"]), ev.GENERAL)
        self.assertEqual(ev.classify_pubmed([]), ev.GENERAL)

    def test_pubmed_document_type_prefers_specific_label(self):
        types = ["Journal Article", "Systematic Review", "Meta-Analysis"]
        self.assertEqual(ev.pubmed_document_type(types, ev.SYSTEMATIC), "Systematic Review")
        self.assertEqual(ev.pubmed_document_type(["Journal Article"], ev.GENERAL), "Journal article")
        self.assertEqual(ev.pubmed_document_type([], ev.GENERAL), "Publication")

    def test_who_repository_type_field_is_trusted(self):
        group, doc, basis = ev.classify_who("Guideline", "Anything at all")
        self.assertEqual((group, basis), (ev.GUIDELINE, "repository type field"))
        self.assertEqual(doc, "Guideline")

    def test_who_title_heuristic_needs_clinical_keyword(self):
        group, _, basis = ev.classify_who(None, "WHO guidelines for the treatment of hypertension")
        self.assertEqual((group, basis), (ev.GUIDELINE, "title keywords (heuristic)"))

    def test_who_guideline_word_without_clinical_topic_is_not_a_clinical_guideline(self):
        group, _, _ = ev.classify_who(None, "Guidelines for research ethics committees")
        self.assertEqual(group, ev.GENERAL)

    def test_who_plain_report_is_general(self):
        group, doc, basis = ev.classify_who("Report", "Annual report of the Director-General")
        self.assertEqual((group, doc, basis), (ev.GENERAL, "Report", "repository type field"))

    def test_who_no_type_field_is_labelled(self):
        _, doc, basis = ev.classify_who(None, "Some publication")
        self.assertEqual((doc, basis), ("Publication", "no type given"))


class RelevanceTests(unittest.TestCase):
    def test_concept_groups_drop_document_words(self):
        groups = ev.concept_groups("sepsis guidelines")
        self.assertEqual(groups, [["sepsis", "septic"]])

    def test_abbreviation_expansion(self):
        groups = ev.concept_groups("DKA")
        self.assertIn("ketoacidosis", groups[0])

    def test_query_without_medical_term_is_refused(self):
        with self.assertRaises(ValueError):
            ev.require_concepts("guidelines and reviews")

    def test_short_query_needs_every_concept(self):
        # "diabetic ketoacidosis" has two concepts; the text has only one.
        score, passed = ev.assess_relevance(
            "diabetic ketoacidosis", ["Insulin use for type 1 diabetes in adults"]
        )
        self.assertFalse(passed)
        self.assertEqual(score, 0.5)

    def test_matching_text_passes(self):
        _, passed = ev.assess_relevance(
            "diabetic ketoacidosis", ["Management of diabetic ketoacidosis in adults"]
        )
        self.assertTrue(passed)

    def test_prefix_matching_catches_word_forms(self):
        _, passed = ev.assess_relevance("hypertension treatment", ["Hypertensive patients receiving therapy"])
        self.assertTrue(passed)

    def test_unrelated_text_fails(self):
        score, passed = ev.assess_relevance("sepsis", ["Hip fracture surgery outcomes"])
        self.assertEqual((score, passed), (0.0, False))

    def test_long_query_needs_two_thirds(self):
        # Three concepts: need at least 2 (primary plus one).
        _, passed = ev.assess_relevance("acute asthma management", ["Asthma exacerbation care"])
        self.assertFalse(passed)  # only asthma matched: 1 of 3 concepts
        _, passed = ev.assess_relevance("acute asthma management", ["Asthma exacerbation management"])
        self.assertTrue(passed)  # asthma + management: 2 of 3

    def test_empty_texts_are_ignored(self):
        _, passed = ev.assess_relevance("sepsis", [None, "", "Septic shock trial"])
        self.assertTrue(passed)


class DateOrderTests(unittest.TestCase):
    def test_month_names_sort_chronologically(self):
        self.assertEqual(ev.date_parts("2026 Jun 19"), (2026, 6, 19))
        self.assertEqual(ev.date_parts("2026 Aug 06"), (2026, 8, 6))
        self.assertLess(ev.date_parts("2026 Apr 21"), ev.date_parts("2026 Jun 19"))
        self.assertLess(ev.date_parts("2026 Jun 19"), ev.date_parts("2026 Aug 06"))

    def test_iso_and_slash_dates(self):
        self.assertEqual(ev.date_parts("2026-11-28"), (2026, 11, 28))
        self.assertEqual(ev.date_parts("2026/01/01"), (2026, 1, 1))

    def test_year_only_and_missing(self):
        self.assertEqual(ev.date_parts("2026"), (2026, 0, 0))
        self.assertEqual(ev.date_parts(None), (0, 0, 0))

    def test_grouping_keeps_relevance_order_not_date_order(self):
        # Callers sort by relevance; grouping must not undo that by re-sorting on date.
        old = ev.MedicalRecord("S", "1", "Old but more relevant", [], "2019 Mar 02", None, "d", ev.GENERAL, None, None, None)
        new = ev.MedicalRecord("S", "2", "New but less relevant", [], "2024 Nov 30", None, "d", ev.GENERAL, None, None, None)
        grouped = ev.group_by_evidence([old, new])
        self.assertEqual([r.title for r in grouped[ev.GENERAL]], ["Old but more relevant", "New but less relevant"])

    def test_newest_first_is_available_as_a_tie_breaker(self):
        old = ev.MedicalRecord("S", "1", "Old", [], "2019 Mar 02", None, "d", ev.GENERAL, None, None, None)
        new = ev.MedicalRecord("S", "2", "New", [], "2024 Nov 30", None, "d", ev.GENERAL, None, None, None)
        self.assertEqual([r.title for r in sorted([old, new], key=ev.sort_key_newest_first, reverse=True)], ["New", "Old"])

    def test_anchor_needed_not_just_generic_words(self):
        # "acute" and "management" alone must not satisfy "acute asthma management".
        _, passed = ev.assess_relevance("acute asthma management", ["Acute care management of septic shock"])
        self.assertFalse(passed)


class CitationAndFormatTests(unittest.TestCase):
    def test_citation_only_uses_present_fields(self):
        text = ev.make_citation([], "A title", None, None, None, None)
        self.assertEqual(text, "A title.")
        self.assertNotIn("doi:", text)
        self.assertNotIn("Available from", text)

    def test_citation_prefers_doi_then_url(self):
        with_doi = ev.make_citation(["Doe J"], "T", "J", "2020", "10.1/x", "https://u")
        self.assertIn("doi:10.1/x.", with_doi)
        self.assertNotIn("Available from", with_doi)
        with_url = ev.make_citation(["Doe J"], "T", "J", "2020", None, "https://u")
        self.assertIn("Available from: https://u", with_url)

    def test_missing_abstract_is_stated_not_invented(self):
        record = ev.MedicalRecord("S", "1", "T", [], None, None, "Journal article", ev.GENERAL, None, None, None)
        lines = ev.record_lines(1, record)
        self.assertIn("   - Abstract: not available from this source", lines)

    def test_markdown_groups_by_evidence_type(self):
        a = ev.MedicalRecord("S", "1", "Review A", [], "2025", None, "Systematic Review", ev.SYSTEMATIC, None, None, None)
        b = ev.MedicalRecord("S", "2", "Trial B", [], "2024", None, "RCT", ev.TRIAL, None, None, None)
        text = ev.format_records_markdown("Q", [], [a, b])
        self.assertLess(text.index("## Systematic review"), text.index("## Clinical trial"))
        self.assertIn("Review A", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

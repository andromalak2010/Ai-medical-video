"""Offline tests for who_guidelines.py. No network needed.

The fixture follows the DSpace 7 REST discovery response shape. WHO IRIS could
not be reached from the build environment, so these tests cannot prove the live
response matches. See references/who.md.

Run: python3 scripts/test_who_guidelines.py
"""

import json
import unittest
import urllib.error
import urllib.parse

import medical_evidence as ev
import who_guidelines as who
from ncbi_bookshelf import NCBIError


def _md(**fields):
    """DSpace metadata: {'dc.title': [{'value': ...}], ...}."""
    return {key: [{"value": value, "language": None}] for key, value in fields.items()}


def _item(uuid, handle, metadata, last_modified="2025-03-04T10:00:00Z"):
    return {
        "_embedded": {
            "indexableObject": {
                "uuid": uuid,
                "name": metadata.get("dc.title", [{}])[0]["value"],
                "handle": handle,
                "type": "item",
                "lastModified": last_modified,
                "metadata": metadata,
            }
        }
    }


GUIDELINE_ITEM = _item(
    "uuid-guideline",
    "10665/345751",
    {
        "dc.title": [{"value": "WHO guidelines for diabetes management and treatment"}],
        "dc.contributor.author": [{"value": "World Health Organization"}],
        "dc.date.issued": [{"value": "2024-07-02"}],
        "dc.type": [{"value": "Guideline"}],
        "dc.description.abstract": [{"value": "These recommendations cover diabetes treatment in adults."}],
        "dc.identifier.doi": [{"value": "10.1234/who.example.1"}],
        "dc.description.version": [{"value": "Version 2"}],
        "dc.subject": [{"value": "diabetes mellitus"}],
        "dc.publisher": [{"value": "World Health Organization"}],
    },
)

REPORT_ITEM = _item(
    "uuid-report",
    "10665/999",
    {
        "dc.title": [{"value": "Global diabetes report"}],
        "dc.date.issued": [{"value": "2016"}],
        "dc.type": [{"value": "Report"}],
    },
)

HEURISTIC_ITEM = _item(
    "uuid-heuristic",
    "10665/777",
    {
        "dc.title": [{"value": "Guidelines for hypertension treatment in primary care"}],
        "dc.date.issued": [{"value": "2021"}],
    },
)

UNRELATED_ITEM = _item(
    "uuid-unrelated",
    "10665/111",
    {
        "dc.title": [{"value": "Immunization coverage report"}],
        "dc.type": [{"value": "Report"}],
    },
)

FIXTURE = {
    "_embedded": {
        "searchResult": {
            "_embedded": {"objects": [GUIDELINE_ITEM, REPORT_ITEM, HEURISTIC_ITEM, UNRELATED_ITEM]},
            "page": {"size": 20, "totalElements": 4, "number": 0},
        }
    }
}


class FakeIris:
    def __init__(self, body=None):
        self.body = json.dumps(body if body is not None else FIXTURE).encode()
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return self.body


def make_client(transport, **kwargs):
    return who.IrisClient(transport=transport, sleep=lambda s: None, **kwargs)


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.items = {it["uuid"]: it for it in (o["_embedded"]["indexableObject"] for o in FIXTURE["_embedded"]["searchResult"]["_embedded"]["objects"])}

    def test_guideline_fields_are_taken_from_metadata(self):
        record, subjects = who.normalize_item(GUIDELINE_ITEM["_embedded"]["indexableObject"])
        self.assertEqual(record.source, "WHO IRIS")
        self.assertEqual(record.title, "WHO guidelines for diabetes management and treatment")
        self.assertEqual(record.authors, ["World Health Organization"])
        self.assertEqual(record.publication_date, "2024-07-02")
        self.assertEqual(record.doi, "10.1234/who.example.1")
        self.assertEqual(record.url, "https://iris.who.int/handle/10665/345751")
        self.assertEqual(record.abstract, "These recommendations cover diabetes treatment in adults.")
        self.assertEqual(record.abstract_scope, ev.SCOPE_ABSTRACT)
        self.assertEqual(record.evidence_type, ev.GUIDELINE)
        self.assertIn("Version 2", " ".join(record.notes))
        self.assertIn("diabetes mellitus", subjects)

    def test_repository_date_is_labelled_not_presented_as_revision(self):
        record, _ = who.normalize_item(GUIDELINE_ITEM["_embedded"]["indexableObject"])
        self.assertEqual(record.last_updated, "2025-03-04")
        self.assertIn("not a WHO revision date", " ".join(record.notes))

    def test_missing_fields_stay_missing(self):
        record, _ = who.normalize_item(REPORT_ITEM["_embedded"]["indexableObject"])
        self.assertIsNone(record.doi)
        self.assertIsNone(record.abstract)
        self.assertEqual(record.abstract_scope, ev.SCOPE_NONE)
        self.assertEqual(record.authors, [])
        self.assertNotIn("doi:", record.citation)

    def test_report_is_not_a_guideline(self):
        record, _ = who.normalize_item(REPORT_ITEM["_embedded"]["indexableObject"])
        self.assertEqual(record.evidence_type, ev.GENERAL)
        self.assertIn("Type basis: repository type field", " ".join(record.notes))

    def test_title_heuristic_is_labelled_as_heuristic(self):
        record, _ = who.normalize_item(HEURISTIC_ITEM["_embedded"]["indexableObject"])
        self.assertEqual(record.evidence_type, ev.GUIDELINE)
        self.assertIn("title keywords (heuristic)", " ".join(record.notes))

    def test_total_reported_is_read(self):
        self.assertEqual(who._total(FIXTURE), 4)

    def test_malformed_payload_yields_no_items(self):
        self.assertEqual(who._objects({"_embedded": {"searchResult": {"_embedded": {"objects": [{}]}}}}), [])


class SearchTests(unittest.TestCase):
    def test_request_targets_discovery_endpoint_only(self):
        fake = FakeIris()
        who.search_who(make_client(fake), "diabetes", 5)
        url = fake.urls[0]
        self.assertTrue(url.startswith("https://iris.who.int/server/api/discover/search/objects?"))
        self.assertIn("dsoType=ITEM", url)
        self.assertNotIn("bitstream", url)  # no PDF or full-text downloads

    def test_unrelated_item_is_dropped(self):
        outcome = who.search_who(make_client(FakeIris()), "diabetes", 5)
        titles = [r.title for r in outcome.records]
        self.assertNotIn("Immunization coverage report", titles)
        self.assertGreaterEqual(outcome.dropped_irrelevant, 1)

    def test_guidelines_only_keeps_clinical_guidelines(self):
        outcome = who.search_who(make_client(FakeIris()), "diabetes", 5, guidelines_only=True)
        self.assertTrue(outcome.records)
        self.assertTrue(all(r.evidence_type == ev.GUIDELINE for r in outcome.records))

    def test_empty_query_refused(self):
        with self.assertRaises(ValueError):
            who.search_who(make_client(FakeIris()), "", 5)

    def test_query_without_medical_term_refused(self):
        with self.assertRaises(ValueError):
            who.search_who(make_client(FakeIris()), "guidelines and reviews", 5)

    def test_transport_error_is_reported_without_url(self):
        def down(url):
            raise urllib.error.URLError("proxy refused")

        with self.assertRaises(NCBIError) as ctx:
            make_client(down, max_retries=0).search_objects("diabetes")
        self.assertNotIn("iris.who.int", str(ctx.exception))

    def test_non_json_is_reported(self):
        with self.assertRaises(NCBIError):
            make_client(lambda url: b"<html>blocked</html>").search_objects("diabetes")

    def test_base_url_is_overridable_for_a_different_iris_host(self):
        client = who.IrisClient(base="https://example.test/server/api", transport=lambda u: b"{}")
        self.assertTrue(client._base.endswith("/"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

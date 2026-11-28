# Cochrane Library: search integration

Official sources
- Cochrane Library: https://www.cochranelibrary.com/
- Cochrane documentation: https://documentation.cochrane.org/
- Cochrane API reference (login required): https://api.cochrane.org/api-docs/index.html
- DOI resolver used for links: https://doi.org/

## How the integration works

Cochrane systematic reviews are indexed in PubMed under the journal
"Cochrane Database Syst Rev". `scripts/cochrane_search.py` runs a PubMed search
limited to that journal, through NCBI E-utilities (the same official API as the
PubMed and Bookshelf scripts). No login, and no scraping of Cochrane web pages.

What each result contains
- Title, authors, publication date, and the PubMed "last updated" date. That
  last date is the PubMed record revision date, not necessarily the Cochrane
  review update date. The output labels it that way.
- Document type from PubMed (Systematic Review, Network Meta-Analysis, and so
  on). Evidence group: systematic review / meta-analysis.
- DOI and an official link, https://doi.org/<DOI>.
- The official abstract from PubMed, only when PubMed supplies one.
- A citation built only from fields that exist.

## Authentication: what the investigation found

I looked at the Cochrane API documentation before building anything. The findings:

- The Cochrane API is protected by OpenID Connect, implemented with Keycloak.
  Clients are registered by Cochrane and receive a client ID and, for
  confidential clients, a secret.
- The token endpoint takes the client credentials as HTTP Basic auth.
- The API accepts either HTTP Basic or a Bearer access token. A missing or bad
  credential returns HTTP 401.
- Cochrane provides a test environment at `test-api.cochrane.org`.
- Access depends on permissions that the API checks for each token.

What I could not verify
- The API reference page (`api.cochrane.org/api-docs/index.html`) and
  `documentation.cochrane.org` were blocked from the build environment, so I
  could not read the endpoint list or the token URL.
- The token endpoint and endpoint paths are not public, so the integration does
  not call the Cochrane API. Guessing them would break the rule against
  inventing endpoints.

Decision: no authenticated call is implemented. If Cochrane issues credentials,
the next step is to read the Swagger page from a machine that can reach it, then
add a client that uses the documented token and endpoints. Keep the secret in an
environment variable, never in code or in output.

## Plain Language Summaries and full text

- A Plain Language Summary (PLS) is not part of the PubMed record, so the
  script does not retrieve it. Each result says so and links to the DOI page.
- Full text is never requested. Each result states "Abstract only".
- Nothing here bypasses a subscription or access restriction.

## Relevance and limits

- The PubMed journal filter guarantees the source. The shared relevance check
  (`scripts/medical_evidence.py`) then drops results whose title and abstract do
  not match the query's medical concepts. The count of dropped results is shown.
- Some items in the Cochrane journal are not reviews, for example protocols.
  PubMed does not always tag them as systematic reviews. Those items are
  classified as general publications and carry a note asking you to check the DOI
  page.
- Not every Cochrane review is in PubMed under this journal name. Coverage is
  whatever PubMed indexes.

## Commands

```bash
python3 scripts/cochrane_search.py search "diabetic ketoacidosis" --max 5
python3 scripts/cochrane_search.py search "hypertension treatment" --max 5 --json
python3 scripts/cochrane_search.py link "sepsis"     # prints the Cochrane Library search link only
```

Optional environment variables (shared with the other NCBI scripts):
`NCBI_API_KEY` (higher rate limit) and `NCBI_TOOL_EMAIL` (contact address NCBI
may use).

## Tests

```bash
python3 scripts/test_cochrane_search.py       # offline
python3 scripts/test_live_medical_sources.py  # live, needs network
```

The Cochrane Library search link format (`/search?q=...`) was not verified against
the live site. It is provided as a convenience link only, and the script does not
fetch it.

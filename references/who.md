# WHO publications and guidelines: search integration

Official sources
- WHO guidelines: https://www.who.int/publications/guidelines
- WHO publications: https://www.who.int/publications
- WHO IRIS (institutional repository): https://iris.who.int/
- WHO website API help (listed, not used): https://www.who.int/api/default/publications/sfhelp

## Status: not yet verified against the live server

The WHO integration is built and tested offline, but it has not been run against
WHO's live servers. The build environment could not reach `iris.who.int` or
`who.int` (the network proxy refused those hosts). The live test for WHO
therefore skips with a stated reason. Run the live test from a machine that can
reach WHO before relying on the output:

```bash
python3 scripts/test_live_medical_sources.py -k who
```

## Which API is used, and why

- Search uses the DSpace REST discovery endpoint, which WHO IRIS is expected to
  expose: `https://iris.who.int/server/api/discover/search/objects`. IRIS is a
  DSpace repository, and the response shape in `scripts/test_who_guidelines.py`
  follows the DSpace 7 REST contract. That shape is an assumption until a live
  call confirms it.
- The WHO website API (`who.int/api/.../sfhelp`) was not used. Its help pages
  list "Publications", but I could not retrieve parameters, response formats or
  authentication rules, so its behaviour is unknown. Nothing was guessed.
- The base URL can be changed with the `WHO_IRIS_API_BASE` environment variable
  if WHO's IRIS instance differs.

## Telling guidelines apart from other documents

Each item gets an evidence group and a basis, shown in the output as
"Type basis":

| Basis | Meaning | Strength |
|---|---|---|
| repository type field | The item's own `dc.type` value (for example "Guideline", "Report", "Systematic review") | Stronger |
| title keywords (heuristic) | The title contains "guideline(s)" and a clinical word (treatment, management, prevention, diagnosis, vaccine, and so on) | Weaker. Check it. |
| no type given | Nothing in the record says what the item is | Unknown |

Use `--guidelines-only` to keep only items classed as clinical guidelines.

## What each result contains

- Title, authors (from `dc.contributor.author`), issue date (`dc.date.issued`).
- Document type (`dc.type`).
- Document version, when IRIS records one.
- "Last updated" is the IRIS repository modification date. It is not a WHO
  revision date, and the output says so.
- DOI, when the item has one.
- Official IRIS handle link (`https://iris.who.int/handle/...`).
- The official abstract (`dc.description.abstract`), only when IRIS supplies it.
- A citation built only from fields that exist.

## What it never does

- Download PDFs or full text. Full-text files are reached only through the
  official item link.
- Invent an author, date, DOI, abstract or recommendation. A missing field stays
  missing and is shown as "not available".
- Treat a newer document as better evidence.

## Relevance

WHO results pass the same relevance check as the other sources. Documents must
match the query's medical concepts in the title, abstract or subject terms.
Unrelated items are dropped, and the count is shown.

## Commands

```bash
python3 scripts/who_guidelines.py search "diabetes" --max 5
python3 scripts/who_guidelines.py search "sepsis" --guidelines-only
python3 scripts/who_guidelines.py search "vaccination" --json
```

## Tests

```bash
python3 scripts/test_who_guidelines.py        # offline, uses a DSpace-shaped fixture
python3 scripts/test_live_medical_sources.py  # live; WHO part skips if unreachable
```

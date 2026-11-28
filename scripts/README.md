# Scripts / السكريبتات

## NCBI Bookshelf search

`ncbi_bookshelf.py` searches NCBI Bookshelf (medical books and chapters) through the official NCBI E-utilities API (`db=books`). Standard library only, so nothing needs to be installed.

Sources:
- Bookshelf: https://www.ncbi.nlm.nih.gov/books/
- Bookshelf help: https://www.ncbi.nlm.nih.gov/books/NBK25500/
- E-utilities usage: https://www.ncbi.nlm.nih.gov/books/NBK25497/

### Usage

```bash
python3 scripts/ncbi_bookshelf.py topics
python3 scripts/ncbi_bookshelf.py search --topic pathophysiology --max 5
python3 scripts/ncbi_bookshelf.py search --topic "emergency medicine"
python3 scripts/ncbi_bookshelf.py search "heart failure" --json
python3 scripts/ncbi_bookshelf.py search --topic anatomy --raw   # raw NCBI JSON
```

Preset topics: `pathophysiology`, `pharmacology`, `anatomy`, `physiology`, `emergency-medicine`.

### Output

Each result has the title, record type (book or chapter), book title for chapters, authors, date, NBK link, and a citation. A summary appears only when NCBI supplies an official text field for that record. Nothing is generated, so a missing field stays missing.

### Rules built into the script

- **No full-text downloads.** The script uses only ESearch and ESummary. It never calls EFetch or requests book pages. A test enforces this.
- **Rate limits.** 3 requests per second without an API key, 10 with one. Retries use exponential backoff on 429 and 5xx responses. Each run returns at most 100 results.
- **Optional environment variables:**
  - `NCBI_API_KEY`: a free NCBI key, which raises the rate limit.
  - `NCBI_TOOL_EMAIL`: a contact address NCBI can use if your traffic causes problems. It is not sent unless you set it.

### Tests

Offline tests, no network needed:

```bash
python3 scripts/test_ncbi_bookshelf.py
```

### Before relying on the output

The field names used in `normalize_record()` follow the public E-utilities format. Run `search --topic <name> --raw` once against the live API to confirm them, then adjust the field list if NCBI's JSON differs.

---

## Medical evidence search: PubMed, Cochrane, WHO, and one unified search

Four official sources, one shared result format. Standard library only.

| Source | Script | Official source | Status |
|---|---|---|---|
| PubMed | `pubmed_search.py` | NCBI E-utilities, db=pubmed | Live-tested |
| NCBI Bookshelf | `ncbi_bookshelf.py` (unchanged) | NCBI E-utilities, db=books | Live-tested |
| Cochrane Library | `cochrane_search.py` | Cochrane reviews indexed in PubMed | Live-tested |
| WHO IRIS | `who_guidelines.py` | iris.who.int (DSpace REST) | **Not yet verified live** |

Details: `references/cochrane.md` and `references/who.md`.

### One command for all four sources

```bash
python3 scripts/medical_search.py "diabetic ketoacidosis" --max 5
python3 scripts/medical_search.py "sepsis guidelines" --sources who,cochrane
python3 scripts/medical_search.py "hypertension treatment" --json
```

Results are grouped by source, then by evidence type. A source that fails is
shown as "Unavailable" with its reason, and the other sources still return.

### Each source on its own

```bash
python3 scripts/pubmed_search.py search "diabetic ketoacidosis" --max 5
python3 scripts/pubmed_search.py search "sepsis" --filter "systematic review[pt]"
python3 scripts/ncbi_bookshelf.py search --topic pathophysiology --max 5
python3 scripts/cochrane_search.py search "acute asthma management" --max 5
python3 scripts/cochrane_search.py link "sepsis"
python3 scripts/who_guidelines.py search "diabetes" --max 5
python3 scripts/who_guidelines.py search "sepsis" --guidelines-only
```

### Evidence groups

Every result is put in one group, in this order:
1. Systematic review / meta-analysis
2. Clinical guideline
3. Observational study
4. Clinical trial
5. General publication (includes textbook content, case reports, editorials)

Grouping comes from the source's own publication-type field. A WHO item with no
type field is classed by its title and marked "heuristic" in its notes.

### Rules built into these scripts

- **Abstracts and metadata only.** No full text is downloaded. PubMed is read
  with ESearch and EFetch (abstracts), never PMC. Bookshelf uses ESearch and
  ESummary only, as before.
- **Official text only.** An abstract is shown only when the source supplies it.
  Nothing is generated or paraphrased. A missing field is shown as missing.
- **Relevance check.** A result must match the query's medical concept. Short
  queries need every concept, and longer ones need two thirds. Generic words
  such as "acute", "management" or "treatment" cannot match on their own. Dropped
  results are counted and shown.
- **No quality ranking by date.** Items inside a group are ordered by relevance,
  then newest first as a tie-breaker. A newer publication is not treated as
  better evidence.
- **Rate limits.** NCBI: 3 requests per second without `NCBI_API_KEY`, 10 with
  it. WHO: a conservative 2 per second. Failed requests retry with backoff on
  429 and 5xx.
- **Secrets.** `NCBI_API_KEY` is read from the environment and never printed.
  Error messages leave out URLs, since those can carry the key.

### Tests

```bash
# Offline (no network needed)
python3 scripts/test_ncbi_bookshelf.py
python3 scripts/test_medical_evidence.py
python3 scripts/test_pubmed_search.py
python3 scripts/test_cochrane_search.py
python3 scripts/test_who_guidelines.py
python3 scripts/test_medical_search.py

# Live (needs network; unreachable sources are skipped with a reason)
python3 scripts/test_live_medical_sources.py
```

The live suite covers the five required topics: diabetes mellitus guidelines,
diabetic ketoacidosis, hypertension treatment, sepsis guidelines, and acute
asthma management. Each topic checks that results are relevant and that links
have the expected official form. Link status checks need access to
`pubmed.ncbi.nlm.nih.gov` and are skipped where that host is blocked.

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

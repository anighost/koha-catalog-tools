# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Tool Does

`clean_catalog.py` is a single-script tool that converts a messy library catalog spreadsheet (CSV/XLSX) into:
- A **MARC21 binary file** (`.mrc`) for direct import into Koha ILS
- A **cleaned audit Excel file** showing all transformations applied per row
- An **error Excel file** listing rejected rows (missing title/author)

The script is self-installing: it auto-installs `pymarc` and `openpyxl` on first run.

## Running the Script

```bash
# Place your input CSV next to the script, then:
python clean_catalog.py
```

The input filename is hardcoded at the top: `INPUT_FILE = 'catalog_to_be_cleaned_v1.csv'`. Change this constant when switching input files.

Outputs are timestamped and written to the working directory. `.csv`, `.xlsx`, and `.mrc` files are git-ignored.

## Persistent State: `koha_session_meta.json`

This JSON file is the heart of the tool's intelligence. It persists across runs and contains four dictionaries:

| Key | Purpose |
|-----|---------|
| `last_primary_barcode` | Tracks the highest `10xxxxx` barcode used; auto-increments for rows with no barcode |
| `synonyms_publisher` | Maps variant publisher names → canonical form (case-insensitive substring match) |
| `synonyms_author` | Maps variant author spellings → canonical form (all-words match, applied pre-inversion) |
| `synonyms_keywords` | Maps Bengali romanization variants → standard spelling via regex word-boundary replacement in titles |
| `series_overrides` | Maps `"SeriesTitle|AuthorName"` composite keys → canonical series name |

To add new synonyms or overrides, edit `koha_session_meta.json` directly — no code changes needed.

## Column Layout

The script uses 0-indexed column positions (defined as constants at the top). The expected input columns are:

| Constant | Index | Field |
|----------|-------|-------|
| `COL_ISBN` | 0 | ISBN |
| `COL_LANG` | 1 | Language |
| `COL_AUTHOR` | 2 | Author |
| `COL_TITLE` | 3 | Title |
| `COL_SUBTITLE` | 4 | Subtitle |
| `COL_EDITION` | 6 | Edition |
| `COL_PLACE` | 7 | Place of publication |
| `COL_PUBLISHER` | 8 | Publisher |
| `COL_YEAR` | 9 | Publication year |
| `COL_PAGES` | 10 | Pages |
| `COL_SERIES` | 12 | Series |
| `COL_SUBJECTS` | 14–18 | Up to 5 subject headings |
| `COL_ITEM_TYPE` | 25 | Koha item type code |
| `COL_BRANCH_HOME` | 28 | Home branch code |
| `COL_BRANCH_HOLD` | 29 | Holding branch code |
| `COL_DATE` | 31 | Acquisition date |
| `COL_COST` | 33 | Item cost |
| `COL_CALL_NO` | 34 | Call number |
| `COL_BARCODE` | 35 | Primary barcode |
| `COL_NOTE` | 36 | Notes |
| `COL_COPY2/3/4` | 37–39 | Copy 2/3/4 trigger columns |

## Key Business Logic

- **Barcode scheme**: Primary barcodes start with `10`. Copy barcodes replace the first two digits: copy 2 → `11`, copy 3 → `12`, copy 4 → `13`. E.g., primary `100045` → copy2 `110045`.
- **Author inversion**: "Firstname Lastname" → "Lastname, Firstname" unless already comma-separated. Synonym matching always happens *before* inversion.
- **Author signed copies**: If the note field contains "author signed" (case-insensitive), item type is forced to `ASB`.
- **Default branch**: `DFL` if branch fields are empty.
- **Default call number**: `891` (Bengali literature class) if missing.
- **Default language**: Bengali (`ben`) if language field is unrecognized.
- **MARC tags generated**: 020, 041, 100, 245, 250, 260, 300, 500, 650 (×5), 830, 942, 952.

---

## Catalog Web App (`catalog-app/`)

A Flask web app that wraps `clean_catalog.py` with a browser UI: upload → dedup review → one-click Koha import.

### Running locally

```bash
cd catalog-app
pip install flask gunicorn filelock openpyxl pymarc rapidfuzz
python app.py          # runs on http://localhost:5050
```

Default password: `changeme` (override with `CATALOG_PASSWORD` env var).

### Architecture

```
catalog-app/
├── app.py                  # Flask routes, auth, dedup logic, session management
├── catalog_engine.py       # Subprocess wrapper around clean_catalog.py
├── dedup_registry.db       # SQLite — master ledger of all processed books (auto-created)
├── koha_session_meta.json  # Symlinked or copied from repo root — barcode counter + synonyms
├── templates/
│   ├── base.html           # Shared layout, CSS design tokens, navbar
│   ├── login.html          # Password gate
│   ├── upload.html         # File picker (drag-and-drop)
│   ├── review.html         # Editable table — main UX screen
│   └── result.html         # Import summary + download links
├── uploads/                # Incoming Gronthee files (auto-cleaned after 7 days)
├── sessions/               # JSON session files keyed by sid (auto-cleaned after 7 days)
└── output/                 # Generated MARC + audit files (auto-cleaned after 7 days)
```

### Key files

**`app.py`** — all application logic:
- `init_db()` — creates SQLite `books` table + `idx_dedup (title_norm, author_norm)` + `idx_barcode` unique indexes
- `lookup_dup()` — 3-stage dedup: ISBN exact → title+author exact → rapidfuzz fuzzy (threshold 80%/72%)
- `build_review_rows()` — parses upload, pre-normalises via `koha_session_meta.json` synonyms, runs dedup on every row
- `register_books()` — inserts processed books; detects barcode mismatches on re-runs
- `process()` route — splits rows into new/copy/skip, calls `catalog_engine.run()`, merges MARC, calls `import_to_koha()`
- `import_to_koha()` — shells out to `sudo koha-shell <instance> -c bulkmarcimport.pl ...`
- `/health` — unauthenticated uptime probe; returns `{"status":"ok","db":true}`
- `/heartbeat` — authenticated ping; review page calls this every 5 min to keep session alive

**`catalog_engine.py`**:
- `run(input_path)` — patches `INPUT_FILE`/`STATE_FILE`/`OUTPUT_DIR` constants in `clean_catalog.py`, runs it as a subprocess, returns paths to `.mrc`, audit XLSX, errors XLSX
- `generate_copy_marc(fields, copy_barcode, copy_num)` — builds a minimal MARC record for adding a physical copy to an existing bib
- `extract_processed_books(audit_path)` — reads audit XLSX to get isbn/author/title/publisher/year/barcode for registry insertion

### Dedup logic

Three-stage lookup per uploaded row:
1. **ISBN exact match** — most reliable; bypasses title/author comparison
2. **Normalized title + author exact match** — `normalize()` lowercases + strips punctuation; `normalize_author()` sorts words to handle name inversion
3. **Fuzzy match** (rapidfuzz `token_sort_ratio`) — title ≥ 80%, author ≥ 72%, combined score weighted 65/35; skipped if Bengali/English volume markers differ (`খণ্ড 2` ≠ `খণ্ড 3`)

Row statuses: `NEW` · `DUPLICATE` · `FUZZY` · `ERROR`

FUZZY rows **require an explicit action** — the dropdown defaults to `— Select action —` and blocks the Process button until resolved. Options: Skip / Add as Copy 2–4 / Import as new book.

### Barcode scheme

Mirrors `clean_catalog.py`:
- Primary: `10XXXX`
- Copy 2: `11XXXX`, Copy 3: `12XXXX`, Copy 4: `13XXXX`

Copy barcode assignment is atomic: `FileLock` + SQLite `BEGIN EXCLUSIVE` prevents two concurrent uploads from assigning the same copy barcode.

### Session model

Each upload gets a UUID `sid`. State is stored in `sessions/<sid>.json` (not in the Flask cookie). The Flask session only carries `auth=True`. Sessions expire after 4 hours (cookie) but session files persist 7 days.

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `CATALOG_PASSWORD` | `changeme` | Shared login password |
| `FLASK_SECRET_KEY` | random (warns) | Flask session signing key — set in production |
| `KOHA_INSTANCE` | `dishari_lib` | Koha instance name passed to `koha-shell` |
| `KOHA_MATCH_RULE` | `STRICT_CLE` | Match rule name for `bulkmarcimport.pl` |
| `CATALOG_SCRIPT` | `../scripts/clean_catalog.py` | Path to `clean_catalog.py` |
| `CATALOG_META` | `./koha_session_meta.json` | Path to `koha_session_meta.json` |

### Deployment (production)

```bash
# Gunicorn — single worker required (barcode state is file-locked, not thread-safe)
gunicorn -w 1 -b 127.0.0.1:5050 app:app

# Sudoers rule for Koha import (add to /etc/sudoers.d/catalog-app)
dishari ALL=(root) NOPASSWD: /usr/sbin/koha-shell dishari_lib -c *
```

Apache proxies `catalog.disharifoundation.org` → `localhost:5050`. See deployment checklist in memory.

### Design system

Inherits CSS tokens from `base.html`: `--crimson`, `--text`, `--text-muted`, `--border`, `--radius`. Status badges use gradient fills: NEW (green), DUPLICATE (amber), FUZZY (teal), ERROR (crimson). Popovers use the `info-box--alt` slate→blush gradient matching disharifoundation.org "Our Mission" section.

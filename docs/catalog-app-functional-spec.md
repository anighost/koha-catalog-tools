# Dishari Catalog App — Functional Specification

> **Codebase**: `catalog-app/` under `koha-catalog-tools`  
> **Stack**: Python 3.11 · Flask · SQLite · Bootstrap 5 · Gunicorn (single worker)  
> **Purpose**: Browser UI that bridges Gronthee (AI book scanner) → `clean_catalog.py` → Koha ILS

---

## 1. Purpose & Problem Statement

Dishari Library uses **Gronthee** (a Vercel app) to scan physical book covers with a phone camera. Gronthee uses OCR/AI to read Bengali text and outputs a structured spreadsheet (CSV/XLSX) with 40 columns per book.

That spreadsheet is then processed by `clean_catalog.py` to produce a MARC21 binary file for import into **Koha** (the library's ILS). The gap this app fills:

- Multiple volunteers run Gronthee independently, creating **duplicate records** and **barcode collisions**
- Gronthee's AI misreads Bengali text — titles, authors, publishers often have **OCR errors** that need human correction before import
- There was no UI to review, correct, or deduplicate before import

The catalog app provides: **upload → OCR-error review → duplicate detection → one-click Koha import**, with a persistent dedup registry that grows across all runs.

---

## 2. User Flow (Happy Path)

```
[Volunteer] opens browser
     │
     ▼
  /login  ──── password check ────► session['auth'] = True
     │
     ▼
  /  (upload page)
     │  drops .xlsx / .csv from Gronthee
     ▼
  POST /upload
     │  parses file → pre-normalizes → dedup check on each row
     │  saves to sessions/<sid>.json
     ▼
  /review/<sid>
     │  volunteer reviews table:
     │    - corrects OCR errors (title, author, ISBN, year, publisher)
     │    - resolves FUZZY matches (skip / add copy / import as new)
     │    - DUPLICATE rows: choose skip or add copy 2/3/4
     ▼
  POST /process/<sid>
     │  splits rows: new_books list + copy_rows list
     │  calls catalog_engine.run() → clean_catalog.py subprocess
     │  merges MARC bytes (new books + copy records)
     │  calls bulkmarcimport.pl on Koha server
     │  registers new books in dedup_registry.db
     ▼
  /result/<sid>
     │  shows: N new books · M copies added · K skipped · W errors
     │  download buttons: MARC file · audit XLSX · error rows XLSX
     ▼
  [done — books searchable in Koha OPAC]
```

---

## 3. Screen Reference

### 3.1 Login (`GET/POST /login`)

- Single shared password for all volunteers (set via `CATALOG_PASSWORD` env var, default `changeme`)
- Rate-limited: 5 failures per IP → 15-minute lockout; state persisted in `login_attempts` SQLite table — survives server restarts
- On success: `session['auth'] = True`, `session.permanent = True` (4-hour cookie lifetime)
- CSRF token validated on POST
- Redirects to `?next=` URL if the user was bounced here mid-session

### 3.2 Upload (`GET /` · `POST /upload`)

- Drag-and-drop file zone; accepts `.xlsx`, `.xls`, `.csv`; client-side validates extension
- **Server-side limits**: 10 MB max file size (`MAX_CONTENT_LENGTH`; Flask rejects at the WSGI layer before any disk/memory allocation — returns a clean flash message via 413 handler); 500-row maximum per file (rows beyond this are rejected with a "split into batches" message)
- On POST:
  1. Saves file to `uploads/<sid><ext>`
  2. Calls `parse_upload()` → list of 40-column rows (header row skipped, blank rows dropped)
  3. Enforces row count limit; deletes file and flashes error if exceeded
  4. Calls `build_review_rows()` → pre-normalizes + dedup check on every row
  5. Saves full state to `sessions/<sid>.json`
  6. Redirects to `/review/<sid>`
- Opportunistically runs `_cleanup_old_files()` (at most once per 24h)

### 3.3 Review (`GET /review/<sid>`)

The main UX screen. A server-rendered editable table — one row per book.

**Columns shown:**
| Column | Editable | Notes |
|--------|----------|-------|
| Status badge | No (JS-updated) | NEW · DUPLICATE · FUZZY · ERROR |
| Title | Yes | Triggers live dedup re-check on input |
| Author | Yes | Triggers live dedup re-check on input |
| ISBN | Yes | Triggers live dedup re-check on input |
| Year | Yes | Was read-only; now editable |
| Publisher | Yes | Was read-only; now editable |
| Action | Yes (select) | Visible only for DUPLICATE/FUZZY rows |

**Status badge meanings:**

| Badge | Color | Meaning |
|-------|-------|---------|
| `NEW` | Green | Not in registry; will be imported as a new bib record |
| `DUPLICATE` | Amber | Exact match in registry (registry dup) **or** same book appeared in an earlier row of this file (same-file dup) |
| `FUZZY` | Teal | Similar but not identical to an existing record; confidence score shown |
| `ERROR` | Red | Missing required field (title or author); blocks processing |

**DUPLICATE row behavior — registry match (`dup_source='registry'`):**
- Action dropdown pre-selects the next logical copy (Copy 2 if 1 exists, Copy 3 if 2 exist, etc.)
- If already at 4 copies, pre-selects Skip
- Clicking the badge opens a popover showing full existing record details (title, author, publisher, year, ISBN, all copy barcodes)
- Dup-info panel shows existing title + barcode

**DUPLICATE row behavior — same-file match (`dup_source='upload'`):**
- Triggered when the same book (by ISBN or normalized title+author+edition) appears in a previous row of the same upload (G3)
- Dup-info panel shows "↑ row N in this file" (no barcode — the first occurrence has not been processed yet)
- Action dropdown shows only "Skip (duplicate in this file)" — Copy 2/3/4 options are hidden since no primary barcode exists to derive a copy barcode from
- No popover (nothing in the registry to look up yet)

**FUZZY row behavior:**
- Confidence score shown below badge (e.g. `~87% match`)
- Action dropdown **defaults to `— Select action —`** — the Process button is blocked until every FUZZY row has an explicit selection
- Options: Skip · Add as Copy 2/3/4 · Import as new book
- Selecting "Import as new book" changes badge to NEW (green) and routes the row through the new-book pipeline
- Switching away from "Import as new book" restores FUZZY badge

**Live dedup re-check:**
- Fires 450ms after last keystroke on title, author, or ISBN fields
- AJAX `GET /api/dedup?title=&author=&isbn=` returns `{status, dup_barcode, dup_title, next_action, fuzzy_score}`
- `updateRow()` JS function updates badge class, dup-info panel, action dropdown, and fuzzy score line
- When transitioning to FUZZY, the `— Select action —` and `Import as new book` options are revealed (they are always in the DOM, just `hidden` for non-FUZZY rows)

**Table filter:**
- Search input above the table filters visible rows by title, author, or ISBN in real-time
- Shows "N of M rows" count when a query is active

**Exclude row:**
- Every row has a subtle "✕ exclude row" link below the status badge
- Clicking it: grays the row (opacity 0.3), disables all inputs, sets `deleted_{i}='1'` hidden input, shows "↺ restore" link
- Excluded rows are skipped by `updateSummary()` (don't block Process) and by `process()` server-side (don't count in any result stat)

**Bottom bar:**
- Summary counts: `3 new · 1 duplicate · 2 possible matches · 1 error`
- "Process & Import to Koha" button — disabled if any ERROR rows remain OR any FUZZY rows have unresolved `— Select action —`
- Clicking the button opens a **confirmation modal** showing: N new books · M copy additions · K duplicates to skip · P excluded rows. User must click "Confirm & Import" to actually submit. If nothing would be imported (all skipped/excluded), a warning is shown in the modal.

**bfcache / back-button safety:**
- A `pageshow` listener hides the processing overlay if the browser restores the review page from the bfcache (back-button navigation). Without this, the spinner would be frozen on screen.

**Session expiry protection:**
- JS pings `GET /heartbeat` every 5 minutes to keep the auth cookie alive
- A dismissible warning banner appears 20 minutes before the 4-hour session expires

### 3.4 Processing (`POST /process/<sid>`)

Synchronous — takes 5–30 seconds. A spinner overlay covers the page during submission.

Steps (detailed in §6 below).

### 3.5 Result (`GET /result/<sid>`)

Shows a summary card with counters:

| Counter | Color | Meaning |
|---------|-------|---------|
| New books | Green | Successfully processed new bib records |
| Copies added | Teal | Physical copies added to existing bibs |
| Skipped | Grey | DUPLICATE/FUZZY rows the user chose to skip |
| Errors | Red | Rows that failed processing |
| Already registered | Amber | Books the engine processed but were already in the dedup registry |

- Koha import status: green "Imported successfully" or amber "Not completed — import manually"
- Full scrollable import log (no truncation)
- Download buttons: MARC file (`.mrc`) · Audit spreadsheet · Error rows spreadsheet
- "Process another file" link back to upload

---

## 4. Data Model

### 4.1 SQLite — `dedup_registry.db`

Two tables. `books` is the dedup registry; `login_attempts` persists rate-limit state.

```sql
CREATE TABLE books (
    id             INTEGER PRIMARY KEY,
    isbn           TEXT,                      -- cleaned (digits + X only); NULL if absent
    title_norm     TEXT NOT NULL,             -- normalize(title): lowercase, no punctuation
    author_norm    TEXT NOT NULL,             -- normalize_author(): words sorted alphabetically
    edition_norm   TEXT NOT NULL DEFAULT '',  -- normalize(edition): '' if unknown (G1)
    title_display  TEXT,                      -- raw canonical title (for Bengali volume detection)
    author_display TEXT,                      -- raw canonical author (for popover display)
    publisher      TEXT,                      -- stored for display only, not used in dedup key
    year           TEXT,                      -- stored for display only, not used in dedup key
    barcode        TEXT NOT NULL,             -- primary barcode (10XXXX format)
    copies         INTEGER DEFAULT 1,         -- total physical copies including primary
    added_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    source_file    TEXT                       -- original Gronthee filename
);

-- G1: unique key is title + author + edition (edition='' for books with no edition info)
CREATE UNIQUE INDEX idx_dedup   ON books(title_norm, author_norm, edition_norm);
CREATE UNIQUE INDEX idx_barcode ON books(barcode);

-- Login rate-limit state (persisted across restarts)
CREATE TABLE login_attempts (
    ip    TEXT PRIMARY KEY,
    count INTEGER DEFAULT 1,
    since REAL NOT NULL        -- Unix timestamp of first failure in current window
);
```

**Key constraints:**
- `idx_dedup` prevents the same logical book/edition from being registered twice. The 3-column key `(title_norm, author_norm, edition_norm)` means the same title in two different editions (e.g. "6th Edition" vs "7th Edition") are distinct registry entries
- `edition_norm = ''` is the default for books with no edition info — they still deduplicate against each other on title+author alone (backward-compatible with pre-G1 data)
- `idx_barcode` prevents duplicate barcodes (defense against race conditions)
- `INSERT OR IGNORE` is used on insert; if skipped, a barcode-mismatch check warns if the stored barcode differs from the new one
- `login_attempts` rows expire naturally: any row whose `since` is older than 15 minutes is treated as cleared and deleted on next check

### 4.2 File-based sessions — `sessions/<sid>.json`

Each upload session is identified by an 8-character UUID fragment (`sid`). The JSON file contains:

```json
{
  "filename": "gronthee_export_2026-04-15.xlsx",
  "upload_path": "uploads/a3f7c2b1.xlsx",
  "uploaded_at": "2026-04-15T14:32:00",
  "rows": [ ... array of row dicts ... ],
  "result": null   // populated after /process
}
```

Each row dict:
```json
{
  "idx": 0,
  "status": "NEW",          // NEW | DUPLICATE | FUZZY | ERROR
  "action": "skip",
  "dup_barcode": null,       // primary barcode of registry match; null for same-file dups
  "dup_title": null,         // display title of matched record
  "dup_source": null,        // "registry" | "upload" | null — distinguishes match source
  "dup_row_num": null,       // 1-indexed row number of first occurrence (same-file dups only)
  "fuzzy_score": null,
  "isbn": "9788170662",
  "title": "Galpa Samagra",
  "author": "Devi, Ashapurna",
  "year": "1993",
  "publisher": "Bikash Grantha Bhavan",
  "cols": [ ... 40-element list of all column values ... ]
}
```

---

## 5. Pre-normalization Pipeline (`build_review_rows`)

Before any dedup check, each uploaded row is run through the same synonym/normalization pipeline as `clean_catalog.py`. This ensures the review screen shows **canonical values** — what Koha will actually receive — not raw OCR output.

```
Raw XLSX row (40 columns)
        │
        ├── clean_isbn()
        │       strip .0 float artifact; strip ISBN prefix/hyphens; keep digits+X
        │       ISBN-10 → ISBN-13: prepend 978, recalculate check digit
        │
        ├── clean_year()
        │       strip all non-digits, take first 4 digits
        │       validate range 1800 ≤ year ≤ current year → '' if invalid
        │       (mirrors clean_catalog.py exactly; "1993 (reprint)" → "1993")
        │
        ├── prenormalize_author()
        │       Stage 1: synonyms_author exact all-words match (case-insensitive)
        │       Stage 2: rapidfuzz token_sort_ratio fallback at fuzzy_author_threshold
        │                (default 88) — catches single-character typos Stage 1 misses
        │       → name inversion: "Firstname Lastname" → "Lastname, Firstname"
        │       → multi-author "A and B": returned unchanged (clean_catalog.py splits it)
        │
        ├── prenormalize_title()
        │       synonyms_keywords regex substitution (word-boundary)
        │
        ├── prenormalize_publisher()
        │       synonyms_publisher word-boundary match
        │
        ├── prenormalize_place()
        │       synonyms_place exact match
        │
        ├── prenormalize_series()
        │       synonyms_keywords + series_overrides["SeriesTitle|AuthorPreInvert"]
        │
        ├── prenormalize_subject() × 5 (cols 14–18)
        │       synonyms_subject word-boundary match
        │
        └── prenormalize_author() × 6 (added authors, cols 19–24)

        → _first_author_for_dedup(raw_author_pre_invert, meta)
        │       Splits "A and B" → takes first author only → prenormalize_author()
        │       Mirrors clean_catalog.py: registry stores only the primary author (100$a)
        │       Full author string is preserved in cols[] for MARC generation
        │
        → lookup_dup(isbn, title, author_for_dedup, edition)  → registry match or None
        │       edition = cols[COL_EDITION] (col 6) — passed as-is; normalize() applied inside lookup_dup
        │
        → within-upload dedup check (G3) — only when lookup_dup returns None
        │       seen_in_upload dict is maintained across all rows in the call
        │       Keys checked in order: ('isbn', isbn) → ('tae', (title_norm, author_norm, edition_norm))
        │       First occurrence registers the key (setdefault); later occurrences match it
        │       Match → status='DUPLICATE', dup_source='upload', dup_row_num=<1-indexed>
        │       No match → status='NEW'
        │       All rows register their keys (even registry-DUPLICATE rows), so the first
        │       physical occurrence in the file is always the anchor for subsequent rows
```

All synonym dictionaries are loaded from `koha_session_meta.json` at the start of each `build_review_rows()` call.

**Live AJAX dedup re-check (`GET /api/dedup`)** applies the same normalization pipeline — `clean_isbn()`, `prenormalize_title()`, `_first_author_for_dedup()` — before calling `lookup_dup()`. Edition is read from the hidden `col_<i>_6` input in the review table and passed as the `edition` query parameter. This ensures live edits by the user produce consistent dedup results with the initial load.

---

## 6. Dedup Algorithm (`lookup_dup`)

Three-stage lookup, stopping at the first match. Signature:
```python
lookup_dup(isbn, title, author, edition='') → dict | None
```

### Stage 1 — ISBN Exact Match
```python
SELECT * FROM books WHERE isbn = clean_isbn(isbn)
```
Most reliable. `clean_isbn()` mirrors `clean_catalog.py`'s `clean_and_convert_isbn()`:
- Strips `.0` Excel float artifacts (`8170669677.0` → `8170669677`)
- Strips `ISBN` prefix text and non-digit/X characters (handles hyphens, spaces)
- **Converts ISBN-10 → ISBN-13**: prepends `978`, recalculates check digit (`8170669677` → `9788170669677`)
- ISBN-13: returned as-is
- Anything else (Na, N/A, blank, unknown length): returns `''` (skips ISBN lookup)

The registry always stores ISBN-13 (written by `clean_catalog.py` before the audit XLSX is generated). Using the same conversion in `clean_isbn()` ensures a Gronthee export with ISBN-10 matches the registry entry for the same book. Returns `fuzzy=False`.

### Stage 2 — Normalized Title + Author + Edition Exact Match (G1)
```python
nt = normalize(title)         # lowercase, strip punctuation, collapse spaces
na = normalize_author(author)  # normalize then sort words alphabetically
ne = normalize(edition)        # lowercase, strip punctuation; '' if no edition
SELECT * FROM books WHERE title_norm=nt AND author_norm=na AND edition_norm=ne
```
Word-order-independent author normalization handles name inversion:
- `"Ashapurna Devi"` → `"ashapurna devi"`
- `"Devi, Ashapurna"` → `"ashapurna devi"` (comma stripped, then sorted)

**Edition matching behaviour:**
- `"6th Edition"` and `"7th Edition"` normalize to different strings → no match → treated as different bibs ✓
- `"1st Edition"` and `"1st edition"` normalize to the same string → match ✓
- Both editions empty (`''`) → match on title+author alone (same as pre-G1 behaviour) ✓
- One edition known, other empty → no Stage 2 match (empty `''` ≠ `"1st edition"`) → falls through to Stage 3 fuzzy

Returns `fuzzy=False`.

### Stage 3 — Fuzzy Match (rapidfuzz)
Only reached if stages 1 and 2 find no match.

**Pre-filter (P1 optimization):** Before running rapidfuzz, the longest title word (> 3 chars) is used as a `LIKE` anchor to reduce candidates from the full registry to a small subset. For an 80%+ similar title, the longest word almost certainly appears in both. A 5,000-book registry is reduced to < 20 candidates in typical cases.

```python
# Pre-filter: longest word in title_norm (most unique token)
sig_words = sorted([w for w in nt.split() if len(w) > 3], key=len, reverse=True)
if sig_words:
    candidates = SELECT ... FROM books WHERE title_norm LIKE '%<sig_words[0]>%'
else:
    candidates = SELECT ... FROM books   # fallback: full scan for short titles

for each candidate in candidates:
    # Hard block 1: different volumes are different books
    if _volumes_conflict(title, candidate.title_display):
        continue

    # Hard block 2 (G1): if both editions are known and differ → different edition,
    # not a duplicate. Skip candidate entirely.
    if ne and candidate.edition_norm and ne != candidate.edition_norm:
        continue

    t_score = token_sort_ratio(nt, candidate.title_norm)
    if t_score < 80: continue

    a_score = token_sort_ratio(na, candidate.author_norm)
    if a_score < 72: continue

    combined = t_score * 0.65 + a_score * 0.35
    # keep best combined score
```

Returns the best-scoring candidate with `fuzzy=True` and `fuzzy_score=round(combined)`.

**Volume conflict detection** (`_volumes_conflict`):
- English: matches `vol`, `volume`, `part`, `pt`, `khand`, `no`, `episode`, `chapter` + number/roman numeral on **normalized** title
- Bengali: matches `খণ্ড`, `ভাগ`, `পর্ব`, `সংখ্যা` + digit on **raw** title (combining marks like virama `্` are stripped by `normalize()`, so Bengali must bypass it)
- If both titles have volume markers and they differ → conflict → skip candidate

**Edition conflict detection (G1):**
- Only fires when **both** the upload row and the registry candidate have a non-empty edition
- If either is unknown (`''`), the edition check is skipped — fuzzy title+author alone decides
- This prevents "Sanchaita 6th Edition" from fuzzy-matching "Sanchaita 7th Edition", while still catching OCR variants like "Sanchaita" (no edition) against "Sanchaita, 6th Edition" (known edition) as a FUZZY match for human review

---

## 7. Processing Pipeline (`POST /process/<sid>`)

```
Form submitted (row_count, per-row: status, action, title, author, isbn, year, publisher, dup_barcode, col_*)
        │
        ├── _validate_csrf()
        ├── load_session(sid)
        │
        ├── For each row i in 0..row_count:
        │       status = status_i (NEW | DUPLICATE | FUZZY | FUZZY_NEW | ERROR)
        │       action = action_i (skip | copy2 | copy3 | copy4 | new | review)
        │       deleted_i = '1' if row was excluded by user
        │       Apply user edits: cols[ISBN/AUTHOR/TITLE/YEAR/PUBLISHER] = form values
        │
        │       deleted='1' → skip (excluded rows counted nowhere in result)
        │       ERROR  → skip
        │       NEW or FUZZY_NEW → new_rows[]
        │       DUPLICATE/FUZZY + action=copy2/3/4 → copy_rows[]
        │       DUPLICATE/FUZZY + action=skip/review → skip
        │
        ├── Process new_rows (if any):
        │       _write_xlsx(new_rows) → temp XLSX
        │       catalog_engine.run(temp_xlsx) → subprocess clean_catalog.py
        │           [FileLock on koha_session_meta.json — serializes barcode counter]
        │       Read .mrc bytes
        │       Copy audit + error XLSX to output/
        │       extract_processed_books(audit) → register_books() → dedup_registry.db
        │           [returns (skipped_count, barcode_mismatch_warnings)]
        │
        ├── Process copy_rows (if any):
        │       For each copy row:
        │           FileLock + SQLite BEGIN EXCLUSIVE:
        │               Re-read current copies count from DB (prevents race)
        │               Derive copy barcode: prefix + primary_barcode[2:]
        │               generate_copy_marc(fields, copy_bc, copy_num) → MARC bytes
        │               UPDATE books SET copies=copies+1
        │           Append MARC bytes to mrc_bytes
        │
        ├── Write merged mrc_bytes → output/<sid>.mrc
        │
        ├── import_to_koha(mrc_path):
        │       sudo koha-shell <instance> -c
        │           "bulkmarcimport.pl -b -file <path> -match <rule> -insert -update -items"
        │       Returns (success, log_output)
        │
        └── Save result → session, redirect to /result/<sid>
```

### FUZZY_NEW sentinel
When a user selects "Import as new book" on a FUZZY row, the JS sets `status_hidden` to `FUZZY_NEW`. The server treats `FUZZY_NEW` identically to `NEW` — the row enters `new_rows` and goes through the full `clean_catalog.py` pipeline.

---

## 8. Barcode Scheme

Barcodes are 6 digits with a 2-digit prefix encoding copy number:

| Prefix | Meaning | Example |
|--------|---------|---------|
| `10` | Primary copy (Copy 1) | `100045` |
| `11` | Copy 2 | `110045` |
| `12` | Copy 3 | `120045` |
| `13` | Copy 4 | `130045` |

The primary barcode is assigned by `clean_catalog.py` (increments `last_primary_barcode` in `koha_session_meta.json`). Copy barcodes are derived by replacing the first two digits of the primary barcode with the copy prefix.

**Concurrency safety:** Copy barcode assignment uses `FileLock(LOCK_FILE)` + `SQLite BEGIN EXCLUSIVE` transaction. This re-reads the current `copies` count inside the lock, preventing two simultaneous `/process` requests from assigning the same copy number.

---

## 9. Copy MARC Record (`generate_copy_marc`)

When adding a physical copy to an existing Koha bib, the app generates a minimal MARC record containing enough fields for `bulkmarcimport.pl` to find the existing bib and attach the new item — rather than creating a duplicate bib.

Fields included:
| Tag | Content | Purpose |
|-----|---------|---------|
| 020 | ISBN | Fallback match key |
| 100 | Author | STRICT_CLE match |
| 245 | Title | STRICT_CLE match |
| 250 | Edition | STRICT_CLE match |
| 260 | Place / Publisher / Year | STRICT_CLE match |
| 942 | Item type code | Koha bib-level |
| 952 | Item/holdings | `$a` home branch · `$b` hold branch · `$p` barcode · `$o` call no · `$d` date · `$y` item type · `$t` copy number · `$8` collection code · `$g` cost |

---

## 10. Koha Import Mechanism

```python
cmd = [
    'sudo', 'koha-shell', KOHA_INSTANCE, '-c',
    f'bulkmarcimport.pl -b -file {shlex.quote(mrc_path)}'
    f' -match {shlex.quote(KOHA_MATCH_RULE)} -insert -update -items'
]
subprocess.run(cmd, timeout=180)
```

- Requires a sudoers rule: `dishari ALL=(root) NOPASSWD: /usr/sbin/koha-shell dishari_lib -c *`
- If `koha-shell` is not on PATH (e.g. local dev), returns a graceful "not on Koha server" message — the user can download the MARC file and import manually via Koha Staff UI
- Timeout: 3 minutes; returns full stdout+stderr as the import log on the result page

---

## 11. `catalog_engine.py` — Subprocess Wrapper

`clean_catalog.py` uses module-level constants (`INPUT_FILE`, `STATE_FILE`, `OUTPUT_DIR`). The engine patches them via regex before running:

```python
source = re.sub(r'^INPUT_FILE\s*=\s*.+$', f'INPUT_FILE = {repr(input_path)}', source, MULTILINE)
source = re.sub(r'^STATE_FILE\s*=\s*.+$', f'STATE_FILE = {repr(META_FILE)}',  source, MULTILINE)
source = re.sub(r'^OUTPUT_DIR\s*=\s*.+$', f'OUTPUT_DIR = {repr(out_dir)}',    source, MULTILINE)
```

The patched script is written to `<tempdir>/_run.py` and executed as a subprocess. This avoids modifying the original script and allows parallel runs (each gets its own temp dir).

**FileLock** on `koha_session_meta.json.lock` serializes access to the barcode counter. Two concurrent uploads can both call `catalog_engine.run()` but only one holds the lock at a time.

---

## 12. Security Model

| Control | Implementation |
|---------|---------------|
| Authentication | Single shared password, checked on every route via `@login_required` decorator |
| Session signing | Flask signed cookie (`FLASK_SECRET_KEY`); random 32-byte key generated at startup if not set (warns to log) |
| CSRF | Per-session `secrets.token_hex(16)` token; validated on all POST routes; available in templates as `csrf_token()` |
| Login rate limiting | 5 failures per IP → 15-min lockout; persisted in `login_attempts` SQLite table — survives server restarts |
| Session lifetime | 4-hour permanent cookie; `/heartbeat` endpoint extends it from the review page |
| Shell injection | `shlex.quote()` applied to all shell arguments; `koha-shell` invoked via list (no `shell=True`) |
| File size limit | `MAX_CONTENT_LENGTH = 10 MB` enforced at WSGI layer; clean 413 handler returns flash message |
| File type validation | Extension check + openpyxl/csv parsing (any parse failure → flash error, no crash) |
| Row count limit | 500 rows max per upload; rejected server-side before `build_review_rows()` runs |

---

## 13. File Lifecycle & Cleanup

All uploaded and generated files are temporary. `_cleanup_old_files()` runs at most once per 24h (triggered opportunistically on `/upload`):

| Directory | Retention | Contents |
|-----------|-----------|---------|
| `uploads/` | 7 days | Original Gronthee XLSX/CSV files |
| `output/` | 7 days | Generated `.mrc`, audit XLSX, error XLSX |
| `sessions/` | 7 days | `<sid>.json` session state files |
| `tempfile` `catalog_run_*` | 1 day | `clean_catalog.py` subprocess temp dirs |

---

## 14. API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/` | Yes | Upload page |
| POST | `/upload` | Yes | Parse file, build review rows, redirect |
| GET | `/review/<sid>` | Yes | Review/edit table |
| POST | `/process/<sid>` | Yes | Run pipeline, import to Koha |
| GET | `/result/<sid>` | Yes | Show import summary |
| GET | `/download/<sid>/<filetype>` | Yes | Download mrc / audit / errors |
| GET | `/api/dedup` | Yes | AJAX dedup check (title, author, isbn params) |
| GET | `/api/book` | Yes | AJAX book detail by barcode (for popover) |
| GET | `/heartbeat` | Yes | Keep session alive (called by review page JS) |
| GET | `/health` | No | Uptime probe; returns `{"status":"ok","db":true}` or 503 |
| GET/POST | `/login` | No | Login form |
| GET | `/logout` | No | Clear session, redirect to login |

---

## 15. Dependencies

```
flask          Web framework
gunicorn       WSGI server (single worker — required for barcode lock safety)
openpyxl       Parse/write XLSX files
pymarc         Build MARC21 binary records
filelock       Cross-process file locking for barcode counter
rapidfuzz      Fuzzy string matching for dedup stage 3
```

`clean_catalog.py` auto-installs `pymarc` and `openpyxl` on first run via `subprocess.check_call([sys.executable, '-m', 'pip', 'install', ...])`.

---

## 16. Known Constraints & Design Decisions

| Decision | Rationale |
|----------|-----------|
| Single Gunicorn worker | `koha_session_meta.json` barcode counter is file-based; single worker avoids cross-process state corruption. FileLock + SQLite EXCLUSIVE tx add a second safety layer. |
| `clean_catalog.py` not modified | Subprocess + constant-patching approach lets the script run standalone OR via the web app without a fork. |
| File-based sessions, not DB | Session state includes full 40-column row data for potentially hundreds of rows; storing in a JSON file avoids SQLite blob complexity and makes debugging easy. |
| No async/SSE for processing | App is internal, low-concurrency. A spinner overlay is sufficient UX. Adding SSE/WebSockets would require architectural changes not warranted by usage volume. |
| Max 4 copies per book | Barcode scheme supports prefixes 10–13 only. Beyond 4 physical copies, the workflow requires manual Koha intervention. |
| Fuzzy match forced to `— Select action —` | FUZZY rows represent uncertain matches (e.g. OCR variants of the same title). Pre-selecting Skip was risky — a real duplicate could slip through as a new book. Forcing an explicit choice makes the review meaningful. |
| `clean_year()` strips all non-digits and validates range | Mirrors `clean_catalog.py` exactly — "1993 (reprint)" → "1993", "9999" → "". Without this, the review screen could display a year that clean_catalog.py would silently blank in the MARC output. |
| `prenormalize_author()` fuzzy stage 2 | `clean_catalog.py` uses rapidfuzz for author synonym matching (threshold 88). Without the same fallback in `app.py`, single-character OCR typos in author names would escape synonym normalization, causing false NEW status for known books. |
| `_first_author_for_dedup()` splits multi-author field | The registry stores only the primary author (100$a from `clean_catalog.py`). Comparing "A and B" against "A" would never match at Stage 2. The helper extracts only the first author for dedup purposes while preserving the full string in `cols[]` for MARC generation. |
| Login failures in SQLite not memory | An in-memory dict resets on every server restart, allowing burst brute-force across restarts. Persisting to SQLite means the lockout window is respected even after a crash or deploy. |
| Same-file duplicates cannot be added as copies | Copy barcode derivation requires the primary barcode from the registry, which doesn't exist until the first occurrence is processed. The action dropdown intentionally hides Copy 2/3/4 for same-file dups (`dup_source='upload'`). If a volunteer legitimately has two physical copies, they should use Gronthee's copy columns (cols 37–39) on the original row rather than scanning the book twice. |
| 500-row upload cap | Beyond ~500 rows the review table becomes difficult to navigate. Gronthee batches should naturally be smaller (single scanning session). Larger files should be split upstream. |
| LIKE pre-filter before rapidfuzz (Stage 3) | Full table scan × every uploaded row is O(N×M). The longest significant word (> 3 chars) is a highly selective filter; for 80%+ similar titles it almost always appears in both. Reduces 5,000-candidate scans to < 20 in typical cases. |
| Confirm modal before process | One-click import with no summary is risky at scale — a misclick could import hundreds of books with wrong data. The modal gives volunteers a final checkpoint showing exact counts. |

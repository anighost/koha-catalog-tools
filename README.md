# koha-catalog-tools

A Python script that cleans and converts a raw library catalog spreadsheet into MARC21 format for import into [Koha ILS](https://koha-community.org/). Designed for Bengali-language library collections with messy, inconsistently entered data.

## What it does

Takes a CSV (or XLSX) of catalog records and produces:

- **`output/cleaned_<name>_<timestamp>.mrc`** — MARC21 binary file, ready for Koha batch import
- **`output/cleaned_<name>_<timestamp>.xlsx`** — Audit spreadsheet showing every transformation applied per row
- **`output/error_<name>_<timestamp>.xlsx`** — Rejected rows (missing mandatory title or author)

All output files are written to an `output/` subdirectory (created automatically). Each run produces timestamped filenames so previous outputs are never overwritten.

---

## Scripts

| Script | Description |
|--------|-------------|
| `clean_catalog.py` | Main script — no external API needed |
| `clean_catalog_llm.py` | Extended version — uses Claude API to generate additional phonetic variant titles (MARC 246) and subject headings (MARC 650) for better search coverage |

---

## Requirements

Python 3.x with `pandas` installed. All other dependencies (`pymarc`, `openpyxl`, `rapidfuzz`) are auto-installed on first run.

```bash
pip install pandas
```

---

## Setup

### 1. Set the input file path

Open the script and update `INPUT_FILE` near the top to the full path of your CSV:

```python
INPUT_FILE = '/path/to/your/catalog_to_be_cleaned_v1.csv'
```

The script accepts `.csv` or `.xlsx`. UTF-8 and Latin-1 encodings are both handled automatically (a warning is printed if Latin-1 fallback is used).

### 2. Ensure `koha_session_meta.json` is present

This file must be in the **same directory as the script**. It holds the barcode counter and all normalization dictionaries. Do not delete it between runs — the barcode counter persists across sessions.

If starting fresh, reset `last_primary_barcode` to `100000` in the JSON before running.

---

## Running

```bash
cd /path/to/koha-catalog-tools
python clean_catalog.py
```

**Console output:**
```
  Processing row 150/2124...
Writing outputs...
  42 rejected rows → output/error_catalog_v1_20260407_1430.xlsx

FINISHED!
  Records processed : 2081
  MARC file         : output/cleaned_catalog_v1_20260407_1430.mrc
  Audit Excel       : output/cleaned_catalog_v1_20260407_1430.xlsx
  Last barcode used : 102081
  Error file        : output/error_catalog_v1_20260407_1430.xlsx
```

### Using the LLM version

Set your Anthropic API key, then run the LLM script instead:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python clean_catalog_llm.py
```

On first run it enriches all unique titles via Claude API (batches of 20), caches results in `koha_session_meta.json` under `llm_cache`, and adds extra MARC 246 and 650 fields. Subsequent runs skip already-cached titles — no repeat API calls. Without the API key set, the script behaves identically to `clean_catalog.py`.

---

## Input Format

The script expects columns in a fixed position order (0-indexed). The expected header names are standard Koha MARC field codes:

| Col | Header | Field |
|-----|--------|-------|
| 0 | `020$a` | ISBN |
| 1 | `041$a` | Language (`BN`, `EN`, `HN`, or full name like `Bengali`) |
| 2 | `100$a` | Author |
| 3 | `245$a` | Title |
| 4 | `245$b` | Subtitle |
| 5 | `246$a` | Variant / alternate title |
| 6 | `250$a` | Edition |
| 7 | `260$a` | Place of publication |
| 8 | `260$b` | Publisher |
| 9 | `260$c` | Year |
| 10 | `300$a` | Pages |
| 12 | `490$a` | Series |
| 13 | `500$a` | General note |
| 14–18 | `650$a` | Subject headings (up to 5) |
| 19–24 | `700$a` | Added authors / contributors (up to 6) |
| 25 | `942$c` | Item type code (e.g. `BK`, `RB`) |
| 27 | `952$8` | Collection / shelving location code |
| 28 | `952$a` | Home branch code |
| 29 | `952$b` | Holding branch code |
| 31 | `952$d` | Acquisition date |
| 33 | `952$g` | Cost |
| 34 | `952$o` | Call number |
| 35 | `952$p` | Barcode (primary copy, must start with `10`) |
| 36 | `952$z` | Item note (also used as copy-2 trigger if no cols 37–39) |
| 37–39 | `953$8`–`955$8` | Copy 2 / 3 / 4 triggers |

**Copy trigger values** accepted in cols 36–39: a numeric barcode (e.g. `110045`), `Y`, `Yes`, `2nd Copy`, `3rd Copy`, or `4th Copy`. Dates and other text are ignored.

---

## Normalization Rules

| Field | What the script does |
|-------|---------------------|
| **Author (100$a)** | Synonym-matched against dictionary, then inverted to `Surname, Forename`. Multiple authors joined by `and`/`&` are split — first goes to `100$a`, rest to `700$a`. Trailing `et al.` is stripped before inversion. Fuzzy matching via `rapidfuzz` catches single-character typos. |
| **Added authors (700$a)** | Cols 19–24 and any co-authors split from the main author field — all synonym-matched, inverted, and deduplicated. |
| **ISBN** | Stripped of noise, converted ISBN-10 → ISBN-13. Invalid check digits are flagged in the audit log but still converted. |
| **Publisher (260$b)** | Matched against synonym dictionary using word-boundary regex → canonical name. |
| **Place (260$a)** | Matched against place synonym dictionary → corrects misspellings (e.g. `Kolkota` → `Kolkata`). |
| **Title keywords** | Bengali romanization variants normalized via keyword dictionary using word-boundary regex (e.g. `golpo` → `Galpa`). |
| **Phonetic variants (246$a)** | Auto-generated by reverse-mapping canonical keywords back to colloquial forms (e.g. `Galpa Samagra` → `Golpo Somogro`). Also written for any explicit comma-separated values in col 5. Deduplicated against existing 246 fields. |
| **Subject headings (650$a)** | Normalized via synonym dictionary (word-boundary match). Deduplicated across all 5 subject columns. Unrecognized language codes are flagged in the audit log. |
| **Dates** | Multiple formats parsed and normalized to `YYYY-MM-DD`; defaults to today if missing. |
| **Pages** | Normalized to `123 p.` format. |
| **Year** | Non-numeric or out-of-range values discarded. |
| **Call number** | Defaults to `891` (Bengali literature) if blank. |
| **Item type** | Forced to `ASB` (Author Signed Book) if `500$a` or `952$z` contains `"author signed"`. |
| **Language** | ISO 639-1 codes (`BN`, `EN`, `HN`) and full names (`Bengali`, `English`, `Hindi`) both accepted → mapped to MARC 3-letter codes. Defaults to `ben`; a warning is logged in the audit if an unrecognized value is found. |
| **Barcodes** | Generated sequentially from `10xxxxx` if missing. Copy barcodes derived by replacing the first two digits: copy 2 → `11xxxxx`, copy 3 → `12xxxxx`, copy 4 → `13xxxxx`. |

---

## Persistent State (`koha_session_meta.json`)

This file lives next to the script and is updated on each run. Synonym dictionaries are **never overwritten** by a run — only `last_primary_barcode` (and `llm_cache` in the LLM version) are updated.

| Key | Purpose |
|-----|---------|
| `last_primary_barcode` | Highest `10xxxxx` barcode used; auto-increments for rows with no barcode |
| `previous_last_primary_barcode` | Barcode value from before the last run — lets you revert accidental runs by copying this back to `last_primary_barcode` |
| `synonyms_publisher` | Variant publisher names → canonical form (word-boundary regex match) |
| `synonyms_author` | Variant/misspelled author names → canonical form. Use pre-inverted canonical (with comma) for compound surnames to prevent wrong inversion |
| `synonyms_keywords` | Bengali romanization variants → standard spelling (regex word-boundary match applied to titles, subtitles, and series) |
| `synonyms_place` | Place misspellings → canonical city name |
| `synonyms_subject` | Subject heading variants → canonical heading (word-boundary regex match) |
| `series_overrides` | `"SeriesTitle\|AuthorName"` → canonical series name |
| `fuzzy_author_threshold` | Minimum RapidFuzz score (0–100) for fuzzy author matching; default `88` |
| `llm_cache` | *(LLM version only)* Cached Claude API results per title — phonetic variants and suggested subjects |

### Adding a compound surname

Use the already-inverted form as the key so inversion is skipped:

```json
"synonyms_author": {
    "Roy Chowdhury, Upendra Kishore": [
        "upendra kishor roy chowdhury",
        "upendrakishore roychoudhury"
    ]
}
```

### Adding a series override

```json
"series_overrides": {
    "Sonar Kella|Satyajit Ray": "Feluda Series"
}
```

### Reverting an accidental run

If the script was run by mistake and you want to restore the previous barcode counter:

1. Open `koha_session_meta.json`
2. Copy the value of `previous_last_primary_barcode` into `last_primary_barcode`
3. Save the file

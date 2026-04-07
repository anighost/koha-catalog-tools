# koha-catalog-tools

A Python script that cleans and converts a raw library catalog spreadsheet into MARC21 format for import into [Koha ILS](https://koha-community.org/). Designed for Bengali-language library collections with messy, inconsistently entered data.

## What it does

Takes a CSV (or XLSX) of catalog records and produces:

- **`cleaned_<name>_<timestamp>.mrc`** — MARC21 binary file, ready for Koha batch import
- **`cleaned_<name>_<timestamp>.xlsx`** — Audit spreadsheet showing every transformation applied per row
- **`error_<name>_<timestamp>.xlsx`** — Rejected rows (missing mandatory title or author)

---

## Requirements

Python 3.x with `pandas` installed. The script auto-installs `pymarc` and `openpyxl` on first run.

```bash
pip install pandas
```

---

## Setup

### 1. Set the input file path

Open `clean_catalog.py` and update `INPUT_FILE` near the top to the full path of your CSV:

```python
INPUT_FILE = '/path/to/your/catalog_to_be_cleaned_v1.csv'
```

The script accepts `.csv` or `.xlsx`. UTF-8 and Latin-1 encodings are both handled automatically.

### 2. Ensure `koha_session_meta.json` is present

This file must be in the **same directory as the script**. It holds the barcode counter and all normalization dictionaries. Do not delete it between runs — the barcode counter persists across sessions.

If starting fresh, reset `last_primary_barcode` to `100000` in the JSON before running.

---

## Running

```bash
cd /path/to/koha-catalog-tools
python clean_catalog.py
```

Output files are written to the **current working directory** (wherever you run the script from). Each run produces timestamped filenames so previous outputs are never overwritten.

**Console output:**
```
  Processing row 150/2124...
Writing outputs...
  42 rejected rows → error_catalog_v1_20260407_1430.xlsx

FINISHED!
  Records processed : 2081
  MARC file         : cleaned_catalog_v1_20260407_1430.mrc
  Audit Excel       : cleaned_catalog_v1_20260407_1430.xlsx
  Last barcode used : 102081
```

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
| **Author (100$a)** | Synonym-matched against dictionary, then inverted to `Surname, Forename`. Multiple authors joined by `and`/`&` are split — first goes to `100$a`, rest to `700$a`. Trailing `et al.` is stripped before inversion. |
| **Added authors (700$a)** | Cols 19–24 and any co-authors split from the main author field — all synonym-matched and inverted. |
| **ISBN** | Stripped of noise, converted ISBN-10 → ISBN-13. Invalid check digits are flagged in the audit log but still converted. |
| **Publisher (260$b)** | Matched against synonym dictionary → canonical name. |
| **Place (260$a)** | Matched against place synonym dictionary → corrects misspellings (e.g. `Kolkota` → `Kolkata`). |
| **Title keywords** | Bengali romanization variants normalized via keyword dictionary (e.g. `golpo` → `Galpa`). |
| **Variant titles (246$a)** | Written as MARC 246 fields; comma-separated values each become a separate field. |
| **Dates** | Multiple formats parsed and normalized to `YYYY-MM-DD`; defaults to today if missing. |
| **Pages** | Normalized to `123 p.` format. |
| **Year** | Non-numeric or out-of-range values discarded. |
| **Call number** | Defaults to `891` (Bengali literature) if blank. |
| **Item type** | Forced to `ASB` (Author Signed Book) if `500$a` or `952$z` contains `"author signed"`. |
| **Language** | ISO 639-1 codes (`BN`, `EN`, `HN`) and full names (`Bengali`, `English`, `Hindi`) both accepted → mapped to MARC 3-letter codes. Defaults to `ben`. |
| **Barcodes** | Generated sequentially from `10xxxxx` if missing. Copy barcodes derived by replacing the first two digits: copy 2 → `11xxxxx`, copy 3 → `12xxxxx`, copy 4 → `13xxxxx`. |

---

## Persistent State (`koha_session_meta.json`)

This file lives next to the script and is the only file modified on each run (only `last_primary_barcode` is updated — all synonym dictionaries are never overwritten by a run).

| Key | Purpose |
|-----|---------|
| `last_primary_barcode` | Highest `10xxxxx` barcode used; auto-increments for rows with no barcode |
| `synonyms_publisher` | Variant publisher names → canonical form (substring match) |
| `synonyms_author` | Variant/misspelled author names → canonical form. Use pre-inverted canonical (with comma) for compound surnames to prevent wrong inversion |
| `synonyms_keywords` | Bengali romanization variants → standard spelling (regex word-boundary match on titles) |
| `synonyms_place` | Place misspellings → canonical city name |
| `series_overrides` | `"SeriesTitle\|AuthorName"` → canonical series name |

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

### Resetting barcodes for a fresh run

Set `last_primary_barcode` to `100000` in the JSON before running.

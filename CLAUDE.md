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

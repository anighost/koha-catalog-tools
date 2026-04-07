# koha-catalog-tools

A Python script that cleans and converts a raw library catalog spreadsheet into MARC21 format for import into [Koha ILS](https://koha-community.org/). Designed for Bengali-language library collections with messy, inconsistently entered data.

## What it does

Takes a CSV (or XLSX) of catalog records and produces:

- **`cleaned_<name>_<timestamp>.mrc`** — MARC21 binary file, ready for Koha batch import
- **`cleaned_<name>_<timestamp>.xlsx`** — Audit spreadsheet showing every transformation applied per row
- **`error_<name>_<timestamp>.xlsx`** — Rejected rows (missing mandatory title or author)

## Requirements

Python 3.x with `pandas` installed. The script auto-installs `pymarc` and `openpyxl` on first run.

```bash
pip install pandas
```

## Usage

1. Place your input CSV next to `clean_catalog.py`
2. Set `INPUT_FILE` at the top of the script to match your filename
3. Run:

```bash
python clean_catalog.py
```

## Input Format

The script expects a CSV/XLSX with columns in a fixed order (0-indexed). Key columns:

| Col | Field |
|-----|-------|
| 0 | ISBN |
| 1 | Language (e.g. "Bengali", "English") |
| 2 | Author |
| 3 | Title |
| 4 | Subtitle |
| 6 | Edition |
| 7 | Place of publication |
| 8 | Publisher |
| 9 | Year |
| 10 | Pages |
| 12 | Series |
| 14–18 | Subject headings (up to 5) |
| 25 | Item type code |
| 28 | Home branch code |
| 29 | Holding branch code |
| 31 | Acquisition date |
| 33 | Cost |
| 34 | Call number |
| 35 | Barcode |
| 36 | Notes |
| 37–39 | Copy 2 / 3 / 4 triggers |

## Normalization Rules

The script applies these transformations automatically:

- **Author names** — inverted to "Surname, Forename" format; matched against synonym dictionary before inversion
- **ISBNs** — stripped of noise, validated, and converted from ISBN-10 to ISBN-13
- **Publishers** — matched against synonym dictionary and replaced with canonical name
- **Title keywords** — Bengali romanization variants normalized (e.g. "golpo" → "Galpa")
- **Dates** — multiple formats parsed and normalized to `YYYY-MM-DD`; defaults to today if missing
- **Pages** — normalized to `"123 p."` format
- **Call numbers** — defaults to `891` (Bengali literature) if blank
- **Item type** — set to `ASB` (Author Signed Book) if notes contain "author signed"
- **Language** — mapped to MARC language codes; defaults to `ben` (Bengali)
- **Barcodes** — generated sequentially from `10xxxxx` if missing; copy barcodes use prefix `11`/`12`/`13`

## Persistent State (`koha_session_meta.json`)

The script stores normalization dictionaries and barcode state in `koha_session_meta.json`. This file persists across runs and can be edited directly to:

- Add publisher/author/keyword synonyms
- Add series overrides (keyed as `"SeriesTitle|AuthorName"`)
- Reset or adjust the barcode counter (`last_primary_barcode`)

Example entry:
```json
{
    "synonyms_author": {
        "Rabindranath Tagore": ["rabindranath tagore", "rabindranath thakur", "rabindra nath"]
    },
    "series_overrides": {
        "Sonar Kella|Satyajit Ray": "Feluda Series"
    },
    "last_primary_barcode": 100045
}
```

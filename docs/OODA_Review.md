# OODA Review — Catalog App Dedup Logic

**Date:** 2026-04-17  
**Scope:** `scripts/clean_catalog.py` + `catalog-app/app.py`  
**Trigger:** 29 duplicate bib records discovered in Koha MySQL after 1700+ book import

---

## What's Currently Used for Matching

| Stage | Fields Matched | Notes |
|-------|---------------|-------|
| Within-upload | `title_norm` + `author_norm` + `edition_norm` (+ ISBN) | Flags second occurrence of same book in the same Gronthee file |
| Stage 1 | ISBN exact | Most reliable — bypasses all other checks |
| Stage 2 | `title_norm` + `author_norm` + `edition_norm` exact | 3-column key since G1 |
| Stage 3 | `title_norm` + `author_norm` fuzzy | rapidfuzz, 80% title / 72% author threshold; hard-blocks on edition conflict |

**Publisher, year, and pages are stored in the registry but never
consulted during matching.** They are display-only metadata.

---

## Root Cause of Duplicate Bibs

`clean_catalog.py` has **zero dedup logic** — it processes every row it
receives and generates MARC for all of them. If the same book appeared in
two different Gronthee files across two sessions, it was imported twice.

The catalog app's dedup check only exists at upload time (web UI), not
retrospectively. Books imported directly via `clean_catalog.py` before the
web app existed were never cross-checked.

---

## Gaps Found

| # | Gap | Risk | Example |
|---|-----|------|---------|
| G1 | Edition ignored in dedup key | Two editions of same title wrongly flagged DUPLICATE | "Sanchaita 6th ed." and "Sanchaita 7th ed." → user forced to add as copy |
| G2 | Publisher spelling variants | "Visva-Bharati" vs "Biswabharati" treated as different publishers | Fixed: added variants to `synonyms_publisher` in `koha_session_meta.json` |
| G3 | No within-upload cross-row dedup | Same book entered twice in one Gronthee file → both rows processed as NEW → duplicate bib in Koha | Volunteer scans the same book twice in one session |
| G4 | Year ignored | Different year reprints treated as same book | 1946 vs 1969 publication of same title |
| G5 | Pages ignored | Abridged vs complete editions indistinguishable | 63p vs 892p of same title |
| G6 | No dedup in clean_catalog.py | Past duplicates were never caught | Explains all 29 duplicate bibs in Koha |

---

## Registry Schema: What's Stored vs. What's Used

```sql
-- Current unique index — title + author + edition (G1)
CREATE UNIQUE INDEX idx_dedup ON books(title_norm, author_norm, edition_norm);

-- Columns stored but never queried during dedup
publisher  TEXT   -- stored, display only
year       TEXT   -- stored, display only

-- Columns not stored (intentional — too noisy as dedup keys)
pages      --  not stored
```

---

## Recommendations & Decisions

### G1 — Edition in dedup key ✅ Implemented

Added `edition_norm TEXT NOT NULL DEFAULT ''` column; unique index changed to `(title_norm, author_norm, edition_norm)`. Stage 2 query now includes edition. Stage 3 hard-blocks when both editions are known and differ. `backfill_registry.py` reads `biblioitems.editionstatement` from Koha MySQL.

### G3 — Within-upload cross-row dedup ✅ Implemented

`build_review_rows()` maintains a `seen_in_upload` dict while iterating rows. For each row, after checking the registry via `lookup_dup()`, it checks whether the same book (by ISBN or by `title_norm + author_norm + edition_norm`) appeared in an earlier row of the same file. If so:
- Row is flagged `DUPLICATE` with `dup_source='upload'`
- Status cell shows "↑ row N in this file" (instead of a registry barcode)
- Action dropdown shows only "Skip (duplicate in this file)" — copy options are hidden since no primary barcode exists yet
- `dup_row_num` field carries the 1-indexed row number of the first occurrence

### Do NOT add year/publisher/pages to the dedup key

These fields are too unreliable as dedup keys:
- **Year** — OCR errors, reprints vs editions are ambiguous
- **Publisher** — spelling variants ("Biswa Barathi" vs "Bishwabharati")
- **Pages** — minor reprints vary by a few pages; too noisy

Edition is the right and sufficient discriminator alongside title+author.

---

## Status

| Gap | Status |
|-----|--------|
| G1 — Edition in dedup key | ✅ Implemented |
| G2 — Publisher synonym variants | ✅ Fixed — variants added to `koha_session_meta.json` |
| G3 — Within-upload cross-row dedup | ✅ Implemented |
| G4 — Year display only | ✅ Acceptable — too noisy for dedup key |
| G5 — Pages display only | ✅ Acceptable — too noisy for dedup key |
| G6 — No dedup in clean_catalog.py | ✅ Superseded by catalog app — direct use of clean_catalog.py is discouraged going forward |

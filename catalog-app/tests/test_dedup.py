"""
Unit tests for the dedup registry logic:
  - extract_volume() and _volumes_conflict()
  - lookup_dup() — all three stages
  - register_books() — insert, idempotency, barcode mismatch warning
"""

import sqlite3

import pytest

import app as catalog_app
from app import (
    extract_volume,
    _volumes_conflict,
    lookup_dup,
    register_books,
)
from tests.conftest import seed_book


# ── extract_volume() and _volumes_conflict() ───────────────────────────────

class TestExtractVolume:
    def test_english_vol_number(self):
        assert extract_volume('Sharadindu Omnibus Vol 2') == '2'

    def test_english_volume_word(self):
        assert extract_volume('Complete Works Volume 3') == '3'

    def test_english_part(self):
        assert extract_volume('History Part 1') == '1'

    def test_roman_numeral(self):
        assert extract_volume('Works Vol III') == '3'

    def test_roman_numeral_iv(self):
        assert extract_volume('Works Vol IV') == '4'

    def test_bengali_khanda(self):
        # খণ্ড ২ — should return '2'
        assert extract_volume('রবীন্দ্র রচনাবলী খণ্ড ২') == '2'

    def test_bengali_digit(self):
        assert extract_volume('গল্পসমগ্র খণ্ড ৩') == '3'

    def test_no_volume_marker(self):
        assert extract_volume('Na Hanyate') is None

    def test_no_volume_in_plain_title(self):
        assert extract_volume('Galpa Samagra') is None


class TestVolumesConflict:
    def test_same_volume_no_conflict(self):
        assert _volumes_conflict('Works Vol 2', 'Works Vol 2') is False

    def test_different_volumes_conflict(self):
        assert _volumes_conflict('Works Vol 1', 'Works Vol 2') is True

    def test_one_missing_volume_is_conflict(self):
        # One title has a volume marker, the other has none.
        # The implementation treats this as a conflict (None != '2').
        # This prevents "Vol 2" from fuzzy-matching the un-numbered series title.
        assert _volumes_conflict('Works Vol 2', 'Works') is True

    def test_both_missing_volume_no_conflict(self):
        assert _volumes_conflict('Galpa Samagra', 'Galpa Samagra') is False

    def test_bengali_volume_conflict(self):
        a = 'রবীন্দ্র রচনাবলী খণ্ড ১'
        b = 'রবীন্দ্র রচনাবলী খণ্ড ২'
        assert _volumes_conflict(a, b) is True

    def test_bengali_volume_same(self):
        a = 'রবীন্দ্র রচনাবলী খণ্ড ২'
        b = 'রবীন্দ্র রচনাবলী খণ্ড ২'
        assert _volumes_conflict(a, b) is False


# ── lookup_dup() ───────────────────────────────────────────────────────────

class TestLookupDupStage1Isbn(object):
    """Stage 1: ISBN exact match."""

    def test_isbn_match_returns_record(self, tmp_db):
        seed_book(tmp_db, isbn='9788170669677', title_norm='galpa samagra',
                  author_norm='ashapurna devi', barcode='100045')
        result = lookup_dup('9788170669677', 'anything', 'anyone')
        assert result is not None
        assert result['barcode'] == '100045'
        assert result['fuzzy'] is False

    def test_isbn10_input_matches_isbn13_in_db(self, tmp_db):
        # Registry stores 13-digit ISBN; input may be 10-digit.
        # 8170669677 (ISBN-10) converts to 9788170669678 (ISBN-13).
        seed_book(tmp_db, isbn='9788170669678', barcode='100045')
        result = lookup_dup('8170669677', 'anything', 'anyone')
        assert result is not None
        assert result['barcode'] == '100045'

    def test_no_isbn_skips_stage1(self, tmp_db):
        seed_book(tmp_db, isbn='9788170669677', title_norm='galpa samagra',
                  author_norm='ashapurna devi', barcode='100045')
        # Empty ISBN → should not match Stage 1 (falls through to Stage 2)
        result = lookup_dup('', 'galpa samagra', 'ashapurna devi')
        assert result is not None   # matches Stage 2
        assert result['fuzzy'] is False

    def test_wrong_isbn_no_match(self, tmp_db):
        seed_book(tmp_db, isbn='9788170669677', barcode='100045')
        result = lookup_dup('9780000000000', 'anything', 'anyone')
        assert result is None


class TestLookupDupStage2Exact(object):
    """Stage 2: normalized title + author + edition exact match."""

    def test_exact_title_author_match(self, tmp_db):
        seed_book(tmp_db, title_norm='galpa samagra', author_norm='ashapurna devi',
                  edition_norm='', barcode='100045')
        result = lookup_dup('', 'Galpa Samagra', 'Ashapurna Devi')
        assert result is not None
        assert result['barcode'] == '100045'
        assert result['fuzzy'] is False

    def test_inverted_author_matches(self, tmp_db):
        # "Ashapurna Devi" and "Devi, Ashapurna" should normalize to same key
        seed_book(tmp_db, title_norm='galpa samagra', author_norm='ashapurna devi',
                  edition_norm='', barcode='100045')
        result = lookup_dup('', 'Galpa Samagra', 'Devi, Ashapurna')
        assert result is not None
        assert result['barcode'] == '100045'

    def test_edition_match_same(self, tmp_db):
        seed_book(tmp_db, title_norm='sanchaita', author_norm='ashapurna devi',
                  edition_norm='6th edition', barcode='100046')
        result = lookup_dup('', 'Sanchaita', 'Ashapurna Devi', '6th Edition')
        assert result is not None
        assert result['barcode'] == '100046'

    def test_edition_mismatch_no_stage2_match(self, tmp_db):
        # "6th Edition" vs "7th Edition" → different editions → no Stage 2 match
        seed_book(tmp_db, title_norm='sanchaita', author_norm='ashapurna devi',
                  edition_norm='6th edition', barcode='100046')
        result = lookup_dup('', 'Sanchaita', 'Ashapurna Devi', '7th Edition')
        # May match Stage 3 (fuzzy) but NOT Stage 2
        # We confirm by checking fuzzy flag
        if result is not None:
            assert result['fuzzy'] is True

    def test_empty_edition_matches_empty_edition(self, tmp_db):
        # Both editions empty → backward-compatible: match on title+author alone
        seed_book(tmp_db, title_norm='galpa samagra', author_norm='ashapurna devi',
                  edition_norm='', barcode='100045')
        result = lookup_dup('', 'Galpa Samagra', 'Ashapurna Devi', '')
        assert result is not None
        assert result['fuzzy'] is False

    def test_one_edition_empty_no_stage2_match(self, tmp_db):
        # Registry has edition; upload has no edition → no Stage 2 match
        seed_book(tmp_db, title_norm='sanchaita', author_norm='ashapurna devi',
                  edition_norm='6th edition', barcode='100046')
        result = lookup_dup('', 'Sanchaita', 'Ashapurna Devi', '')
        # Not a Stage 2 match; may be Stage 3 fuzzy
        if result is not None:
            assert result['fuzzy'] is True

    def test_no_match_returns_none(self, tmp_db):
        seed_book(tmp_db, title_norm='galpa samagra', author_norm='ashapurna devi',
                  barcode='100045')
        result = lookup_dup('', 'Completely Different Book', 'Other Author')
        assert result is None


class TestLookupDupStage3Fuzzy(object):
    """Stage 3: rapidfuzz match with hard blocks."""

    def test_fuzzy_match_on_spelling_variant(self, tmp_db):
        # "Bishad Sindhu" vs "Bisad Sindhu" — OCR variant of a well-known title.
        # 'sindhu' is the longest word and appears in both, so the LIKE pre-filter
        # finds the candidate before rapidfuzz scores it.
        seed_book(tmp_db, title_norm='bishad sindhu', author_norm='mir mosharraf hossain',
                  edition_norm='', title_display='Bishad Sindhu', barcode='100050')
        result = lookup_dup('', 'Bisad Sindhu', 'Mir Mosharraf Hossain')
        assert result is not None
        assert result['fuzzy'] is True
        assert result['fuzzy_score'] > 0

    def test_fuzzy_blocked_on_edition_conflict(self, tmp_db):
        # Both editions known and differ → hard block, no fuzzy match
        seed_book(tmp_db, title_norm='sanchaita', author_norm='ashapurna devi',
                  edition_norm='6th edition', title_display='Sanchaita', barcode='100046')
        result = lookup_dup('', 'Sanchaita', 'Ashapurna Devi', '7th Edition')
        # Hard block means no match at all (Stage 3 skipped for this candidate)
        assert result is None

    def test_fuzzy_blocked_on_volume_conflict(self, tmp_db):
        # Vol 1 vs Vol 2 → volume conflict → no fuzzy match
        seed_book(tmp_db, title_norm='sharadindu omnibus vol 1',
                  author_norm='bandyopadhyay sharadindu',
                  title_display='Sharadindu Omnibus Vol 1', barcode='100051')
        result = lookup_dup('', 'Sharadindu Omnibus Vol 2', 'Sharadindu Bandyopadhyay')
        assert result is None

    def test_fuzzy_allowed_when_one_edition_empty(self, tmp_db):
        # Registry has edition; upload has none → edition hard-block does NOT fire
        seed_book(tmp_db, title_norm='sanchaita', author_norm='ashapurna devi',
                  edition_norm='6th edition', title_display='Sanchaita', barcode='100046')
        result = lookup_dup('', 'Sanchaita', 'Ashapurna Devi', '')
        # Should fuzzy-match (no hard block since upload edition is empty)
        assert result is not None
        assert result['fuzzy'] is True


# ── register_books() ───────────────────────────────────────────────────────

class TestRegisterBooks:
    def test_inserts_new_book(self, tmp_db):
        books = [{'isbn': '', 'title': 'Galpa Samagra', 'author': 'Devi, Ashapurna',
                  'edition': '', 'publisher': '', 'year': '1993', 'barcode': '100045'}]
        skipped, warnings = register_books(books, 'test.xlsx')
        assert skipped == 0
        assert warnings == ''
        with sqlite3.connect(str(tmp_db)) as conn:
            row = conn.execute('SELECT barcode FROM books').fetchone()
        assert row[0] == '100045'

    def test_idempotent_second_insert(self, tmp_db):
        books = [{'isbn': '', 'title': 'Galpa Samagra', 'author': 'Devi, Ashapurna',
                  'edition': '', 'publisher': '', 'year': '1993', 'barcode': '100045'}]
        register_books(books, 'test.xlsx')
        skipped, warnings = register_books(books, 'test.xlsx')
        assert skipped == 1
        assert warnings == ''

    def test_barcode_mismatch_generates_warning(self, tmp_db):
        seed_book(tmp_db, title_norm='galpa samagra', author_norm='ashapurna devi',
                  edition_norm='', barcode='100045')
        # Same title+author but different barcode
        books = [{'isbn': '', 'title': 'Galpa Samagra', 'author': 'Devi, Ashapurna',
                  'edition': '', 'publisher': '', 'year': '', 'barcode': '100099'}]
        skipped, warnings = register_books(books, 'test.xlsx')
        assert skipped == 1
        assert '100099' in warnings
        assert '100045' in warnings

    def test_isbn_normalized_on_insert(self, tmp_db):
        # 8170669677 (ISBN-10) should be stored as 9788170669678 (ISBN-13)
        books = [{'isbn': '8170669677', 'title': 'Galpa Samagra',
                  'author': 'Devi, Ashapurna', 'edition': '',
                  'publisher': '', 'year': '', 'barcode': '100045'}]
        register_books(books, 'test.xlsx')
        with sqlite3.connect(str(tmp_db)) as conn:
            row = conn.execute('SELECT isbn FROM books').fetchone()
        assert row[0] == '9788170669678'

    def test_multiple_editions_insert_separately(self, tmp_db):
        books = [
            {'isbn': '', 'title': 'Sanchaita', 'author': 'Devi, Ashapurna',
             'edition': '6th Edition', 'publisher': '', 'year': '', 'barcode': '100046'},
            {'isbn': '', 'title': 'Sanchaita', 'author': 'Devi, Ashapurna',
             'edition': '7th Edition', 'publisher': '', 'year': '', 'barcode': '100047'},
        ]
        skipped, _ = register_books(books, 'test.xlsx')
        assert skipped == 0
        with sqlite3.connect(str(tmp_db)) as conn:
            count = conn.execute('SELECT COUNT(*) FROM books').fetchone()[0]
        assert count == 2

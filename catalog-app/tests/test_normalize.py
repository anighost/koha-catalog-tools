"""
Unit tests for pure normalization helpers in app.py.
No DB or Flask context required.
"""

import pytest
from app import (
    normalize,
    normalize_author,
    normalize_edition,
    clean_isbn,
    clean_year,
    next_copy_action,
)


# ── normalize() ────────────────────────────────────────────────────────────

class TestNormalize:
    def test_lowercase(self):
        assert normalize('Galpa Samagra') == 'galpa samagra'

    def test_strips_punctuation(self):
        assert normalize('Galpa-Samagra!') == 'galpasamagra'

    def test_collapses_whitespace(self):
        assert normalize('galpa   samagra') == 'galpa samagra'

    def test_strips_leading_trailing(self):
        assert normalize('  galpa samagra  ') == 'galpa samagra'

    def test_empty_string(self):
        assert normalize('') == ''

    def test_none_equivalent(self):
        assert normalize(None) == ''  # type: ignore[arg-type]

    def test_bengali_text_strips_combining_marks(self):
        # normalize() strips combining marks (virama, vowel signs) since they
        # are not matched by \w in Python's regex — this is a known trade-off.
        # Bengali dedup uses raw titles for volume detection (_BN_VOL_RE) precisely
        # because normalize() loses these marks.
        result = normalize('রবীন্দ্র রচনাবলী')
        assert len(result) > 0   # text is retained (base consonants survive)
        assert result == result.lower()  # lowercase applied

    def test_comma_stripped(self):
        assert normalize('Devi, Ashapurna') == 'devi ashapurna'


# ── normalize_author() ─────────────────────────────────────────────────────

class TestNormalizeAuthor:
    def test_sorts_words(self):
        # "Ashapurna Devi" and "Devi Ashapurna" should produce the same key
        assert normalize_author('Ashapurna Devi') == normalize_author('Devi Ashapurna')

    def test_comma_inverted_name(self):
        # "Devi, Ashapurna" — comma stripped, words sorted
        assert normalize_author('Devi, Ashapurna') == normalize_author('Ashapurna Devi')

    def test_three_word_name(self):
        a = normalize_author('Rabindranath Tagore Sen')
        b = normalize_author('Tagore Rabindranath Sen')
        assert a == b

    def test_empty(self):
        assert normalize_author('') == ''


# ── clean_isbn() ───────────────────────────────────────────────────────────

class TestCleanIsbn:
    def test_isbn13_passthrough(self):
        assert clean_isbn('9788170669677') == '9788170669677'

    def test_isbn10_converts_to_isbn13(self):
        # 8170669677 (ISBN-10) → 9788170669678 (ISBN-13, check digit = 8)
        result = clean_isbn('8170669677')
        assert result == '9788170669678'

    def test_strips_excel_float(self):
        # Excel sometimes exports ISBN as "8170669677.0"
        assert clean_isbn('8170669677.0') == '9788170669678'

    def test_strips_hyphens(self):
        assert clean_isbn('978-81-7066-967-7') == '9788170669677'

    def test_strips_isbn_prefix(self):
        assert clean_isbn('ISBN 9788170669677') == '9788170669677'
        assert clean_isbn('isbn9788170669677') == '9788170669677'

    def test_blank_returns_empty(self):
        assert clean_isbn('') == ''
        assert clean_isbn('Na') == ''
        assert clean_isbn('N/A') == ''

    def test_none_returns_empty(self):
        assert clean_isbn(None) == ''  # type: ignore[arg-type]

    def test_isbn10_with_x_check_digit(self):
        # ISBN-10 with X check digit — function returns cleaned form (not full 13)
        # since numeric conversion would fail; result should not be empty
        result = clean_isbn('047191595X')
        assert result != ''


# ── clean_year() ───────────────────────────────────────────────────────────

class TestCleanYear:
    def test_valid_year(self):
        assert clean_year('1993') == '1993'

    def test_year_with_text(self):
        assert clean_year('1993 (reprint)') == '1993'

    def test_float_artifact(self):
        assert clean_year('1993.0') == '1993'

    def test_future_year_rejected(self):
        assert clean_year('9999') == ''

    def test_too_old_rejected(self):
        assert clean_year('1799') == ''

    def test_blank(self):
        assert clean_year('') == ''

    def test_none(self):
        assert clean_year(None) == ''  # type: ignore[arg-type]

    def test_boundary_1800(self):
        assert clean_year('1800') == '1800'

    def test_text_only(self):
        assert clean_year('unknown') == ''


# ── next_copy_action() ─────────────────────────────────────────────────────

class TestNormalizeEdition:
    def test_bare_digit(self):
        assert normalize_edition('1') == '1'

    def test_ordinal_suffix(self):
        assert normalize_edition('1st') == '1'

    def test_ordinal_with_ed(self):
        assert normalize_edition('1st Ed') == '1'

    def test_ordinal_with_edition(self):
        assert normalize_edition('1st Edition.') == '1'

    def test_ocr_bang_ordinal(self):
        assert normalize_edition('!st Ed.') == '1'

    def test_second_ordinal(self):
        assert normalize_edition('2nd Edition') == '2'

    def test_second_bare(self):
        assert normalize_edition('2nd') == '2'

    def test_second_with_ed(self):
        assert normalize_edition('2nd Ed.') == '2'

    def test_third_ordinal(self):
        assert normalize_edition('3rd ed') == '3'

    def test_third_bare(self):
        assert normalize_edition('3rd') == '3'

    def test_third_with_edition(self):
        assert normalize_edition('3rd Edition') == '3'

    def test_fourth_ordinal(self):
        assert normalize_edition('4th Edition') == '4'

    def test_fourth_bare(self):
        assert normalize_edition('4th') == '4'

    def test_word_ordinal_first(self):
        assert normalize_edition('First Edition') == '1'

    def test_word_ordinal_second(self):
        assert normalize_edition('Second') == '2'

    def test_word_ordinal_third(self):
        assert normalize_edition('Third ed.') == '3'

    def test_word_ordinal_fourth(self):
        assert normalize_edition('Fourth Edition') == '4'

    def test_empty_string(self):
        assert normalize_edition('') == ''

    def test_none_equivalent(self):
        assert normalize_edition(None) == ''

    def test_all_first_variants_same(self):
        # All common ways "1st edition" can appear → same canonical "1"
        variants = ['1', '1st', '1st Ed', '1st Edition.', '!st Ed.', 'First', 'First Edition']
        results = {normalize_edition(v) for v in variants}
        assert results == {'1'}

    def test_all_second_variants_same(self):
        variants = ['2', '2nd', '2nd Ed', '2nd Edition', 'Second', 'Second Edition']
        results = {normalize_edition(v) for v in variants}
        assert results == {'2'}

    def test_all_third_variants_same(self):
        variants = ['3', '3rd', '3rd Ed.', '3rd Edition', 'Third', 'Third ed']
        results = {normalize_edition(v) for v in variants}
        assert results == {'3'}

    def test_all_fourth_variants_same(self):
        variants = ['4', '4th', '4th Ed', '4th Edition', 'Fourth Edition']
        results = {normalize_edition(v) for v in variants}
        assert results == {'4'}

    def test_different_editions_differ(self):
        assert normalize_edition('1st') != normalize_edition('2nd')
        assert normalize_edition('2nd') != normalize_edition('3rd')
        assert normalize_edition('3rd') != normalize_edition('4th')

    def test_revised_passthrough(self):
        # "revised" has no numeric equivalent — returned as-is after noise strip
        assert normalize_edition('Revised Edition') == 'revised'

    def test_noise_only_string(self):
        # "ed" alone → stripped to empty
        assert normalize_edition('Ed') == ''


class TestNextCopyAction:
    def test_one_copy_suggests_copy2(self):
        assert next_copy_action(1) == 'copy2'

    def test_two_copies_suggests_copy3(self):
        assert next_copy_action(2) == 'copy3'

    def test_three_copies_suggests_copy4(self):
        assert next_copy_action(3) == 'copy4'

    def test_four_copies_suggests_skip(self):
        assert next_copy_action(4) == 'skip'

    def test_many_copies_suggests_skip(self):
        assert next_copy_action(10) == 'skip'

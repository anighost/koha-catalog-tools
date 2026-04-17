"""
Unit tests for build_review_rows() — the function that converts raw XLSX rows
into review-ready dicts with initial dedup status.

Key behaviors tested:
  - Status assignment: NEW, DUPLICATE (registry), DUPLICATE (same-file / G3), FUZZY, ERROR
  - G3: within-upload cross-row dedup (same book twice in one file)
  - dup_source field: 'registry' vs 'upload'
  - dup_row_num: 1-indexed row of first occurrence for same-file dups
"""

import pytest

import app as catalog_app
from app import build_review_rows
from tests.conftest import seed_book, make_row


@pytest.fixture(autouse=True)
def patch_meta(monkeypatch):
    """All build_review_rows tests use empty synonym meta (no synonym processing)."""
    monkeypatch.setattr(catalog_app, 'load_meta', lambda: {})


# ── NEW status ─────────────────────────────────────────────────────────────

class TestNewStatus:
    def test_unknown_book_is_new(self, tmp_db):
        rows = [make_row(title='Galpa Samagra', author='Devi, Ashapurna')]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['status'] == 'NEW'
        assert result[0]['dup_source'] is None
        assert result[0]['dup_row_num'] is None

    def test_new_row_has_skip_action(self, tmp_db):
        rows = [make_row(title='Some Book', author='Some Author')]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['action'] == 'skip'


# ── ERROR status ───────────────────────────────────────────────────────────

class TestErrorStatus:
    def test_both_missing_is_error(self, tmp_db):
        # ERROR requires BOTH title AND author to be empty (app.py: `if not title and not author`)
        rows = [make_row(title='', author='')]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['status'] == 'ERROR'

    def test_missing_title_only_is_new(self, tmp_db):
        # Only title missing — app does NOT flag as ERROR (author is present)
        # This is current behavior: the AND condition in app.py line 682.
        rows = [make_row(title='', author='Some Author')]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['status'] == 'NEW'

    def test_missing_author_only_is_new(self, tmp_db):
        # Only author missing — app does NOT flag as ERROR (title is present)
        rows = [make_row(title='Some Title', author='')]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['status'] == 'NEW'


# ── DUPLICATE (registry) ───────────────────────────────────────────────────

class TestDuplicateRegistryStatus:
    def test_isbn_match_is_duplicate(self, tmp_db):
        seed_book(tmp_db, isbn='9788170669677', title_norm='galpa samagra',
                  author_norm='ashapurna devi', barcode='100045', copies=1)
        rows = [make_row(isbn='9788170669677', title='Galpa Samagra',
                         author='Devi, Ashapurna')]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['status'] == 'DUPLICATE'
        assert result[0]['dup_source'] == 'registry'
        assert result[0]['dup_barcode'] == '100045'
        assert result[0]['dup_row_num'] is None

    def test_title_author_match_is_duplicate(self, tmp_db):
        seed_book(tmp_db, title_norm='galpa samagra', author_norm='ashapurna devi',
                  edition_norm='', barcode='100045', copies=1)
        rows = [make_row(title='Galpa Samagra', author='Devi, Ashapurna')]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['status'] == 'DUPLICATE'
        assert result[0]['dup_source'] == 'registry'

    def test_duplicate_action_pre_selects_copy2(self, tmp_db):
        seed_book(tmp_db, title_norm='galpa samagra', author_norm='ashapurna devi',
                  edition_norm='', barcode='100045', copies=1)
        rows = [make_row(title='Galpa Samagra', author='Devi, Ashapurna')]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['action'] == 'copy2'

    def test_duplicate_with_2_copies_pre_selects_copy3(self, tmp_db):
        seed_book(tmp_db, title_norm='galpa samagra', author_norm='ashapurna devi',
                  edition_norm='', barcode='100045', copies=2)
        rows = [make_row(title='Galpa Samagra', author='Devi, Ashapurna')]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['action'] == 'copy3'

    def test_different_edition_is_new_not_duplicate(self, tmp_db):
        seed_book(tmp_db, title_norm='sanchaita', author_norm='ashapurna devi',
                  edition_norm='6th edition', barcode='100046', copies=1)
        rows = [make_row(title='Sanchaita', author='Devi, Ashapurna',
                         edition='7th Edition')]
        result = build_review_rows(rows, 'test.xlsx')
        # 7th Edition is a different edition — should not be an exact DUPLICATE
        assert result[0]['status'] in ('NEW', 'FUZZY')


# ── DUPLICATE (same-file / G3) ─────────────────────────────────────────────

class TestDuplicateUploadStatus:
    def test_same_title_author_twice_second_is_duplicate(self, tmp_db):
        rows = [
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
        ]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['status'] == 'NEW'
        assert result[1]['status'] == 'DUPLICATE'
        assert result[1]['dup_source'] == 'upload'
        assert result[1]['dup_row_num'] == 1    # first occurrence is row 1

    def test_same_isbn_twice_second_is_duplicate(self, tmp_db):
        rows = [
            make_row(isbn='9788170669677', title='Galpa Samagra',
                     author='Devi, Ashapurna'),
            make_row(isbn='9788170669677', title='Galpa Samagra',
                     author='Devi, Ashapurna'),
        ]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[1]['status'] == 'DUPLICATE'
        assert result[1]['dup_source'] == 'upload'

    def test_dup_row_num_points_to_correct_row(self, tmp_db):
        rows = [
            make_row(title='Book A', author='Author One'),
            make_row(title='Book B', author='Author Two'),
            make_row(title='Book A', author='Author One'),   # dup of row 1
        ]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[2]['status'] == 'DUPLICATE'
        assert result[2]['dup_source'] == 'upload'
        assert result[2]['dup_row_num'] == 1   # row 1 (1-indexed)

    def test_same_file_dup_has_no_barcode(self, tmp_db):
        rows = [
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
        ]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[1]['dup_barcode'] is None

    def test_same_file_dup_default_action_is_skip(self, tmp_db):
        rows = [
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
        ]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[1]['action'] == 'skip'

    def test_first_occurrence_stays_new(self, tmp_db):
        rows = [
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
        ]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['status'] == 'NEW'
        assert result[0]['dup_source'] is None

    def test_registry_dup_also_anchors_same_file_dup(self, tmp_db):
        # First row matches registry (DUPLICATE/registry).
        # Second row is the same book again — should be DUPLICATE/upload
        # (not registry, since registry anchor is already the first row).
        seed_book(tmp_db, title_norm='galpa samagra', author_norm='ashapurna devi',
                  edition_norm='', barcode='100045', copies=1)
        rows = [
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
        ]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['status'] == 'DUPLICATE'
        assert result[0]['dup_source'] == 'registry'
        # Second row: registry lookup also finds it, so it's registry dup too
        # (lookup_dup runs first, finds registry match before we check seen_in_upload)
        assert result[1]['status'] == 'DUPLICATE'

    def test_edition_aware_same_file_dup(self, tmp_db):
        # Same title/author but different editions — should NOT be flagged as same-file dup
        rows = [
            make_row(title='Sanchaita', author='Devi, Ashapurna', edition='6th Edition'),
            make_row(title='Sanchaita', author='Devi, Ashapurna', edition='7th Edition'),
        ]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['status'] == 'NEW'
        assert result[1]['status'] == 'NEW'   # different edition → not a same-file dup

    def test_three_rows_only_later_occurrences_flagged(self, tmp_db):
        rows = [
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
            make_row(title='Other Book', author='Other Author'),
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
        ]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['status'] == 'NEW'
        assert result[1]['status'] == 'NEW'
        assert result[2]['status'] == 'DUPLICATE'
        assert result[2]['dup_row_num'] == 1


# ── Miscellaneous ──────────────────────────────────────────────────────────

class TestBuildReviewRowsMisc:
    def test_pages_decimal_stripped(self, tmp_db):
        row = make_row(title='Some Book', author='Some Author')
        row[10] = '300.0'   # Excel float artifact
        result = build_review_rows([row], 'test.xlsx')
        assert result[0]['pages'] == '300'

    def test_idx_matches_position(self, tmp_db):
        rows = [
            make_row(title='Book A', author='Author One'),
            make_row(title='Book B', author='Author Two'),
        ]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['idx'] == 0
        assert result[1]['idx'] == 1

    def test_title_author_preserved_in_result(self, tmp_db):
        rows = [make_row(title='Galpa Samagra', author='Devi, Ashapurna')]
        result = build_review_rows(rows, 'test.xlsx')
        assert result[0]['title'] == 'Galpa Samagra'
        assert result[0]['author'] == 'Devi, Ashapurna'

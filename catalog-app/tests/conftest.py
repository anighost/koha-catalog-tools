"""
Shared fixtures and helpers for the catalog-app test suite.

All tests that touch the SQLite registry use the `tmp_db` fixture, which
redirects `app.DB_PATH` to a fresh temp file so the real dedup_registry.db
is never read or written during tests.
"""

import json
import sqlite3

import pytest

import app as catalog_app


# ── DB fixture ─────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """
    Point app.DB_PATH at a fresh temp SQLite file for the duration of the test,
    then re-run init_db() so the schema is created there instead of the real DB.
    """
    db_file = tmp_path / 'test_registry.db'
    monkeypatch.setattr(catalog_app, 'DB_PATH', db_file)
    catalog_app.init_db()
    return db_file


# ── Directory fixtures (for route tests) ──────────────────────────────────

@pytest.fixture
def tmp_dirs(tmp_path, monkeypatch):
    """Redirect sessions/, uploads/, output/ to temp dirs."""
    sessions = tmp_path / 'sessions'
    uploads  = tmp_path / 'uploads'
    output   = tmp_path / 'output'
    for d in (sessions, uploads, output):
        d.mkdir()
    monkeypatch.setattr(catalog_app, 'SESSIONS_DIR', sessions)
    monkeypatch.setattr(catalog_app, 'UPLOADS_DIR',  uploads)
    monkeypatch.setattr(catalog_app, 'OUTPUT_DIR',   output)
    return {'sessions': sessions, 'uploads': uploads, 'output': output}


# ── Flask test client ──────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_db, tmp_dirs, monkeypatch):
    """
    Flask test client with:
      - temp DB + directories
      - CSRF validation bypassed
      - load_meta() returns empty dict (no synonym processing)
    """
    monkeypatch.setattr(catalog_app, '_validate_csrf', lambda: None)
    monkeypatch.setattr(catalog_app, 'load_meta', lambda: {})
    catalog_app.app.config['TESTING'] = True
    catalog_app.app.config['SECRET_KEY'] = 'test-secret'
    with catalog_app.app.test_client() as c:
        yield c


@pytest.fixture
def auth_client(client):
    """Flask test client pre-authenticated (session['auth'] = True)."""
    with client.session_transaction() as sess:
        sess['auth'] = True
        sess['csrf_token'] = 'test-csrf'
    return client


# ── Helpers ────────────────────────────────────────────────────────────────

def seed_book(db_path, **kwargs):
    """Insert one book row directly into the test registry."""
    defaults = dict(
        isbn=None,
        title_norm='test title',
        author_norm='author test',
        edition_norm='',
        title_display='Test Title',
        author_display='Test, Author',
        publisher=None,
        year=None,
        barcode='100001',
        copies=1,
        source_file='test',
    )
    defaults.update(kwargs)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            '''INSERT INTO books
               (isbn, title_norm, author_norm, edition_norm,
                title_display, author_display, publisher, year,
                barcode, copies, source_file)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (defaults['isbn'], defaults['title_norm'], defaults['author_norm'],
             defaults['edition_norm'], defaults['title_display'], defaults['author_display'],
             defaults['publisher'], defaults['year'],
             defaults['barcode'], defaults['copies'], defaults['source_file']),
        )


def make_row(**kwargs):
    """
    Build a 40-element Gronthee-style row list with the given field values.
    All unspecified columns default to empty string.

    Keyword args map to column indices matching app.py constants:
      isbn, author, title, edition, publisher, year, item_type, home_branch, hold_branch
    """
    row = [''] * 40
    row[0]  = kwargs.get('isbn', '')
    row[2]  = kwargs.get('author', '')
    row[3]  = kwargs.get('title', '')
    row[4]  = kwargs.get('subtitle', '')
    row[6]  = kwargs.get('edition', '')
    row[7]  = kwargs.get('place', '')
    row[8]  = kwargs.get('publisher', '')
    row[9]  = kwargs.get('year', '')
    row[10] = kwargs.get('pages', '')
    row[25] = kwargs.get('item_type', 'BK')
    row[28] = kwargs.get('home_branch', 'DFL')
    row[29] = kwargs.get('hold_branch', 'DFL')
    return row

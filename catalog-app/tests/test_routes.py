"""
Integration tests for Flask routes.

Uses the `auth_client` fixture (test client pre-authenticated, CSRF bypassed,
load_meta() stubbed to return {}, DB and file dirs redirected to temp paths).
"""

import io
import json
import openpyxl

import pytest

import app as catalog_app
from tests.conftest import seed_book, make_row


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_xlsx_bytes(rows):
    """
    Build a minimal Gronthee-style XLSX in memory from a list of 40-column rows.
    Adds a header row matching clean_catalog.py column positions.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    # Minimal header row (content doesn't matter — parse_upload skips it)
    ws.append(['ISBN', 'Lang', 'Author', 'Title'] + [''] * 36)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ── /health ────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get('/health')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['status'] == 'ok'

    def test_health_no_auth_required(self, client):
        # /health must work without authentication
        resp = client.get('/health')
        assert resp.status_code == 200


# ── /login ─────────────────────────────────────────────────────────────────

class TestLogin:
    def test_get_login_page(self, client):
        resp = client.get('/login')
        assert resp.status_code == 200
        assert b'password' in resp.data.lower()

    def test_correct_password_redirects(self, client, monkeypatch):
        monkeypatch.setattr(catalog_app, 'CATALOG_PASSWORD', 'secret123')
        resp = client.post('/login', data={'password': 'secret123'},
                           follow_redirects=False)
        assert resp.status_code == 302
        assert '/' in resp.headers['Location']

    def test_wrong_password_stays_on_login(self, client, monkeypatch):
        monkeypatch.setattr(catalog_app, 'CATALOG_PASSWORD', 'secret123')
        resp = client.post('/login', data={'password': 'wrongpass'},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b'password' in resp.data.lower()

    def test_rate_limit_after_failures(self, client, monkeypatch):
        monkeypatch.setattr(catalog_app, 'CATALOG_PASSWORD', 'secret123')
        # Exhaust the 5-failure limit
        for _ in range(5):
            client.post('/login', data={'password': 'wrong'})
        resp = client.post('/login', data={'password': 'wrong'})
        # App returns 429 Too Many Requests once limit is hit
        assert resp.status_code == 429


# ── Unauthenticated redirects ──────────────────────────────────────────────

class TestAuthGuard:
    def test_upload_page_requires_auth(self, client):
        resp = client.get('/', follow_redirects=False)
        assert resp.status_code == 302
        assert 'login' in resp.headers['Location']

    def test_api_dedup_requires_auth(self, client):
        resp = client.get('/api/dedup?title=test&author=test',
                          follow_redirects=False)
        assert resp.status_code == 302
        assert 'login' in resp.headers['Location']


# ── /api/dedup ─────────────────────────────────────────────────────────────

class TestApiDedup:
    def test_unknown_book_returns_new(self, auth_client):
        resp = auth_client.get('/api/dedup?title=Unknown+Book&author=Unknown+Author')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['status'] == 'NEW'

    def test_known_isbn_returns_duplicate(self, auth_client, tmp_db):
        seed_book(tmp_db, isbn='9788170669677', title_norm='galpa samagra',
                  author_norm='ashapurna devi', barcode='100045', copies=1)
        resp = auth_client.get('/api/dedup?isbn=9788170669677&title=Galpa+Samagra'
                               '&author=Devi+Ashapurna')
        data = json.loads(resp.data)
        assert data['status'] == 'DUPLICATE'
        assert data['dup_barcode'] == '100045'

    def test_known_title_author_returns_duplicate(self, auth_client, tmp_db):
        seed_book(tmp_db, title_norm='galpa samagra', author_norm='ashapurna devi',
                  edition_norm='', barcode='100045', copies=1)
        resp = auth_client.get(
            '/api/dedup?title=Galpa+Samagra&author=Devi%2C+Ashapurna&isbn=&edition=')
        data = json.loads(resp.data)
        assert data['status'] == 'DUPLICATE'

    def test_empty_title_author_returns_error(self, auth_client):
        resp = auth_client.get('/api/dedup?title=&author=&isbn=')
        data = json.loads(resp.data)
        assert data['status'] == 'ERROR'

    def test_next_action_reflects_copy_count(self, auth_client, tmp_db):
        seed_book(tmp_db, title_norm='galpa samagra', author_norm='ashapurna devi',
                  edition_norm='', barcode='100045', copies=2)
        resp = auth_client.get(
            '/api/dedup?title=Galpa+Samagra&author=Devi%2C+Ashapurna&isbn=&edition=')
        data = json.loads(resp.data)
        assert data['next_action'] == 'copy3'


# ── POST /upload ───────────────────────────────────────────────────────────

class TestUpload:
    def test_upload_xlsx_creates_session(self, auth_client, tmp_dirs):
        rows = [make_row(title='Galpa Samagra', author='Devi, Ashapurna')]
        xlsx = _make_xlsx_bytes(rows)
        resp = auth_client.post(
            '/upload',
            data={'file': (io.BytesIO(xlsx), 'test.xlsx')},
            content_type='multipart/form-data',
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert '/review/' in resp.headers['Location']
        # Session file should have been created
        sessions = list(tmp_dirs['sessions'].glob('*.json'))
        assert len(sessions) == 1

    def test_upload_empty_file_flashes_error(self, auth_client):
        resp = auth_client.post(
            '/upload',
            data={'file': (io.BytesIO(b''), 'empty.xlsx')},
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Should stay on upload page (flash message)
        assert b'upload' in resp.data.lower() or b'file' in resp.data.lower()

    def test_upload_no_file_flashes_error(self, auth_client):
        resp = auth_client.post('/upload', data={},
                                content_type='multipart/form-data',
                                follow_redirects=True)
        assert resp.status_code == 200

    def test_upload_shows_correct_row_count(self, auth_client, tmp_dirs):
        rows = [
            make_row(title='Book One', author='Author A'),
            make_row(title='Book Two', author='Author B'),
            make_row(title='Book Three', author='Author C'),
        ]
        xlsx = _make_xlsx_bytes(rows)
        resp = auth_client.post(
            '/upload',
            data={'file': (io.BytesIO(xlsx), 'test.xlsx')},
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        assert resp.status_code == 200
        sessions = list(tmp_dirs['sessions'].glob('*.json'))
        session_data = json.loads(sessions[0].read_text())
        assert len(session_data['rows']) == 3

    def test_upload_detects_within_file_duplicate(self, auth_client, tmp_dirs):
        # Same book twice → second row should be DUPLICATE/upload
        rows = [
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
            make_row(title='Galpa Samagra', author='Devi, Ashapurna'),
        ]
        xlsx = _make_xlsx_bytes(rows)
        auth_client.post(
            '/upload',
            data={'file': (io.BytesIO(xlsx), 'test.xlsx')},
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        sessions = list(tmp_dirs['sessions'].glob('*.json'))
        session_data = json.loads(sessions[0].read_text())
        assert session_data['rows'][0]['status'] == 'NEW'
        assert session_data['rows'][1]['status'] == 'DUPLICATE'
        assert session_data['rows'][1]['dup_source'] == 'upload'
        assert session_data['rows'][1]['dup_row_num'] == 1

    def test_upload_detects_registry_duplicate(self, auth_client, tmp_dirs, tmp_db):
        seed_book(tmp_db, title_norm='galpa samagra', author_norm='ashapurna devi',
                  edition_norm='', barcode='100045', copies=1)
        rows = [make_row(title='Galpa Samagra', author='Devi, Ashapurna')]
        xlsx = _make_xlsx_bytes(rows)
        auth_client.post(
            '/upload',
            data={'file': (io.BytesIO(xlsx), 'test.xlsx')},
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        sessions = list(tmp_dirs['sessions'].glob('*.json'))
        session_data = json.loads(sessions[0].read_text())
        assert session_data['rows'][0]['status'] == 'DUPLICATE'
        assert session_data['rows'][0]['dup_source'] == 'registry'


# ── /heartbeat ─────────────────────────────────────────────────────────────

class TestHeartbeat:
    def test_heartbeat_authenticated(self, auth_client):
        resp = auth_client.get('/heartbeat')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get('ok') is True

    def test_heartbeat_unauthenticated(self, client):
        resp = client.get('/heartbeat', follow_redirects=False)
        assert resp.status_code == 302

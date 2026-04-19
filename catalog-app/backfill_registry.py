"""
backfill_registry.py
Seed (or re-seed) the SQLite dedup registry from Koha's MySQL biblio/items tables.

Reads the database name from koha-conf.xml; connects via 'sudo mysql' (root access,
no password required — requires the sudoers rule for mysql to be in place).

Usage:
    # Clear registry and reload fresh from Koha:
    sqlite3 /home/dishari/koha-catalog-tools/catalog-app/dedup_registry.db \
        "DELETE FROM books;"
    python3 /home/dishari/koha-catalog-tools/catalog-app/backfill_registry.py

Safe to re-run without DELETE — uses INSERT OR IGNORE so existing rows are kept.
"""

import re, sqlite3, subprocess, sys, xml.etree.ElementTree as ET
from pathlib import Path

KOHA_INSTANCE = 'dishari_lib'
KOHA_CONF     = f'/etc/koha/sites/{KOHA_INSTANCE}/koha-conf.xml'
DB_PATH       = Path(__file__).parent / 'dedup_registry.db'


# ── Normalization (mirrors app.py) ─────────────────────────────────────────

def normalize(text: str) -> str:
    if not text:
        return ''
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def normalize_author(text: str) -> str:
    return ' '.join(sorted(normalize(text).split()))


_EDITION_WORD_MAP = {
    'first': '1', 'second': '2', 'third': '3', 'fourth': '4',
    'fifth': '5', 'sixth': '6', 'seventh': '7', 'eighth': '8',
    'ninth': '9', 'tenth': '10',
}
_EDITION_NOISE = re.compile(r'\b(edition|editions|ed|eds)\b\.?', re.IGNORECASE)


def normalize_edition(text: str) -> str:
    if not text:
        return ''
    s = text.strip()
    s = re.sub(r'!(?=st|nd|rd|th|\s|$)', '1', s, flags=re.IGNORECASE)
    s = normalize(s)
    if not s:
        return ''
    m = re.match(r'^(\d+)(?:st|nd|rd|th)?\b', s)
    if m:
        return m.group(1)
    first_word = s.split()[0]
    if first_word in _EDITION_WORD_MAP:
        return _EDITION_WORD_MAP[first_word]
    s = _EDITION_NOISE.sub('', s).strip()
    return s


# ── Read DB name from koha-conf.xml ───────────────────────────────────────

def read_koha_db_name(conf_path: str) -> str:
    """Read only the database name from koha-conf.xml.
    MySQL access uses 'sudo mysql' (root) — no user/pass needed."""
    try:
        tree = ET.parse(conf_path)
        root = tree.getroot()
        instance = KOHA_INSTANCE.replace('-', '_')
        all_dbs = [el.text.strip() for el in root.findall('.//database') if el.text]
        db = next((d for d in all_dbs if instance in d), all_dbs[0] if all_dbs else '')
        if not db:
            print(f"ERROR: could not find database name in {conf_path}")
            sys.exit(1)
        return db
    except Exception as e:
        print(f"ERROR reading {conf_path}: {e}")
        sys.exit(1)


# ── Query Koha MySQL via subprocess (sudo mysql — root access, no password) ─

def fetch_koha_books(db_name: str) -> list[dict]:
    """
    Return one row per bib that has at least one item with a primary barcode
    (starts with '10').  Aggregates copy count across all items.
    """
    sql = (
        "SELECT b.biblionumber, b.title, b.author, bi.isbn, bi.editionstatement,"
        " MIN(CASE WHEN i.barcode LIKE '10%' THEN i.barcode END) AS primary_barcode,"
        " COUNT(i.itemnumber) AS copies"
        " FROM biblio b"
        " JOIN biblioitems bi USING(biblionumber)"
        " LEFT JOIN items i USING(biblionumber)"
        " GROUP BY b.biblionumber, b.title, b.author, bi.isbn, bi.editionstatement"
        " HAVING primary_barcode IS NOT NULL;"
    )

    cmd = ['sudo', 'mysql', db_name, '-N', '--batch', '-e', sql]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR querying MySQL:\n{result.stderr}")
        sys.exit(1)

    books = []
    for line in result.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) < 7:
            continue
        _, title, author, isbn, edition, primary_barcode, copies = parts[:7]
        title   = title.strip()
        author  = author.strip()
        isbn    = isbn.strip() if isbn.strip() not in ('', 'NULL') else ''
        edition = edition.strip() if edition.strip() not in ('', 'NULL') else ''
        barcode = primary_barcode.strip()
        try:
            copies = int(copies.strip())
        except ValueError:
            copies = 1

        if not title or not barcode:
            continue

        books.append({
            'isbn':           isbn,
            'edition_norm':   normalize_edition(edition),
            'title_norm':     normalize(title),
            'author_norm':    normalize_author(author),
            'title_display':  title,
            'author_display': author,
            'barcode':        barcode,
            'copies':         copies,
        })

    return books


# ── Insert into SQLite registry ────────────────────────────────────────────

def backfill(books: list[dict]) -> tuple[int, int]:
    inserted = skipped = 0
    with sqlite3.connect(str(DB_PATH)) as conn:
        for b in books:
            try:
                conn.execute(
                    '''INSERT OR IGNORE INTO books
                       (isbn, title_norm, author_norm, edition_norm,
                        title_display, author_display,
                        barcode, copies, source_file)
                       VALUES (?,?,?,?,?,?,?,?,'koha-backfill')''',
                    (b['isbn'], b['title_norm'], b['author_norm'], b['edition_norm'],
                     b['title_display'], b['author_display'],
                     b['barcode'], b['copies'])
                )
                if conn.execute('SELECT changes()').fetchone()[0]:
                    inserted += 1
                else:
                    skipped += 1
            except sqlite3.IntegrityError:
                skipped += 1
    return inserted, skipped


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    print(f"Reading Koha config from {KOHA_CONF} …")
    db_name = read_koha_db_name(KOHA_CONF)
    print(f"Using database: {db_name} (connecting via sudo mysql)")

    books = fetch_koha_books(db_name)
    print(f"Fetched {len(books)} bibs from Koha")

    if not books:
        print("Nothing to insert — check that items have barcodes starting with '10'.")
        sys.exit(0)

    inserted, skipped = backfill(books)
    print(f"Done — inserted {inserted} new rows, skipped {skipped} already present.")


if __name__ == '__main__':
    main()

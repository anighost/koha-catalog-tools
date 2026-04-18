"""
backfill_registry.py
Run once on the Koha server to seed the SQLite dedup registry from
Koha's MySQL biblio/items tables.

Reads MySQL credentials automatically from Koha's config file:
  /etc/koha/sites/<KOHA_INSTANCE>/koha-conf.xml

Usage:
    python3 /home/dishari/koha-catalog-tools/catalog-app/backfill_registry.py

Safe to re-run — uses INSERT OR IGNORE so existing registry rows are never
overwritten.
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


# ── Read MySQL credentials from koha-conf.xml ──────────────────────────────

def read_koha_db_config(conf_path: str) -> dict:
    try:
        tree = ET.parse(conf_path)
        root = tree.getroot()
        # Credentials live inside <config><database> or directly under <config>
        def _get(tag):
            el = root.find(f'.//{tag}')
            return el.text.strip() if el is not None and el.text else ''
        # Koha config may have multiple user/pass blocks; find the one
        # that matches the instance database name pattern (koha_<instance>)
        instance = KOHA_INSTANCE.replace('-', '_')
        all_users = [el.text.strip() for el in root.findall('.//user') if el.text]
        all_passes = [el.text.strip() for el in root.findall('.//pass') if el.text]
        all_dbs   = [el.text.strip() for el in root.findall('.//database') if el.text]

        # Prefer the user/db that contains the instance name
        user = next((u for u in all_users if instance in u), all_users[0] if all_users else '')
        db   = next((d for d in all_dbs   if instance in d), all_dbs[0]   if all_dbs   else '')
        idx  = all_users.index(user) if user in all_users else 0
        passwd = all_passes[idx] if idx < len(all_passes) else (all_passes[0] if all_passes else '')

        return {
            'host':   _get('hostname') or _get('host') or 'localhost',
            'port':   _get('port') or '3306',
            'user':   user,
            'passwd': passwd,
            'db':     db,
        }
    except Exception as e:
        print(f"ERROR reading {conf_path}: {e}")
        sys.exit(1)


# ── Query Koha MySQL via subprocess (mysql CLI) ────────────────────────────

def fetch_koha_books(cfg: dict) -> list[dict]:
    """
    Return one row per bib that has at least one item with a primary barcode
    (starts with '10').  Aggregates copy count across all items.
    """
    sql = """
SELECT
    b.biblionumber,
    b.title,
    b.author,
    bi.isbn,
    bi.editionstatement,
    MIN(CASE WHEN i.barcode LIKE '10%' THEN i.barcode END) AS primary_barcode,
    COUNT(i.itemnumber) AS copies
FROM biblio b
JOIN biblioitems bi USING(biblionumber)
LEFT JOIN items i USING(biblionumber)
GROUP BY b.biblionumber, b.title, b.author, bi.isbn, bi.editionstatement
HAVING primary_barcode IS NOT NULL;
""".strip()

    cmd = [
        'mysql',
        f'-h{cfg["host"]}',
        f'-P{cfg["port"]}',
        f'-u{cfg["user"]}',
        f'-p{cfg["passwd"]}',
        '--batch', '--raw', '--skip-column-names',
        cfg['db'],
        '-e', sql,
    ]

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
    cfg = read_koha_db_config(KOHA_CONF)
    if not cfg['user'] or not cfg['db']:
        print("ERROR: could not read DB credentials from koha-conf.xml")
        sys.exit(1)
    print(f"Connecting to MySQL {cfg['db']} at {cfg['host']}:{cfg['port']} …")

    books = fetch_koha_books(cfg)
    print(f"Fetched {len(books)} bibs from Koha")

    if not books:
        print("Nothing to insert — check that items have barcodes starting with '10'.")
        sys.exit(0)

    inserted, skipped = backfill(books)
    print(f"Done — inserted {inserted} new rows, skipped {skipped} already present.")


if __name__ == '__main__':
    main()

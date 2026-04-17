"""
backfill_registry.py
Run once on the Koha server to populate title_display / author_display
in the SQLite dedup registry from Koha's MySQL biblio table.

Usage (on the server):
    python3 /home/dishari/catalog-app/backfill_registry.py
"""

import sqlite3, subprocess, sys
from pathlib import Path

DB_PATH     = Path(__file__).parent / 'dedup_registry.db'
KOHA_INSTANCE = 'dishari_lib'

def main():
    # Pull barcode → title + author from Koha MySQL
    sql = (
        "SELECT i.barcode, b.title, b.author "
        "FROM items i "
        "JOIN biblio b USING(biblionumber) "
        "WHERE i.barcode REGEXP '^1[0-3][0-9]+' "
        "AND i.copynumber = 1;"   # primary copies only — matches registry entries
    )
    result = subprocess.run(
        ['sudo', 'koha-mysql', KOHA_INSTANCE,
         '--batch', '--raw', '-e', sql],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR querying Koha MySQL:\n{result.stderr}")
        sys.exit(1)

    rows = []
    for line in result.stdout.splitlines()[1:]:   # skip header row
        parts = line.split('\t')
        if len(parts) >= 3:
            barcode = parts[0].strip()
            title   = parts[1].strip()
            author  = parts[2].strip()
            if barcode and title:
                rows.append((title, author, barcode))

    if not rows:
        print("No rows returned from Koha — check KOHA_INSTANCE name.")
        sys.exit(1)

    print(f"Fetched {len(rows)} records from Koha MySQL")

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executemany(
            '''UPDATE books
               SET title_display  = COALESCE(NULLIF(title_display,  ''), ?),
                   author_display = COALESCE(NULLIF(author_display, ''), ?)
               WHERE barcode = ?''',
            rows
        )
        print(f"Updated {conn.total_changes} rows in SQLite registry")

if __name__ == '__main__':
    main()

"""
catalog_engine.py
Subprocess wrapper around clean_catalog.py for the catalog web app.

Patches INPUT_FILE, STATE_FILE, OUTPUT_DIR constants in the script, runs it
as a subprocess, and returns paths to the generated outputs.
Uses filelock to serialize barcode allocation across concurrent web requests.
"""

import io, os, re, shutil, sys, subprocess, tempfile
from pathlib import Path
from datetime import datetime

try:
    from filelock import FileLock
except ImportError:
    class FileLock:
        """No-op fallback when filelock is not installed."""
        def __init__(self, path, timeout=120): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass

# ── Path configuration ─────────────────────────────────────────────────────
_HERE = Path(__file__).parent

CATALOG_SCRIPT = Path(os.environ.get(
    'CATALOG_SCRIPT',
    str(_HERE.parent / 'scripts' / 'clean_catalog.py')
))
META_FILE = Path(os.environ.get(
    'CATALOG_META',
    str(_HERE / 'koha_session_meta.json')
))
LOCK_FILE = str(META_FILE) + '.lock'


def run(input_path: str) -> dict:
    """
    Process a Gronthee XLSX/CSV through clean_catalog.py.

    Returns dict:
      success  bool
      mrc      path to .mrc output (or None)
      audit    path to audit .xlsx (or None)
      errors   path to errors .xlsx (or None)
      stdout   captured stdout
      stderr   captured stderr
      out_dir  temp directory containing all outputs
    """
    if not CATALOG_SCRIPT.exists():
        return {
            'success': False,
            'mrc': None, 'audit': None, 'errors': None,
            'stdout': '',
            'stderr': f'clean_catalog.py not found at {CATALOG_SCRIPT}',
            'out_dir': None,
        }

    out_dir = tempfile.mkdtemp(prefix='catalog_run_')

    # Read source and patch the three module-level constants
    source = CATALOG_SCRIPT.read_text(encoding='utf-8')
    source = re.sub(r'^INPUT_FILE\s*=\s*.+$',
                    f'INPUT_FILE = {repr(str(input_path))}',
                    source, flags=re.MULTILINE)
    source = re.sub(r'^STATE_FILE\s*=\s*.+$',
                    f'STATE_FILE = {repr(str(META_FILE))}',
                    source, flags=re.MULTILINE)
    source = re.sub(r'^OUTPUT_DIR\s*=\s*.+$',
                    f'OUTPUT_DIR = {repr(out_dir)}',
                    source, flags=re.MULTILINE)

    patched = os.path.join(out_dir, '_run.py')
    with open(patched, 'w', encoding='utf-8') as f:
        f.write(source)

    # Filelock serializes access to koha_session_meta.json (barcode counter)
    lock = FileLock(LOCK_FILE, timeout=120)
    try:
        with lock:
            result = subprocess.run(
                [sys.executable, patched],
                capture_output=True, text=True, timeout=300
            )
    except Exception as exc:
        # Subprocess failed to even start or timed out — clean up and re-raise
        shutil.rmtree(out_dir, ignore_errors=True)
        raise RuntimeError(f'catalog_engine.run failed: {exc}') from exc

    def _find(predicate):
        for fname in sorted(os.listdir(out_dir)):
            if not fname.startswith('_') and predicate(fname):
                return os.path.join(out_dir, fname)
        return None

    # If the subprocess itself errored, clean up immediately — caller gets stderr
    if result.returncode != 0:
        out = {
            'success': False,
            'mrc': None, 'audit': None, 'errors': None,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'out_dir': None,
        }
        shutil.rmtree(out_dir, ignore_errors=True)
        return out

    return {
        'success': True,
        'mrc':    _find(lambda f: f.endswith('.mrc')),
        'audit':  _find(lambda f: f.startswith('cleaned_') and f.endswith('.xlsx')),
        'errors': _find(lambda f: f.startswith('error_') and f.endswith('.xlsx')),
        'stdout': result.stdout,
        'stderr': result.stderr,
        'out_dir': out_dir,
    }


def generate_copy_marc(fields: dict, copy_barcode: str, copy_num: int = 2, biblionumber: str = '') -> bytes:
    """
    Build a MARC record for adding a physical copy to an existing bib.

    Includes full bibliographic fields (020, 100, 245, 250, 260) so that
    STRICT_CLE matching (Title+Author+Edition+Publisher+Year) can locate the
    existing bib and attach the new 952 item rather than creating a duplicate bib.

    952 subfields match clean_catalog.py: $a $b $p $o $d $y $t $8 $g.
    """
    try:
        from pymarc import Record, Field, Subfield, MARCWriter
    except ImportError:
        raise RuntimeError('pymarc not installed — run: pip3 install pymarc')

    def _s(key, default=''):
        return (fields.get(key) or default).strip() or default

    rec = Record()
    rec.leader = '00000nam a22000007a 4500'

    # 020 — ISBN (used as fallback match if STRICT_CLE fails)
    from re import sub as _sub
    isbn_raw = _sub(r'\.0+$', '', (fields.get('isbn') or '').strip())
    isbn_raw = _sub(r'[^\dXx]', '', isbn_raw).upper()
    if isbn_raw:
        rec.add_field(Field('020', [' ', ' '], subfields=[Subfield('a', isbn_raw)]))

    # 100 — Author (needed for STRICT_CLE match)
    author = _s('author')
    if author:
        rec.add_field(Field('100', ['1', ' '], subfields=[Subfield('a', author)]))

    # 245 — Title (needed for STRICT_CLE match)
    title = _s('title')
    if title:
        rec.add_field(Field('245', ['1', '0'], subfields=[Subfield('a', title + '.')]))

    # 250 — Edition (needed for STRICT_CLE match when edition is present)
    edition = _s('edition')
    if edition:
        rec.add_field(Field('250', [' ', ' '], subfields=[Subfield('a', edition)]))

    # 260 — Publication info (needed for STRICT_CLE match on Publisher+Year)
    place, publisher, year = _s('place'), _s('publisher'), _s('year')
    if publisher or year:
        sfs = []
        if place:     sfs.append(Subfield('a', place + ' :'))
        if publisher: sfs.append(Subfield('b', publisher + ','))
        if year:      sfs.append(Subfield('c', year + '.'))
        rec.add_field(Field('260', [' ', ' '], subfields=sfs))

    # 942 — Koha bib-level item type
    itype = _s('item_type', 'BK') or 'BK'
    rec.add_field(Field('942', [' ', ' '], subfields=[Subfield('c', itype)]))

    # 999 — Koha internal bib ID for Local-Number matching
    # Allows the match rule to locate the exact existing bib regardless of
    # title/author/ISBN variations. Only added when biblionumber is known.
    if biblionumber:
        rec.add_field(Field('999', [' ', ' '], subfields=[Subfield('c', biblionumber)]))

    # 952 — Item/holdings (parity with clean_catalog.py)
    home  = _s('home_branch', 'DFL') or 'DFL'
    hold  = _s('hold_branch', 'DFL') or 'DFL'
    call  = _s('call_no',     '891') or '891'
    date  = (_s('date') or datetime.now().strftime('%Y-%m-%d')).split(' ')[0].split('T')[0]
    ccode = _s('ccode')
    cost  = _s('cost')

    subfields_952 = [
        Subfield('a', home),
        Subfield('b', hold),
        Subfield('p', copy_barcode),
        Subfield('o', call),
        Subfield('d', date),
        Subfield('y', itype),
        Subfield('t', str(copy_num)),   # copy number — shown in OPAC & used for holds
    ]
    if ccode:
        subfields_952.append(Subfield('8', ccode))
    if cost:
        subfields_952.append(Subfield('g', cost))
    rec.add_field(Field('952', [' ', ' '], subfields=subfields_952))

    buf = io.BytesIO()
    w = MARCWriter(buf)
    w.write(rec)
    data = buf.getvalue()
    w.close()
    return data


def extract_processed_books(audit_path: str) -> list:
    """
    Parse the audit XLSX generated by catalog_engine.run() to extract the
    (isbn, title, author, barcode) of every processed book.
    Returns only primary (10XXXX) barcode rows — ignores copy rows.
    """
    try:
        import openpyxl
    except ImportError:
        return []

    wb = openpyxl.load_workbook(audit_path, read_only=True, data_only=True)
    ws = wb.active
    books = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row:
            continue
        barcode = str(row[35] if len(row) > 35 else '').strip()
        if not barcode.startswith('10'):
            continue  # skip copy rows (11/12/13 prefix) and blank rows
        books.append({
            'isbn':      str(row[0]  if len(row) > 0  else '').strip(),
            'author':    str(row[2]  if len(row) > 2  else '').strip(),
            'title':     str(row[3]  if len(row) > 3  else '').strip(),
            'edition':   str(row[6]  if len(row) > 6  else '').strip(),
            'publisher': str(row[8]  if len(row) > 8  else '').strip(),
            'year':      str(row[9]  if len(row) > 9  else '').strip(),
            'pages':     str(row[10] if len(row) > 10 else '').strip(),
            'barcode':   barcode,
        })
    wb.close()
    return books

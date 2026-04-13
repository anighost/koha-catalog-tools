"""
prepare_covers.py
-----------------
Reads image_mapping/image_mapping_output.xlsx, copies + converts each matched
image from the DONE directory to the UPLOAD directory (as <biblionumber>.jpeg),
then writes a datalink.txt in the format Koha expects:

    biblionumber,filename

Run:
    python scripts/prepare_covers.py [--dry-run]

Options:
    --dry-run   Print what would be done without copying any files.

Dependencies (auto-installed on first run): openpyxl, Pillow
"""

import os
import sys
import subprocess

# ── Auto-install dependencies ──────────────────────────────────────────────
for pkg in ('openpyxl', 'Pillow'):
    try:
        __import__('openpyxl' if pkg == 'openpyxl' else 'PIL')
    except ImportError:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg, '-q'])

import openpyxl
from PIL import Image

# ── Config ─────────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

MAPPING_FILE = os.path.join(PROJECT_ROOT, 'image_mapping', 'image_mapping_output.xlsx')
DONE_DIR     = '/Users/anirbanghosh/Dishari/Library/Catalog/Catalog_AI/Image/DONE'
UPLOAD_DIR   = '/Users/anirbanghosh/Dishari/Library/Catalog/Catalog_AI/Image/UPLOAD'
DATALINK_OUT = os.path.join(UPLOAD_DIR, 'datalink.txt')

# Column indices (0-based) in image_mapping_output.xlsx
# Expected header: Author | Title | Image File Name | Confidence % | Confidence Level | Match Notes | biblionumber
COL_IMAGE_FILE   = 2   # "Image File Name"
COL_CONFIDENCE   = 4   # "Confidence Level"
COL_BIBLIONUMBER = 6   # "biblionumber"

DRY_RUN = '--dry-run' in sys.argv
# ───────────────────────────────────────────────────────────────────────────


def load_mapping(path):
    """Return list of (biblionumber, image_filename, confidence_level) tuples."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    # Skip header row
    entries = []
    for row in rows[1:]:
        try:
            img_file   = str(row[COL_IMAGE_FILE]).strip()   if row[COL_IMAGE_FILE]   else ''
            confidence = str(row[COL_CONFIDENCE]).strip()   if row[COL_CONFIDENCE]   else ''
            biblio_raw = row[COL_BIBLIONUMBER]
        except IndexError:
            continue

        if not img_file or not biblio_raw:
            continue

        # biblionumber may be int or float from Excel
        try:
            biblionumber = int(float(str(biblio_raw)))
        except (ValueError, TypeError):
            continue

        entries.append((biblionumber, img_file, confidence))

    wb.close()
    return entries


def find_source(img_file):
    """Locate the source image in DONE_DIR (case-insensitive, extension-flexible)."""
    # Exact match first
    exact = os.path.join(DONE_DIR, img_file)
    if os.path.exists(exact):
        return exact

    stem = os.path.splitext(img_file)[0].lower()
    for fname in os.listdir(DONE_DIR):
        fstem = os.path.splitext(fname)[0].lower()
        if fstem == stem:
            return os.path.join(DONE_DIR, fname)

    return None


def copy_as_jpeg(src, dest):
    """Convert src image to JPEG and save to dest."""
    with Image.open(src) as img:
        rgb = img.convert('RGB')
        rgb.save(dest, 'JPEG', quality=92, optimize=True)


def main():
    if not os.path.exists(MAPPING_FILE):
        print(f'ERROR: mapping file not found: {MAPPING_FILE}')
        sys.exit(1)
    if not os.path.isdir(DONE_DIR):
        print(f'ERROR: DONE directory not found: {DONE_DIR}')
        sys.exit(1)
    if not DRY_RUN:
        os.makedirs(UPLOAD_DIR, exist_ok=True)

    print(f'Reading mapping from {MAPPING_FILE}')
    entries = load_mapping(MAPPING_FILE)
    print(f'Found {len(entries)} mapped entries\n')

    datalink_lines = []
    skipped = []
    copied  = []
    already = []

    for biblionumber, img_file, confidence in entries:
        dest_name = f'{biblionumber}.jpeg'
        dest_path = os.path.join(UPLOAD_DIR, dest_name)

        src_path = find_source(img_file)
        if not src_path:
            print(f'  MISSING  {img_file} -> biblionumber {biblionumber} [{confidence}]')
            skipped.append((biblionumber, img_file))
            continue

        if os.path.exists(dest_path):
            print(f'  EXISTS   {dest_name}  (skipping copy)')
            already.append(biblionumber)
        else:
            if DRY_RUN:
                print(f'  DRY-RUN  {img_file} -> {dest_name}  [{confidence}]')
            else:
                copy_as_jpeg(src_path, dest_path)
                print(f'  COPIED   {img_file} -> {dest_name}  [{confidence}]')
            copied.append(biblionumber)

        datalink_lines.append(f'{biblionumber},{dest_name}')

    # Write datalink.txt
    print(f'\n--- datalink.txt ({len(datalink_lines)} entries) ---')
    datalink_content = '\n'.join(datalink_lines)
    if DRY_RUN:
        print(datalink_content)
    else:
        with open(DATALINK_OUT, 'w') as f:
            f.write(datalink_content + '\n')
        print(f'Written to {DATALINK_OUT}')

    print(f'\nSummary:')
    print(f'  Copied : {len(copied)}')
    print(f'  Already existed: {len(already)}')
    print(f'  Missing source : {len(skipped)}')
    if skipped:
        print('  Missing files:', [img for _, img in skipped])


if __name__ == '__main__':
    main()

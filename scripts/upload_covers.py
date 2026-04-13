import os
import re
import sys
import time
import requests

# --- CONFIG ---
KOHA_BASE    = 'http://aluposto.ddns.net:8080'
STAFF_USER   = os.environ.get('KOHA_USER') or exit('ERROR: set KOHA_USER env variable')
STAFF_PASS   = os.environ.get('KOHA_PASS') or exit('ERROR: set KOHA_PASS env variable')
UPLOAD_DIR   = '/Users/anirbanghosh/Dishari/Library/Catalog/Catalog_AI/Image/UPLOAD'
DELAY        = 1.0   # seconds between uploads — be gentle on the server
TEST_ONLY    = int(sys.argv[1]) if len(sys.argv) > 1 else None
# --------------

LOGIN_URL  = f'{KOHA_BASE}/cgi-bin/koha/mainpage.pl'
UPLOAD_URL = f'{KOHA_BASE}/cgi-bin/koha/tools/upload-cover-image.pl'

session = requests.Session()
session.headers.update({'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'})

# Step 1: GET login page to capture hidden token
resp = session.get(LOGIN_URL)
resp.raise_for_status()
token_match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
token = token_match.group(1) if token_match else ''

# Step 2: Log in
login_data = {
    'userid':             STAFF_USER,
    'password':           STAFF_PASS,
    'op':                 'cud-login',
    'csrf_token':         token,
    'koha_login_context': 'intranet',
}
resp = session.post(LOGIN_URL, data=login_data)
resp.raise_for_status()

# Check login success — use the specific HTML comment, not just 'auth.tt'
if '<!-- TEMPLATE FILE: auth.tt' in resp.text:
    print('ERROR: Login failed — check credentials')
    exit(1)
print('Logged in OK')

# Step 2b: verify session works on a simple authenticated page
check = session.get(f'{KOHA_BASE}/cgi-bin/koha/mainpage.pl')
if '<!-- TEMPLATE FILE: auth.tt' in check.text:
    print('ERROR: Session not valid after login')
    exit(1)
print('Session verified OK')

# Step 3: Collect images — only files named <biblionumber>.jpeg
images = []
for fname in sorted(os.listdir(UPLOAD_DIR)):
    if not fname.endswith('.jpeg'):
        continue
    stem = fname.replace('.jpeg', '')
    if not stem.isdigit():
        continue
    images.append((int(stem), os.path.join(UPLOAD_DIR, fname)))

if TEST_ONLY is not None:
    images = [(b, p) for b, p in images if b == TEST_ONLY]
    print(f'TEST MODE: uploading biblionumber {TEST_ONLY} only\n')
else:
    print(f'Found {len(images)} images to upload\n')

def get_csrf_token(url):
    """Fetch a page and extract its CSRF token."""
    r = session.get(url)
    r.raise_for_status()
    # Try meta tag first (Koha 25.x), then hidden input
    m = re.search(r'<meta name="csrf-token"\s+content="([^"]+)"', r.text)
    if not m:
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
    return m.group(1) if m else ''

# Step 4: Upload each image via the individual record form
ok = []
failed = []

for biblionumber, fpath in images:
    # Fetch a fresh CSRF token before every upload POST
    csrf = get_csrf_token(UPLOAD_URL)

    with open(fpath, 'rb') as f:
        files = {'uploadfile': (f'{biblionumber}.jpeg', f, 'image/jpeg')}
        data  = {
            'biblionumber': str(biblionumber),
            'op':           'cud-Upload',
            'csrf_token':   csrf,
        }
        resp = session.post(UPLOAD_URL, files=files, data=data)

    # Detect session timeout or auth redirect
    if '<!-- TEMPLATE FILE: auth.tt' in resp.text:
        print(f'  SESSION EXPIRED — re-login needed')
        failed.append(biblionumber)
    elif resp.status_code == 200 and 'error' not in resp.text.lower()[:500]:
        print(f'  OK  {biblionumber}.jpeg -> biblionumber {biblionumber}')
        ok.append(biblionumber)
    else:
        print(f'  FAIL {biblionumber}.jpeg (HTTP {resp.status_code})')
        failed.append(biblionumber)

    time.sleep(DELAY)

print(f'\nDone: {len(ok)} uploaded, {len(failed)} failed')
if failed:
    print('Failed biblionumbers:', failed)

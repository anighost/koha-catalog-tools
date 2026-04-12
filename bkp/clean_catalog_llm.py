import sys, subprocess, os, json, re
import pandas as pd
from datetime import datetime

# --- AUTOMATIC INSTALLER ---
def install_requirements():
    try:
        import pymarc, openpyxl
    except ImportError:
        print("Installing required libraries (pymarc, openpyxl)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pymarc", "openpyxl"])
        print("Installation complete.\n")
    try:
        import rapidfuzz  # noqa: F401
    except ImportError:
        print("Installing rapidfuzz for fuzzy author matching...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "rapidfuzz"])
        print("Installation complete.\n")
    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("Installing anthropic SDK for LLM enrichment...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "anthropic"])
        print("Installation complete.\n")

install_requirements()
from pymarc import Record, Field, Subfield, MARCWriter

# Top-level rapidfuzz import (avoids repeated per-call import inside match_author_synonym)
try:
    from rapidfuzz import fuzz as _rf_fuzz, process as _rf_process
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:
    _RAPIDFUZZ_AVAILABLE = False

# ==========================================
# CONFIGURATION & POSITIONS (0-indexed)
# ==========================================
INPUT_FILE = '/Users/anirbanghosh/Dishari/Library/Catalog/Catalog_AI/Catalog_Input/catalog_to_be_cleaned_v1.csv'
STATE_FILE = 'koha_session_meta.json'

COL_ISBN       = 0;  COL_LANG      = 1;  COL_AUTHOR    = 2;  COL_TITLE    = 3
COL_SUBTITLE   = 4;  COL_EDITION   = 6;  COL_PLACE     = 7;  COL_PUBLISHER= 8
COL_YEAR       = 9;  COL_PAGES     = 10; COL_SERIES    = 12
COL_SUBJECTS   = [14, 15, 16, 17, 18]
COL_ITEM_TYPE  = 25; COL_COLLECTION = 27; COL_BRANCH_HOME = 28; COL_BRANCH_HOLD = 29
COL_DATE       = 31; COL_COST      = 33; COL_CALL_NO   = 34
COL_VARIANT_TITLE = 5
COL_BARCODE    = 35; COL_NOTE      = 13; COL_ITEM_NOTE = 36
COL_ADDED_AUTHORS = [19, 20, 21, 22, 23, 24]
COL_COPY2      = 37; COL_COPY3     = 38; COL_COPY4     = 39

# LLM configuration
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_BATCH_SIZE = 20  # titles per Claude API call

# Language code map — full names and ISO 639-1 two-letter codes → MARC three-letter codes
LANG_MAP = {
    'english': 'eng', 'en': 'eng', 'eng': 'eng',
    'bengali': 'ben', 'bangla': 'ben', 'bn': 'ben',
    'hindi': 'hin', 'hi': 'hin', 'hn': 'hin',
    'urdu': 'urd', 'ur': 'urd',
    'sanskrit': 'san', 'sa': 'san',
    'arabic': 'ara', 'ar': 'ara',
    'french': 'fre', 'fr': 'fre',
    'german': 'ger', 'de': 'ger',
}

# ==========================================
# FILE NAMING LOGIC
# ==========================================
timestamp  = datetime.now().strftime('%Y%m%d_%H%M')
raw_base   = os.path.splitext(os.path.basename(INPUT_FILE))[0]
base_name  = raw_base.replace('_to_be_cleaned', '')

OUTPUT_DIR  = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)
OUTPUT_MRC  = os.path.join(OUTPUT_DIR, f"cleaned_{base_name}_{timestamp}.mrc")
OUTPUT_XLSX = os.path.join(OUTPUT_DIR, f"cleaned_{base_name}_{timestamp}.xlsx")
ERROR_XLSX  = os.path.join(OUTPUT_DIR, f"error_{base_name}_{timestamp}.xlsx")

# ==========================================
# HELPERS
# ==========================================
def clean_text(val):
    if val is None or (isinstance(val, float) and pd.isna(val)) or str(val).strip() == "":
        return ""
    return re.sub(r'\s+', ' ', str(val)).strip()

def invert_author_name(name):
    """Convert 'Firstname Lastname' → 'Lastname, Firstname'. Skip if already inverted.
    Strips trailing 'et al.' before inverting."""
    name = clean_text(name)
    if not name or "," in name:
        return name, False
    # Multiple authors joined by "and" / "&" — don't invert, return as-is
    if re.search(r'\s+and\s+|\s*&\s*', name, flags=re.IGNORECASE):
        return name, False
    # Remove trailing "et al." variants before inverting
    name = re.sub(r'\s+et\.?\s+al\.?$', '', name, flags=re.IGNORECASE).strip()
    parts = name.split()
    if len(parts) > 1:
        return f"{parts[-1]}, {' '.join(parts[:-1])}", True
    return name, False

def match_author_synonym(raw_author, synonyms_author, fuzzy_threshold=88):
    """
    Match author against synonym dictionary.
    First: exact all-words match (fast path).
    Fallback: fuzzy token_sort_ratio match via rapidfuzz.
    Returns (standardized_name, matched_bool)
    """
    raw_lower = raw_author.lower()
    for standard, variations in synonyms_author.items():
        for v in variations:
            v_words = v.lower().split()
            if all(w in raw_lower for w in v_words):
                return standard, True

    # Fuzzy fallback — catches single-character typos that exact match misses
    if _RAPIDFUZZ_AVAILABLE:
        # Build candidate list: all variation strings mapped back to their canonical name
        # Keys are lowercased for consistent comparison with raw_lower
        candidates = {}
        for standard, variations in synonyms_author.items():
            for v in variations:
                candidates[v.lower()] = standard
            # Also allow matching directly against the canonical name
            candidates[standard.lower()] = standard

        result = _rf_process.extractOne(
            raw_lower, list(candidates.keys()), scorer=_rf_fuzz.token_sort_ratio
        )
        if result and result[1] >= fuzzy_threshold:
            return candidates[result[0]], True

    return raw_author, False

def clean_and_convert_isbn(text):
    """
    Validates ISBN-10 before converting to ISBN-13.
    Returns (cleaned_isbn, was_modified, is_invalid_isbn10)
    is_invalid_isbn10 is True only when an ISBN-10 with a bad check digit is detected.
    """
    if not text or (isinstance(text, float) and pd.isna(text)) or str(text).strip() == "":
        return "", False, False
    original = str(text)
    cleaned  = re.sub(r'(?i)isbn', '', original)
    cleaned  = re.sub(r'[^0-9X]', '', cleaned.upper())

    if len(cleaned) == 13:
        return cleaned, cleaned != re.sub(r'[^0-9X]', '', original.upper()), False

    if len(cleaned) == 10:
        # Check validity but convert regardless — an invalid check digit may be a typo,
        # and ISBN-13 form is always preferred in Koha.
        invalid = False
        try:
            check = sum((10 - i) * (10 if c == 'X' else int(c)) for i, c in enumerate(cleaned))
            if check % 11 != 0:
                invalid = True
        except ValueError:
            invalid = True

        # Convert to ISBN-13
        isbn12 = "978" + cleaned[:9]
        total  = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(isbn12))
        check_digit = (10 - (total % 10)) % 10
        return isbn12 + str(check_digit), True, invalid

    # Unknown format — return stripped version
    return cleaned, cleaned != re.sub(r'[^0-9X]', '', original.upper()), False

def validate_date(date_val):
    """Parse and normalize date. Returns (date_str, was_modified)."""
    today = datetime.now().strftime('%Y-%m-%d')
    if not date_val or (isinstance(date_val, float) and pd.isna(date_val)) or str(date_val).strip() == "":
        return today, True
    date_str = str(date_val).strip()
    for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d']:
        try:
            parsed = datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
            return parsed, (parsed != date_str)
        except ValueError:
            continue
    # Could not parse — default to today and flag it
    return today, True

def clean_pages(pages_val):
    """Normalize page count to '123 p.' format."""
    pages = str(pages_val).strip()
    digits_only = re.sub(r'[^\d]', '', pages)
    if digits_only:
        return f"{digits_only} p.", digits_only != pages.rstrip()
    return "", False

def clean_year(year_val):
    """Validate publication year."""
    year = re.sub(r'[^\d]', '', str(year_val).strip())[:4]
    if year.isdigit() and 1800 <= int(year) <= datetime.now().year:
        return year
    return ""

def is_primary_barcode(bc_str):
    """Returns True if barcode is a primary (1st copy) barcode starting with '10'."""
    return bc_str.isdigit() and bc_str.startswith('10')

def copy_number_from_barcode(bc_str):
    """Returns copy number (2, 3, or 4) if bc_str is a valid copy barcode, else None."""
    if bc_str.isdigit() and len(bc_str) >= 6:
        if bc_str.startswith('11'): return 2
        if bc_str.startswith('12'): return 3
        if bc_str.startswith('13'): return 4
    return None

# Words that mean "yes, this copy exists" when found in a copy trigger column.
# Anything else (dates like '01/25/25', notes, etc.) is ignored.
COPY_TRIGGER_WORDS = {'y', 'yes', '2nd copy', '3rd copy', '4th copy'}

def resolve_copy_barcode(val, primary_bc, prefix):
    """
    Given a copy column value, return the barcode string to use, or None.
    - Numeric value  → use directly (already a barcode)
    - Known trigger word (Y/Yes/2nd Copy) → generate from primary + prefix
    - Anything else (dates, notes) → None (not a copy)
    """
    if not val:
        return None
    if val.isdigit():
        return val
    if val.lower() in COPY_TRIGGER_WORDS:
        return (prefix + primary_bc[2:]) if len(primary_bc) >= 2 else None
    return None

def apply_keyword_normalization(text, synonyms_keywords):
    """Apply synonyms_keywords substitutions to any text field. Returns (normalized_text, list_of_changes)."""
    result = text
    changes = []
    for standard, pattern in synonyms_keywords.items():
        new, count = re.subn(rf'\b({pattern})\b', standard, result, flags=re.IGNORECASE)
        if count > 0:
            changes.append(standard)
            result = new
    return result, changes

def generate_phonetic_variants(title, synonyms_keywords):
    """
    Reverse-map canonical keywords back to their colloquial romanization forms.
    e.g. "Galpa Samagra" → "Golpo Somogro"
    Returns a list of variant strings (empty list if no keywords match).
    """
    variant = title
    changed = False
    for canonical, pattern in synonyms_keywords.items():
        # Use the first variant form (before any | in the pattern)
        first_variant = pattern.split('|')[0]
        new, n = re.subn(
            rf'\b{re.escape(canonical)}\b', first_variant, variant, flags=re.IGNORECASE
        )
        if n:
            variant = new
            changed = True
    return [variant] if changed else []

def batch_enrich_via_llm(title_author_pairs, api_key):
    """
    Call Claude to generate phonetic romanization variants and subject suggestions.
    title_author_pairs: list of (title_std, author_std) tuples
    Returns: dict keyed by title_std → {"variants": [...], "subjects": [...]}
    """
    import anthropic
    import json as _json

    items = "\n".join(
        f'{i+1}. Title: "{t}" | Author: "{a}"'
        for i, (t, a) in enumerate(title_author_pairs)
    )
    prompt = f"""You are helping a Bengali library index books for search.
All titles are Bengali books romanized in English. Bengali romanization varies widely
(e.g., the vowel 'অ' can be written 'a' or 'o'; 'শ' can be 'sh' or 's'; endings like '-a' often become '-o').

For each book, provide:
1. "variants": 3-5 alternative romanizations a Bengali reader might type when searching
   (focus on the most common spelling variations, not transliterations)
2. "subjects": 2-3 English subject headings describing genre, form, or theme

Books:
{items}

Respond ONLY with a JSON array, one object per book, in the same order:
[
  {{"title": "...", "variants": ["...", "..."], "subjects": ["...", "..."]}},
  ...
]"""

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    results = _json.loads(raw)

    cache = {}
    for i, item in enumerate(results):
        if i >= len(title_author_pairs):
            break
        title_key = title_author_pairs[i][0]
        cache[title_key] = {
            "variants": [v.strip() for v in item.get("variants", []) if v.strip()],
            "subjects": [s.strip() for s in item.get("subjects", []) if s.strip()],
        }
    return cache

def load_session_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "last_primary_barcode": 100000,
        "synonyms_publisher": {},
        "synonyms_author": {},
        "series_overrides": {},
        "synonyms_keywords": {},
        "synonyms_place": {},
        "synonyms_subject": {},
        "fuzzy_author_threshold": 88,
        "llm_cache": {}
    }

def save_session_state(state_dict):
    # Read the current file first so synonym dicts are never overwritten by a stale in-memory state.
    # Saves previous barcode as a backup before updating, so accidental runs can be reverted.
    # Also persists llm_cache when present.
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            on_disk = json.load(f)
    else:
        on_disk = {}
    on_disk["previous_last_primary_barcode"] = on_disk.get("last_primary_barcode", state_dict["last_primary_barcode"])
    on_disk["last_primary_barcode"] = state_dict["last_primary_barcode"]
    if "llm_cache" in state_dict:
        on_disk["llm_cache"] = state_dict["llm_cache"]
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(on_disk, f, indent=4, ensure_ascii=False)

# ==========================================
# MAIN EXECUTION
# ==========================================
state = load_session_state()

if not os.path.exists(INPUT_FILE):
    print(f"ERROR: {INPUT_FILE} not found.")
    sys.exit(1)

# Load input file
if INPUT_FILE.endswith('.csv'):
    try:
        df = pd.read_csv(INPUT_FILE, dtype=str, header=0, encoding='utf-8-sig').fillna('')
    except UnicodeDecodeError:
        print("WARNING: UTF-8 decoding failed — retrying with latin-1. Non-ASCII characters may be mangled.")
        df = pd.read_csv(INPUT_FILE, dtype=str, header=0, encoding='latin1').fillna('')
else:
    df = pd.read_excel(INPUT_FILE, dtype=str, header=0).fillna('')

today_date = datetime.now().strftime('%Y-%m-%d')

# --- BASELINE BARCODE SYNC ---
# FIX: Only track primary (10xxx) barcodes for last_primary_barcode
last_primary_bc = state.get("last_primary_barcode", 100000)
for val in df.iloc[:, COL_BARCODE]:
    s_val = str(val).strip()
    if is_primary_barcode(s_val):
        last_primary_bc = max(last_primary_bc, int(s_val))

# ── LLM ENRICHMENT (optional, requires ANTHROPIC_API_KEY) ─────────────────────
llm_cache = state.get("llm_cache", {})
if ANTHROPIC_API_KEY:
    # Pre-pass: collect unique (title_std, author) pairs not yet in the cache
    to_enrich = []
    seen_enrich = set()
    for _, row in df.iterrows():
        t_raw = clean_text(row.iloc[COL_TITLE])
        a_raw = clean_text(row.iloc[COL_AUTHOR])
        if not t_raw or not a_raw:
            continue
        # Apply keyword normalization so cache key matches what the main loop uses
        t_std, _ = apply_keyword_normalization(t_raw, state.get("synonyms_keywords", {}))
        if t_std and t_std not in llm_cache and t_std not in seen_enrich:
            to_enrich.append((t_std, a_raw))
            seen_enrich.add(t_std)

    if to_enrich:
        print(f"  Enriching {len(to_enrich)} titles via Claude API...")
        for i in range(0, len(to_enrich), LLM_BATCH_SIZE):
            batch = to_enrich[i:i + LLM_BATCH_SIZE]
            try:
                new_entries = batch_enrich_via_llm(batch, ANTHROPIC_API_KEY)
                llm_cache.update(new_entries)
                print(f"    Batch {i // LLM_BATCH_SIZE + 1}: enriched {len(new_entries)} titles")
            except Exception as e:
                print(f"    WARNING: LLM enrichment batch failed: {e}")
        state["llm_cache"] = llm_cache
        save_session_state(state)  # persist cache before main loop so it's not lost on crash
    else:
        print(f"  LLM cache up to date ({len(llm_cache)} titles already enriched).")
else:
    if not llm_cache:
        print("  (No ANTHROPIC_API_KEY set — skipping LLM enrichment. Set env var to enable.)")
    else:
        print(f"  (Using cached LLM enrichment for {len(llm_cache)} titles — no API key set.)")

clean_xlsx_rows, error_rows, marc_recs = [], [], []
total_rows = len(df)

for row_num, (index, row) in enumerate(df.iterrows()):
    # Skip blank rows
    if not "".join([str(v) for v in row]).strip():
        continue

    if row_num % 100 == 0:
        print(f"  Processing row {row_num + 2}/{total_rows + 1}...", end='\r')

    row_logs = []

    # ── 1. AUTHOR ──────────────────────────────────────────────────────────────
    raw_author_full = clean_text(row.iloc[COL_AUTHOR])

    # Split multiple authors joined by "and" / "&" — first becomes 100$a, rest go to 700$a
    author_parts = re.split(r'\s+and\s+|\s*&\s*', raw_author_full, flags=re.IGNORECASE)
    raw_author      = author_parts[0].strip()
    extra_authors_raw = [a.strip() for a in author_parts[1:] if a.strip()]

    # Synonym match BEFORE inversion (raw name is easier to match)
    author_matched, author_synonymed = match_author_synonym(
        raw_author, state.get("synonyms_author", {}),
        fuzzy_threshold=state.get("fuzzy_author_threshold", 88)
    )
    if author_synonymed:
        row_logs.append(f"Normalized author via dictionary: '{raw_author}' → '{author_matched}'")

    # Now invert
    author_std, inverted = invert_author_name(author_matched)
    if inverted:
        row_logs.append("Inverted author name (Surname, Firstname)")

    # ── 2. PUBLISHER ───────────────────────────────────────────────────────────
    raw_pub = clean_text(row.iloc[COL_PUBLISHER])
    pub_std = raw_pub
    for standard, variations in state.get("synonyms_publisher", {}).items():
        if any(re.search(rf'\b{re.escape(v)}\b', raw_pub, flags=re.IGNORECASE) for v in variations):
            pub_std = standard
            row_logs.append(f"Standardized publisher: '{raw_pub}' → '{pub_std}'")
            break

    # ── 3. TITLE (keyword normalization) ───────────────────────────────────────
    title_raw = clean_text(row.iloc[COL_TITLE])
    title_std = title_raw
    for standard, pattern in state.get("synonyms_keywords", {}).items():
        title_new, count = re.subn(
            rf'\b({pattern})\b', standard, title_std, flags=re.IGNORECASE
        )
        if count > 0:
            row_logs.append(f"Keyword normalized in title: → '{standard}'")
            title_std = title_new

    # ── 4. VALIDATION ──────────────────────────────────────────────────────────
    if not title_std or not author_std:
        err_row = row.copy()
        err_row['Error_Reason'] = "Missing mandatory Title or Author"
        err_row['Source_Row']   = index + 2  # +2: 1-indexed + header row
        error_rows.append(err_row)
        continue

    # ── 5. FIELD STANDARDIZATION ───────────────────────────────────────────────
    isbn_clean, isbn_mod, isbn_invalid = clean_and_convert_isbn(row.iloc[COL_ISBN])
    if isbn_invalid:
        row_logs.append("WARNING: invalid ISBN-10 check digit — stored as-is")
    elif isbn_mod:
        row_logs.append("Cleaned/converted ISBN to ISBN-13")

    date_clean, date_mod = validate_date(row.iloc[COL_DATE])
    if date_mod:
        row_logs.append(f"Standardized date → {date_clean}")

    pages_clean, pages_mod = clean_pages(row.iloc[COL_PAGES])
    if pages_mod:
        row_logs.append("Standardized page notation")

    year_clean = clean_year(row.iloc[COL_YEAR])

    subtitle_raw = clean_text(row.iloc[COL_SUBTITLE])
    subtitle, sub_kw_changes = apply_keyword_normalization(
        subtitle_raw, state.get("synonyms_keywords", {})
    )
    for kw in sub_kw_changes:
        row_logs.append(f"Keyword normalized in subtitle: → '{kw}'")
    edition  = clean_text(row.iloc[COL_EDITION])
    place_raw = clean_text(row.iloc[COL_PLACE])
    place = next(
        (std for std, variants in state.get("synonyms_place", {}).items()
         if place_raw.lower() in [v.lower() for v in variants]),
        place_raw
    )
    cost     = clean_text(row.iloc[COL_COST])
    note_text  = clean_text(row.iloc[COL_NOTE])       # 500$a general note
    item_note  = clean_text(row.iloc[COL_ITEM_NOTE])  # 952$z item note (may hold flags)

    # Call number
    call_no = clean_text(row.iloc[COL_CALL_NO])
    if not call_no:
        call_no = "891"
        row_logs.append("Defaulted call number to 891")

    # Item type — check both 500$a (col 13) and 952$z (col 36) for "author signed"
    itype_input = clean_text(row.iloc[COL_ITEM_TYPE])
    if "author signed" in note_text.lower() or "author signed" in item_note.lower():
        final_itype = "ASB"
        row_logs.append("Author signed copy detected → item type set to ASB")
    else:
        final_itype = itype_input if itype_input else "BK"

    # Collection code (952$8 shelving location)
    collection_code = clean_text(row.iloc[COL_COLLECTION])

    # Language
    lang_raw  = clean_text(row.iloc[COL_LANG]).lower()
    if lang_raw and lang_raw not in LANG_MAP:
        row_logs.append(f"WARNING: unrecognized language '{clean_text(row.iloc[COL_LANG])}' — defaulted to Bengali (ben)")
    lang_code = LANG_MAP.get(lang_raw, 'ben')  # default Bengali

    # Branch codes
    home_branch = clean_text(row.iloc[COL_BRANCH_HOME]) or 'DFL'
    hold_branch = clean_text(row.iloc[COL_BRANCH_HOLD]) or 'DFL'

    # Series + override
    # FIX: series_overrides key is "SeriesTitle|AuthorName" — match composite key
    series_raw_input = clean_text(row.iloc[COL_SERIES])
    series_raw, series_kw_changes = apply_keyword_normalization(
        series_raw_input, state.get("synonyms_keywords", {})
    )
    for kw in series_kw_changes:
        row_logs.append(f"Keyword normalized in series: → '{kw}'")
    series_key = f"{series_raw}|{author_matched}"  # use pre-inversion author
    series_val = state.get("series_overrides", {}).get(series_key, series_raw)
    if series_val != series_raw and series_raw:
        row_logs.append(f"Series override: '{series_raw}' → '{series_val}'")

    # ── 6. BARCODE GENERATION ──────────────────────────────────────────────────
    # FIX: Proper primary barcode logic — only increment from 10xxx values
    input_bc = clean_text(row.iloc[COL_BARCODE])
    if is_primary_barcode(input_bc):
        current_primary_bc = input_bc
        last_primary_bc    = max(last_primary_bc, int(input_bc))
    else:
        # Empty or non-primary barcode in input → generate new one
        last_primary_bc   += 1
        current_primary_bc = str(last_primary_bc)
        row_logs.append(f"Generated new primary barcode: {current_primary_bc}")

    # FIX: Copy barcodes replace first 2 digits with 11/12/13
    # e.g. primary 100045 → copy2=110045, copy3=120045, copy4=130045
    copy_prefix_map = {COL_COPY2: '11', COL_COPY3: '12', COL_COPY4: '13'}

    # ── 7. BUILD MARC RECORD ───────────────────────────────────────────────────
    rec = Record()
    rec.leader = '00000nam a22000007a 4500'

    # 020 — ISBN
    if isbn_clean:
        rec.add_field(Field(
            tag='020', indicators=[' ', ' '],
            subfields=[Subfield(code='a', value=isbn_clean)]
        ))

    # 041 — Language (from column, not hardcoded)
    rec.add_field(Field(
        tag='041', indicators=['0', ' '],
        subfields=[Subfield(code='a', value=lang_code)]
    ))

    # 100 — Main entry (author)
    rec.add_field(Field(
        tag='100', indicators=['1', ' '],
        subfields=[Subfield(code='a', value=author_std)]
    ))

    # 245 — Title + subtitle with ISBD punctuation
    subfields_245 = [Subfield(code='a', value=title_std + (' :' if subtitle else '.'))]
    if subtitle:
        subfields_245.append(Subfield(code='b', value=subtitle + '.'))
    rec.add_field(Field(tag='245', indicators=['1', '0'], subfields=subfields_245))

    # 246 — Variant/alternate titles (comma-separated in col 5)
    variant_raw = clean_text(row.iloc[COL_VARIANT_TITLE])
    if variant_raw:
        for vt in [v.strip() for v in variant_raw.split(',') if v.strip()]:
            rec.add_field(Field(
                tag='246', indicators=['1', ' '],
                subfields=[Subfield(code='a', value=vt)]
            ))

    # 246 — Auto-generated phonetic variants (reverse-map canonical keywords → colloquial forms)
    # e.g. "Galpa Samagra" (245$a) → auto-adds "Golpo Somogro" as 246$a so both spellings are indexed
    for phonetic_variant in generate_phonetic_variants(title_std, state.get("synonyms_keywords", {})):
        rec.add_field(Field(
            tag='246', indicators=['1', ' '],
            subfields=[Subfield(code='a', value=phonetic_variant)]
        ))
        row_logs.append(f"Auto-generated phonetic 246$a: '{phonetic_variant}'")

    # 246 — LLM-generated phonetic romanization variants
    cached_entry = llm_cache.get(title_std, {})
    existing_246 = {f['a'].casefold() for f in rec.get_fields('246') if f['a']}
    for variant in cached_entry.get("variants", []):
        if variant and variant.casefold() not in existing_246:
            rec.add_field(Field(
                tag='246', indicators=['1', ' '],
                subfields=[Subfield(code='a', value=variant)]
            ))
            existing_246.add(variant.casefold())
            row_logs.append(f"LLM phonetic variant 246$a: '{variant}'")

    # 250 — Edition
    if edition:
        rec.add_field(Field(
            tag='250', indicators=[' ', ' '],
            subfields=[Subfield(code='a', value=edition)]
        ))

    # 260 — Publication info (place : publisher, year)
    subfields_260 = []
    if place:
        subfields_260.append(Subfield(code='a', value=place + ' :'))
    if pub_std:
        subfields_260.append(Subfield(code='b', value=pub_std + ','))
    if year_clean:
        subfields_260.append(Subfield(code='c', value=year_clean + '.'))
    if subfields_260:
        rec.add_field(Field(tag='260', indicators=[' ', ' '], subfields=subfields_260))

    # 300 — Physical description (pages)
    if pages_clean:
        rec.add_field(Field(
            tag='300', indicators=[' ', ' '],
            subfields=[Subfield(code='a', value=pages_clean)]
        ))

    # 440/830 — Series
    if series_val:
        rec.add_field(Field(
            tag='830', indicators=[' ', '0'],
            subfields=[Subfield(code='a', value=series_val)]
        ))

    # 500 — Note
    if note_text:
        rec.add_field(Field(
            tag='500', indicators=[' ', ' '],
            subfields=[Subfield(code='a', value=note_text)]
        ))

    # 650 — Subject entries (all 5 subject columns)
    seen_650 = set()
    for col_idx in COL_SUBJECTS:
        subject_raw = clean_text(row.iloc[col_idx])
        if not subject_raw:
            continue
        # Normalize subject via synonyms_subject (word-boundary match → canonical heading)
        subject = subject_raw
        for std_subj, subj_variants in state.get("synonyms_subject", {}).items():
            if any(re.search(rf'\b{re.escape(v)}\b', subject_raw, flags=re.IGNORECASE) for v in subj_variants):
                subject = std_subj
                row_logs.append(f"Normalized subject: '{subject_raw}' → '{subject}'")
                break
        if subject.casefold() in seen_650:
            row_logs.append(f"Skipped duplicate subject heading: '{subject}'")
            continue
        seen_650.add(subject.casefold())
        rec.add_field(Field(
            tag='650', indicators=[' ', '4'],
            subfields=[Subfield(code='a', value=subject)]
        ))

    # 650 — LLM-suggested subject headings
    for subj in cached_entry.get("subjects", []):
        if subj and subj.casefold() not in seen_650:
            seen_650.add(subj.casefold())
            rec.add_field(Field(
                tag='650', indicators=[' ', '4'],
                subfields=[Subfield(code='a', value=subj)]
            ))
            row_logs.append(f"LLM-suggested subject: '{subj}'")

    # 700 — Added entries: co-authors split from 100$a field, then cols 19-24
    seen_700 = {author_std.casefold()}

    # Co-authors extracted from the main author field ("X and Y" → Y goes here)
    fuzzy_thr = state.get("fuzzy_author_threshold", 88)
    for extra_raw in extra_authors_raw:
        extra_matched, _ = match_author_synonym(extra_raw, state.get("synonyms_author", {}), fuzzy_thr)
        extra_std, _ = invert_author_name(extra_matched)
        if extra_std.casefold() not in seen_700:
            rec.add_field(Field(
                tag='700', indicators=['1', ' '],
                subfields=[Subfield(code='a', value=extra_std)]
            ))
            seen_700.add(extra_std.casefold())

    # Added authors from cols 19-24
    for col_idx in COL_ADDED_AUTHORS:
        added_raw = clean_text(row.iloc[col_idx])
        if not added_raw:
            continue
        added_matched, _ = match_author_synonym(added_raw, state.get("synonyms_author", {}), fuzzy_thr)
        added_std, _ = invert_author_name(added_matched)
        if added_std.casefold() not in seen_700:
            rec.add_field(Field(
                tag='700', indicators=['1', ' '],
                subfields=[Subfield(code='a', value=added_std)]
            ))
            seen_700.add(added_std.casefold())

    # 942 — Koha item type
    rec.add_field(Field(
        tag='942', indicators=[' ', ' '],
        subfields=[Subfield(code='c', value=final_itype)]
    ))

    # 952 — Item (holdings) builder
    def add_item_to_marc(barcode_val):
        subfields_952 = [
            Subfield(code='a', value=home_branch),
            Subfield(code='b', value=hold_branch),
            Subfield(code='p', value=barcode_val),
            Subfield(code='o', value=call_no),
            Subfield(code='d', value=date_clean),
            Subfield(code='y', value=final_itype),
        ]
        if collection_code:
            subfields_952.append(Subfield(code='8', value=collection_code))
        if cost:
            subfields_952.append(Subfield(code='g', value=cost))
        rec.add_field(Field(tag='952', indicators=[' ', ' '], subfields=subfields_952))

    # Primary item
    bib_barcodes = [current_primary_bc]
    add_item_to_marc(current_primary_bc)

    # Copy items (2nd/3rd/4th)
    # Priority: 953$8/954$8/955$8 (cols 37-39) take precedence over 952$z (col 36).
    # Values in copy cols can be actual barcodes (110xxx) or flags (Y/Yes) — both trigger a copy.
    # If all copy cols are empty, fall back to checking 952$z for a copy-style barcode (11/12/13 prefix).
    any_copy_col = any(clean_text(row.iloc[c]) != '' for c in [COL_COPY2, COL_COPY3, COL_COPY4])

    if any_copy_col:
        for col_idx, prefix in copy_prefix_map.items():
            copy_bc = resolve_copy_barcode(clean_text(row.iloc[col_idx]), current_primary_bc, prefix)
            if copy_bc:
                bib_barcodes.append(copy_bc)
                add_item_to_marc(copy_bc)
                row_logs.append(f"Added copy barcode: {copy_bc}")
    else:
        # Fall back: check 952$z for a copy indicator placed there instead of 953$8.
        # Accepts numeric copy barcodes (110xxx) or trigger words ("2nd Copy").
        item_note_val = clean_text(row.iloc[COL_ITEM_NOTE])
        copy_num = copy_number_from_barcode(item_note_val)
        if copy_num:
            bib_barcodes.append(item_note_val)
            add_item_to_marc(item_note_val)
            row_logs.append(f"Detected copy {copy_num} barcode from 952$z: {item_note_val}")
        elif item_note_val.lower() in ('2nd copy', '3rd copy', '4th copy'):
            prefix = {'2nd copy': '11', '3rd copy': '12', '4th copy': '13'}[item_note_val.lower()]
            copy_bc = (prefix + current_primary_bc[2:]) if len(current_primary_bc) >= 2 else None
            if copy_bc:
                bib_barcodes.append(copy_bc)
                add_item_to_marc(copy_bc)
                row_logs.append(f"Detected copy from '952$z={item_note_val}', barcode: {copy_bc}")

    marc_recs.append(rec)

    # ── 8. BUILD EXCEL AUDIT ROWS ──────────────────────────────────────────────
    for bc in bib_barcodes:
        xl_row = row.copy()
        xl_row.iloc[COL_BARCODE]   = bc
        xl_row.iloc[COL_AUTHOR]    = author_std
        xl_row.iloc[COL_TITLE]     = title_std
        xl_row.iloc[COL_PUBLISHER] = pub_std
        xl_row.iloc[COL_ISBN]      = isbn_clean
        xl_row.iloc[COL_DATE]      = date_clean
        xl_row.iloc[COL_CALL_NO]   = call_no
        xl_row.iloc[COL_ITEM_TYPE] = final_itype
        # Write processed (synonym-matched + inverted) values back to 700$a cols
        for col_idx in COL_ADDED_AUTHORS:
            raw_val = clean_text(row.iloc[col_idx])
            if raw_val:
                matched, _ = match_author_synonym(raw_val, state.get("synonyms_author", {}), fuzzy_thr)
                std, _     = invert_author_name(matched)
                xl_row.iloc[col_idx] = std
        xl_row['Modification_Log'] = "; ".join(row_logs) if row_logs else "No changes"
        xl_row['Source_Row']       = index + 2
        clean_xlsx_rows.append(xl_row)

# ── SAVE OUTPUTS ───────────────────────────────────────────────────────────────
print(f"\nWriting outputs...")

# FIX: Save only the primary barcode counter (never a copy barcode value)
state["last_primary_barcode"] = last_primary_bc
save_session_state(state)

# MARC file
with open(OUTPUT_MRC, 'wb') as f:
    writer = MARCWriter(f)
    for r in marc_recs:
        writer.write(r)
    writer.close()

# Clean Excel (drop copy trigger columns from audit sheet)
final_df = pd.DataFrame(clean_xlsx_rows)
copy_col_names = [final_df.columns[c] for c in [COL_COPY2, COL_COPY3, COL_COPY4]
                  if c < len(final_df.columns)]
final_df.drop(columns=copy_col_names, inplace=True)
final_df.to_excel(OUTPUT_XLSX, index=False)

# Error Excel
if error_rows:
    pd.DataFrame(error_rows).to_excel(ERROR_XLSX, index=False)
    print(f"  {len(error_rows)} rejected rows → {ERROR_XLSX}")

print(f"\nFINISHED!")
print(f"  Records processed : {len(marc_recs)}")
print(f"  MARC file         : {OUTPUT_MRC}")
print(f"  Audit Excel       : {OUTPUT_XLSX}")
print(f"  Last barcode used : {last_primary_bc}")
if error_rows:
    print(f"  Error file        : {ERROR_XLSX}")
llm_cached_count = len(llm_cache)
if llm_cached_count:
    print(f"  LLM-enriched titles in cache: {llm_cached_count}")

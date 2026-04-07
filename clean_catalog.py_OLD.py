import sys, subprocess, os, json, re
import pandas as pd
import numpy as np
from datetime import datetime

# --- AUTOMATIC INSTALLER ---
def install_requirements():
    try:
        import pymarc, openpyxl
    except ImportError:
        print("Installing required libraries (pymarc, openpyxl)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pymarc", "openpyxl"])
        print("Installation complete.\n")

install_requirements()
from pymarc import Record, Field, Subfield, MARCWriter

# ==========================================
# CONFIGURATION & POSITIONS (0-indexed)
# ==========================================
INPUT_FILE = 'catalog_to_be_cleaned_v1.csv'
STATE_FILE = 'koha_session_meta.json'

# Positional Mapping
COL_ISBN = 0
COL_LANG = 1
COL_AUTHOR = 2
COL_TITLE = 3
COL_SUBTITLE = 4
COL_OTHER_TITLE = 5
COL_EDITION = 6
COL_PLACE = 7
COL_PUBLISHER = 8
COL_YEAR = 9
COL_PAGES = 10
COL_PHYSICAL = 11
COL_SERIES = 12
COL_NOTES_AREA = 13
COL_SUBJECTS = [14, 15, 16, 17, 18]        # 650$a
COL_ADDED_AUTHORS = [19, 20, 21, 22, 23, 24] # 700$a
COL_ITEM_TYPE = 25
COL_STATUS = 26
COL_COLLECTION = 27
COL_BRANCH_HOME = 28
COL_BRANCH_HOLD = 29
COL_SHELVING = 30
COL_DATE_ACQ = 31
COL_SOURCE = 32
COL_COST = 33
COL_CALL_NO = 34
COL_BARCODE = 35
COL_PUBLIC_NOTE = 36
COL_COPY2 = 37
COL_COPY3 = 38
COL_COPY4 = 39

# ==========================================
# REFINED HELPERS
# ==========================================
def clean_text(text):
    """Deep cleaning of whitespace and non-breaking spaces."""
    if text is None or pd.isna(text) or str(text).strip() == "":
        return ""
    # Remove non-breaking spaces (\xa0) and multiple spaces
    text = str(text).replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', text).strip()

def invert_name(name):
    """
    Converts 'Forename Middle Surname' to 'Surname, Forename Middle'
    Handles names with more than 2 parts correctly.
    """
    name = clean_text(name)
    if not name or "," in name:
        return name, False
    
    parts = name.split(" ")
    if len(parts) > 1:
        surname = parts[-1]  # Last word is surname
        forenames = " ".join(parts[:-1]) # Everything else is forename/middle
        return f"{surname}, {forenames}", True
    return name, False

def clean_isbn_logic(text):
    if not text: return "", False
    original = str(text)
    # Remove the word 'ISBN' if it exists
    clean = re.sub(r'(?i)isbn', '', original)
    # Remove everything except digits and X
    clean = re.sub(r'[^0-9X]', '', clean.upper())
    
    if len(clean) == 10:
        # Mathematical conversion to ISBN-13
        isbn9 = "978" + clean[:9]
        sum_v = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(isbn9))
        check = (10 - (sum_v % 10)) % 10
        return isbn9 + str(check), True
    return clean, (clean != original.replace('-', ''))

def validate_date(date_val):
    today = datetime.now().strftime('%Y-%m-%d')
    if not date_val or pd.isna(date_val) or str(date_val).strip() == "":
        return today, True
    date_str = str(date_val).strip()
    formats = ['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d']
    for fmt in formats:
        try: 
            parsed = datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
            return parsed, (parsed != date_str)
        except: continue
    return today, True

def handle_series(title, author, existing_series, state):
    title = clean_text(title)
    if existing_series and existing_series.strip():
        return existing_series, False
    
    # Check overrides
    lookup_key = f"{title}|{author}"
    if lookup_key in state.get("series_overrides", {}):
        return state["series_overrides"][lookup_key], True
    
    # Auto-detect Series from Title (e.g. 'Book Name - Vol 1')
    if "rachanabali" in title.lower() or "samagra" in title.lower():
        return f"{author} Rachanabali", True
    
    patterns = [r'(.*)\s+Vol\.?\s?\d+', r'(.*)\s+Part\.?\s?\d+', r'(.*)\s+Volume\s?\d+']
    for p in patterns:
        match = re.search(p, title, re.IGNORECASE)
        if match: return clean_text(match.group(1)), True
        
    return "", False

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    return {"last_primary_barcode": 100000, "synonyms_publisher": {}, "synonyms_author": {}, "synonyms_keywords": {}, "series_overrides": {}}

# ==========================================
# MAIN EXECUTION
# ==========================================
state = load_state()

if not os.path.exists(INPUT_FILE):
    print(f"❌ ERROR: {INPUT_FILE} not found.")
else:
    # Load File
    if INPUT_FILE.endswith('.csv'):
        try: df = pd.read_csv(INPUT_FILE, dtype=str, header=0, encoding='utf-8-sig').fillna('')
        except: df = pd.read_csv(INPUT_FILE, dtype=str, header=0, encoding='latin1').fillna('')
    else:
        df = pd.read_excel(INPUT_FILE, dtype=str, header=0).fillna('')

    original_headers = df.columns.tolist()
    last_bc = state.get("last_primary_barcode", 100000)
    
    # Baseline Barcode Sync
    for val in df.iloc[:, COL_BARCODE]:
        s_val = str(val).strip()
        if s_val.isdigit() and s_val.startswith('10'):
            last_bc = max(last_bc, int(s_val))

    clean_xlsx_rows, error_rows, marc_recs = [], [], []

    for index, row in df.iterrows():
        if not "".join([str(v) for v in row]).strip(): continue
        logs = []

        # 1. AUTHOR NORMALIZATION
        raw_main_author = str(row.iloc[COL_AUTHOR]).strip()
        author_std, inv = invert_name(raw_main_author)
        # Check synonyms
        for standard, variations in state.get("synonyms_author", {}).items():
            if any(v in author_std.lower() for v in variations):
                author_std = standard; logs.append("Normalized Author Name")
                break
        if inv: logs.append("Inverted Author Name")

        # 2. TITLE NORMALIZATION
        title_std = clean_text(row.iloc[COL_TITLE])
        subtitle_std = clean_text(row.iloc[COL_SUBTITLE])
        for std_kw, pat in state.get("synonyms_keywords", {}).items():
            title_std, count1 = re.subn(rf'\b({pat})\b', std_kw, title_std, flags=re.IGNORECASE)
            subtitle_std, count2 = re.subn(rf'\b({pat})\b', std_kw, subtitle_std, flags=re.IGNORECASE)
            if count1 > 0 or count2 > 0: logs.append(f"Standardized Keyword: {std_kw}")

        # 3. VALIDATION
        if not title_std or not author_std:
            err_row = row.copy(); err_row['Error_Reason'] = "Missing Mandatory Title or Author"
            error_rows.append(err_row); continue

        # 4. FIELD STANDARDIZATION
        isbn_clean, isbn_mod = clean_isbn_logic(row.iloc[COL_ISBN])
        if isbn_mod: logs.append("Cleaned/Converted ISBN")
        
        date_clean, date_mod = validate_date(row.iloc[COL_DATE_ACQ])
        if date_mod: logs.append("Standardized Date format")
        
        call_no = clean_text(row.iloc[COL_CALL_NO])
        if not call_no:
            call_no = "891"; logs.append("Defaulted Call No to 891")

        pages = str(row.iloc[COL_PAGES]).strip()
        if pages.isdigit(): pages = f"{pages} p."; logs.append("Standardized Pages")

        publisher = clean_text(row.iloc[COL_PUBLISHER])
        for standard, variations in state.get("synonyms_publisher", {}).items():
            if any(v in publisher.lower() for v in variations):
                publisher = standard; logs.append("Standardized Publisher"); break

        series_name, series_mod = handle_series(title_std, author_std, row.iloc[COL_SERIES], state)
        if series_mod: logs.append("Auto-populated Series")

        # Item Type Rule
        note_text = str(row.iloc[COL_PUBLIC_NOTE]).lower()
        itype = "ASB" if "author signed" in note_text else (clean_text(row.iloc[COL_ITEM_TYPE]) or "BK")

        # 5. BARCODE logic
        bc_input = str(row.iloc[COL_BARCODE]).strip()
        if not (bc_input.isdigit() and bc_input.startswith('10')):
            last_bc += 1
            current_primary_bc = str(last_bc)
            logs.append(f"Generated Primary Barcode: {current_primary_bc}")
        else:
            current_primary_bc = bc_input; last_bc = max(last_bc, int(bc_input))

        # 6. MARC CREATION
        rec = Record()
        rec.leader = '00000nam a22000007a 4500'
        if isbn_clean: rec.add_field(Field(tag='020', indicators=[' ',' '], subfields=[Subfield(code='a', value=isbn_clean)]))
        rec.add_field(Field(tag='041', indicators=['0',' '], subfields=[Subfield(code='a', value='ben')]))
        rec.add_field(Field(tag='100', indicators=['1',' '], subfields=[Subfield(code='a', value=author_std)]))
        
        t_subs = [Subfield(code='a', value=title_std)]
        if subtitle_std: t_subs.append(Subfield(code='b', value=subtitle_std))
        rec.add_field(Field(tag='245', indicators=['1','0'], subfields=t_subs))
        
        if clean_text(row.iloc[COL_EDITION]):
            rec.add_field(Field(tag='250', indicators=[' ',' '], subfields=[Subfield(code='a', value=clean_text(row.iloc[COL_EDITION]))]))
        
        rec.add_field(Field(tag='260', indicators=[' ',' '], subfields=[
            Subfield(code='a', value=clean_text(row.iloc[COL_PLACE])), 
            Subfield(code='b', value=publisher), 
            Subfield(code='c', value=clean_text(row.iloc[COL_YEAR]))
        ]))
        
        if pages: rec.add_field(Field(tag='300', indicators=[' ',' '], subfields=[Subfield(code='a', value=pages)]))
        if series_name: rec.add_field(Field(tag='490', indicators=['0',' '], subfields=[Subfield(code='a', value=series_name)]))
        
        # Add Subjects (650)
        for s_idx in COL_SUBJECTS:
            s_val = clean_text(row.iloc[s_idx])
            if s_val: rec.add_field(Field(tag='650', indicators=[' ','0'], subfields=[Subfield(code='a', value=s_val)]))

        # --- ADDED AUTHORS (700$a) ---
        processed_700s = []
        for col_idx in COL_ADDED_AUTHORS:
            raw_added = str(row.iloc[col_idx]).strip()
            if raw_added:
                added_std, _ = invert_name(raw_added)
                # Ensure it is NOT the same as Main Author and NOT a duplicate in 700s
                if added_std.lower() != author_std.lower() and added_std not in processed_700s:
                    rec.add_field(Field(tag='700', indicators=['1',' '], subfields=[Subfield(code='a', value=added_std)]))
                    processed_700s.append(added_std)
        
        rec.add_field(Field(tag='942', indicators=[' ',' '], subfields=[Subfield(code='c', value=itype)]))

        # --- ITEMS (952) ---
        bib_barcodes = [current_primary_bc]
        def add_952(b_val):
            rec.add_field(Field(tag='952', indicators=[' ',' '], subfields=[
                Subfield(code='a', 'DFL'), Subfield(code='b', 'DFL'), 
                Subfield(code='p', b_val), Subfield(code='o', call_no), 
                Subfield(code='y', itype), Subfield(code='d', date_clean)
            ]))

        add_952(current_primary_bc)
        # Check copies logic
        for col_idx, digit in zip([COL_COPY2, COL_COPY3, COL_COPY4], ['1', '2', '3']):
            if clean_text(row.iloc[col_idx]):
                if len(current_primary_bc) >= 2:
                    c_bc = current_primary_bc[0] + digit + current_primary_bc[2:]
                    bib_barcodes.append(c_bc); add_952(c_bc)

        marc_recs.append(rec)
        
        # --- XLSX OUTPUT BUILDING ---
        for b in bib_barcodes:
            xl_row = row.copy()
            xl_row.iloc[COL_BARCODE], xl_row.iloc[COL_AUTHOR], xl_row.iloc[COL_TITLE] = b, author_std, title_std
            xl_row.iloc[COL_SUBTITLE], xl_row.iloc[COL_PUBLISHER], xl_row.iloc[COL_ISBN] = subtitle_std, publisher, isbn_clean
            xl_row.iloc[COL_DATE_ACQ], xl_row.iloc[COL_ITEM_TYPE], xl_row.iloc[COL_CALL_NO] = date_clean, itype, call_no
            xl_row.iloc[COL_SERIES] = series_name
            # Modification Log for XLSX Audit
            xl_row['Modification_Log'] = "; ".join(logs)
            clean_xlsx_rows.append(xl_row)

    # --- SAVE ---
    state["last_primary_barcode"] = last_bc
    with open(STATE_FILE, 'w', encoding='utf-8') as f: json.dump(state, f, indent=4)
    
    with open(OUTPUT_MRC, 'wb') as f:
        writer = MARCWriter(f)
        for r in marc_recs: writer.write(r)
        writer.close()

    final_df = pd.DataFrame(clean_xlsx_rows)
    final_df.drop(final_df.columns[[COL_COPY2, COL_COPY3, COL_COPY4]], axis=1, inplace=True)
    final_df.to_excel(OUTPUT_XLSX, index=False)
    
    if error_rows:
        pd.DataFrame(error_rows).to_excel(ERROR_XLSX, index=False)
        print(f"⚠️ {len(error_rows)} records missing Title/Author saved to {ERROR_XLSX}")

    print(f"✅ SUCCESS! Total records staged: {len(marc_recs)}")
    print(f"MARC file: {OUTPUT_MRC}")
    print(f"Excel file: {OUTPUT_XLSX}")
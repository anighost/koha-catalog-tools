import sys, subprocess, os, json, re
import pandas as pd
import numpy as np
from datetime import datetime

# --- AUTOMATIC INSTALLER ---
def install_requirements():
    try:
        import pymarc, openpyxl
    except ImportError:
        print("Installing required libraries...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pymarc", "openpyxl"])

install_requirements()
from pymarc import Record, Field, Subfield, MARCWriter

# ==========================================
# CONFIGURATION & MEMORY (JSON)
# ==========================================
INPUT_FILE = 'catalog_to_be_cleaned_v1.csv'
STATE_FILE = 'koha_session_meta.json'

# Column Positions (0-indexed)
COL_ISBN=0; COL_LANG=1; COL_AUTHOR=2; COL_TITLE=3; COL_SUBTITLE=4; COL_EDITION=6
COL_PLACE=7; COL_PUBLISHER=8; COL_YEAR=9; COL_PAGES=10; COL_SUBJECTS=[14,15,16,17,18]
COL_ITEM_TYPE=25; COL_BRANCH_HOME=28; COL_BRANCH_HOLD=29; COL_DATE=31; COL_COST=33
COL_CALL_NO=34; COL_BARCODE=35; COL_NOTE=36; COL_COPY2=37; COL_COPY3=38; COL_COPY4=39

def load_session_state():
    """Memory: Loads last barcode and all phonetic synonym dictionaries."""
    default_state = {
        "last_primary_barcode": 100000,
        "synonyms_publisher": {
            "Ananda Publishers": ["ananda", "anondo"],
            "Mitra & Ghosh Publishers Pvt. Ltd.": ["mitra", "ghosh"],
            "Dey's Publishing": ["dey"],
            "Sahitya Sansad": ["sansad", "samsad", "sahitya sansad"],
            "Visva-Bharati": ["visva", "viswa", "vishwa", "vishwabharati", "biswa bharati"],
            "M.C. Sarkar & Sons Pvt. Ltd.": ["sarkar", "sons"]
        },
        "synonyms_author": {
            "Rabindranath Tagore": ["rabindranath tagore", "rabindranath thakur", "rabindra nath"],
            "Ashapurna Devi": ["ashapurna devi", "ashapurna debi", "ashapura"],
            "Jibanananda Das": ["jibanananda das", "jibanananda dash"],
            "Bibhutibhushan Bandyopadhyay": ["bibhuti", "bandyopadhyay", "bandopadhyay", "bibhubhusan"],
            "Sunil Gangopadhyay": ["sunil ganguly", "sunil gangopadhyaya", "sunil ganguli"],
            "Sarat Chandra Chattopadhyay": ["sarat chandra", "sharatchandra", "chattopadhay"],
            "Banaful": ["banaful", "bonophool", "bonoful", "balaichand"],
            "Abanindranath Tagore": ["abanindranath tagore", "abanindranath thakur"]
        },
        "synonyms_keywords": {
            "Galpa": "golpo", "Samagra": "somogro", "Rachanabali": "rochonaboli|rachonabali",
            "Pratham": "prothom", "Tanaya": "tonoya", "Ajanta": "ojonta", "Satabarsha": "shotoborsho",
            "Subarnalata": "shubarnalata", "Sera": "shera", "Sonar": "shonar", "Satyajit": "shatyajit",
            "Biswas": "bishwas", "Teen": "tin", "Teeney": "tiney", "Ekey": "akey",
            "Kailashe": "koilashey|kailashey", "Pratisruti": "protishruti|protisruti",
            "Ghanada": "ghonada", "Jnan Gamyi": "jnangomyi|jnangamyi", "Double": "dubol"
        }
    }
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default_state

def save_session_state(state_df):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state_df, f, indent=4, ensure_ascii=False)

# ==========================================
# PROCESSING UTILITIES
# ==========================================
state = load_session_state()

def clean_text(text):
    if text is None or pd.isna(text) or str(text).strip() == "": return ""
    return re.sub(r'\s+', ' ', str(text)).strip()

def normalize_entity(text, category):
    """Standardizes names/publishers based on the JSON memory."""
    t = clean_text(text)
    if not t: return ""
    tl = t.lower()
    
    mapping = state.get(f"synonyms_{category.lower()}", {})
    for standard, variations in mapping.items():
        if any(v in tl for v in variations):
            return standard
    return t

def normalize_title(text):
    """Standardizes phonetic words inside titles based on JSON memory."""
    t = clean_text(text)
    if not t: return ""
    for standard, pattern in state["synonyms_keywords"].items():
        # Uses word-boundary regex for accuracy
        t = re.sub(rf'\b({pattern})\b', standard, t, flags=re.IGNORECASE)
    return t

def validate_date(date_val):
    today = datetime.now().strftime('%Y-%m-%d')
    if not date_val or pd.isna(date_val): return today
    date_str = str(date_val).strip()
    for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y']:
        try: return datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
        except: continue
    return today

# ==========================================
# MAIN EXECUTION
# ==========================================
if not os.path.exists(INPUT_FILE):
    print(f"❌ ERROR: {INPUT_FILE} not found.")
else:
    try:
        df = pd.read_csv(INPUT_FILE, dtype=str, header=0, encoding='utf-8-sig').fillna('')
    except:
        df = pd.read_csv(INPUT_FILE, dtype=str, header=0, encoding='latin1').fillna('')

    file_base = os.path.splitext(os.path.basename(INPUT_FILE))[0]
    OUTPUT_MRC = f"cleaned_{file_base}.mrc"; OUTPUT_XLSX = f"cleaned_{file_base}.xlsx"
    ERROR_XLSX = f"error_{file_base}.xlsx"

    today_date = datetime.now().strftime('%Y-%m-%d')
    last_bc = state["last_primary_barcode"]
    
    # Pre-scan for the highest 10xxxx in current file to keep session synced
    for val in df.iloc[:, COL_BARCODE]:
        s_val = str(val).strip()
        if s_val.isdigit() and s_val.startswith('10'):
            last_bc = max(last_bc, int(s_val))

    clean_xlsx_rows, error_rows, marc_recs = [], [], []

    for index, row in df.iterrows():
        # 0. Skip blank rows
        if not "".join([str(v) for v in row]).strip(): continue

        # 1. Normalization & Validation
        title = normalize_title(row.iloc[COL_TITLE])
        author = normalize_entity(row.iloc[COL_AUTHOR], 'Author')
        pub = normalize_entity(row.iloc[COL_PUBLISHER], 'Publisher')
        
        if index > 0: # Row 2 labels are kept
            if not title or not author:
                err_row = row.copy()
                err_row['Error_Reason'] = "Missing Title/Author"
                error_rows.append(err_row)
                continue

        # 2. Standards
        final_date = validate_date(row.iloc[COL_DATE])
        itype_existing = str(row.iloc[COL_ITEM_TYPE]).strip()
        note = str(row.iloc[COL_NOTE]).strip()
        final_itype = "ASB" if "author signed" in note.lower() else (itype_existing or "BK")
        
        # 3. Barcode Logic (Persistent 10xxxx series)
        input_bc = str(row.iloc[COL_BARCODE]).strip()
        if index > 0:
            if input_bc.isdigit() and input_bc.startswith('10'):
                last_bc = max(last_bc, int(input_bc))
                current_main_bc = input_bc
            else:
                last_bc += 1
                current_main_bc = str(last_bc)
        else: current_main_bc = input_bc # Row 2 label preservation

        # 4. MARC creation
        rec = Record()
        rec.add_field(Field(tag='100', indicators=['1',' '], subfields=[Subfield(code='a', value=author)]))
        rec.add_field(Field(tag='245', indicators=['1','0'], subfields=[Subfield(code='a', value=title)]))
        rec.add_field(Field(tag='260', indicators=[' ',' '], subfields=[Subfield(code='b', value=pub), Subfield(code='c', value=str(row.iloc[COL_YEAR]))]))
        
        # Items logic
        def add_952(bc_val):
            rec.add_field(Field(tag='952', indicators=[' ',' '], subfields=[
                Subfield(code='a', value='DFL'), Subfield(code='p', value=bc_val),
                Subfield(code='o', value='891'), Subfield(code='d', value=final_date),
                Subfield(code='y', value=final_itype)
            ]))

        bib_barcodes = [current_main_bc]
        add_952(current_main_bc)

        # 5. Extra Copies (10xxxx -> 11xxxx, 12xxxx, 13xxxx)
        if index > 0:
            for col_idx, digit in zip([COL_COPY2, COL_COPY3, COL_COPY4], ['1', '2', '3']):
                if str(row.iloc[col_idx]).strip() not in ['', 'nan']:
                    if len(current_main_bc) >= 2:
                        copy_bc = current_main_bc[0] + digit + current_main_bc[2:]
                        bib_barcodes.append(copy_bc); add_952(copy_bc)

        marc_recs.append(rec)
        for b in bib_barcodes:
            xl_row = row.copy()
            xl_row.iloc[COL_BARCODE], xl_row.iloc[COL_AUTHOR], xl_row.iloc[COL_TITLE] = b, author, title
            xl_row.iloc[COL_PUBLISHER], xl_row.iloc[COL_DATE], xl_row.iloc[COL_ITEM_TYPE] = pub, final_date, final_itype
            clean_xlsx_rows.append(xl_row)

    # --- SAVE MEMORY & FILES ---
    state["last_primary_barcode"] = last_bc
    save_session_state(state)

    with open(OUTPUT_MRC, 'wb') as f:
        writer = MARCWriter(f)
        for r in marc_recs: writer.write(r)
        writer.close()

    final_df = pd.DataFrame(clean_xlsx_rows)
    final_df.drop(final_df.columns[[COL_COPY2, COL_COPY3, COL_COPY4]], axis=1, inplace=True)
    final_df.to_excel(OUTPUT_XLSX, index=False)
    if error_rows: pd.DataFrame(error_rows).to_excel(ERROR_XLSX, index=False)

    print(f"✅ SUCCESS!")
    print(f"Memory saved: {STATE_FILE} (Keep this for your next session!)")
    print(f"Last primary barcode reached: {last_bc}")
    print(f"MARC File: {OUTPUT_MRC}")


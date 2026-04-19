# Catalog App — Deployment Checklist

**App location on server:** `/home/dishari/koha-catalog-tools/catalog-app/`  
**URL:** `catalog.disharifoundation.org` (Cloudflare → Apache → localhost:5050)  
**Koha instance:** `dishari_lib`  
**SSH:** `ssh -p 2222 dishari@aluposto.ddns.net`

---

## Step 1 — Commit and push local changes

```bash
cd /Users/anirbanghosh/Code/koha-catalog-tools
git add catalog-app/ docs/
git status   # confirm dedup_registry.db is NOT staged
git commit -m "..."
git push
```

Status: ✅ Done

---

## Step 2 — rsync files to server

```bash
rsync -av -e "ssh -p 2222" \
--exclude='__pycache__' --exclude='*.db' \
--exclude='koha_session_meta.json' \
--exclude='uploads/' --exclude='output/' --exclude='sessions/' \
/Users/anirbanghosh/Code/koha-catalog-tools/catalog-app/ \
dishari@aluposto.ddns.net:/home/dishari/koha-catalog-tools/catalog-app/
```

**Note:** `koha_session_meta.json` is excluded because the server copy is the
live one (barcode counter increments with every run). Only sync it intentionally
when you have new synonyms to deploy — commit locally first, then rsync just
that file separately.

Status: ✅ Done

---

## Step 3 — Install Python dependencies

```bash
pip3 install flask gunicorn filelock openpyxl pymarc rapidfuzz
```

Status: ✅ Done

---

## Step 4 — koha_session_meta.json symlink

The app reads `koha_session_meta.json` from its working directory.
It is symlinked from the repo root:

```bash
ln -s /home/dishari/koha-catalog-tools/koha_session_meta.json \
      /home/dishari/koha-catalog-tools/catalog-app/koha_session_meta.json
```

**Important:** Never reset `koha_session_meta.json` — it contains the barcode
counter (`last_primary_barcode`) and all hand-curated synonym dictionaries.
The counter must always stay ahead of the highest barcode in Koha.

Status: ✅ Done

---

## Step 5 — sudoers rules

Two rules required — one for `koha-shell` (legacy, kept for manual use) and
one for `mysql` (used by `get_biblionumber` and `backfill_registry.py`):

```bash
sudo visudo -f /etc/sudoers.d/catalog-app
```

Contents:
```
dishari ALL=(root) NOPASSWD: /usr/sbin/koha-shell dishari_lib -c *
dishari ALL=(root) NOPASSWD: /usr/bin/mysql
```

Status: ✅ Done (2026-04-18)

---

## Step 6 — systemd service

Service file: `/home/dishari/koha-catalog-tools/catalog-app/catalog-app.service`

```bash
sudo cp /home/dishari/koha-catalog-tools/catalog-app/catalog-app.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable catalog-app
sudo systemctl start catalog-app
sudo systemctl status catalog-app
```

**Note:** gunicorn is at `/home/dishari/.local/bin/gunicorn` — already set
correctly in the service file.

To restart after code updates:
```bash
sudo systemctl restart catalog-app
```

Status: ✅ Done (running)

---

## Step 7 — Apache vhost

```bash
sudo nano /etc/apache2/sites-available/catalog-app.conf
```

Content:
```apache
<VirtualHost *:80>
    ServerName catalog.disharifoundation.org
    ProxyPass        / http://127.0.0.1:5050/
    ProxyPassReverse / http://127.0.0.1:5050/
</VirtualHost>
```

Enable and reload:
```bash
sudo a2enmod proxy proxy_http
sudo a2ensite catalog-app
sudo systemctl reload apache2
```

Status: ✅ Done (2026-04-18)

---

## Step 8 — SSL Certificate (Let's Encrypt)

```bash
sudo apt-get install certbot python3-certbot-apache
sudo certbot --apache -d catalog.disharifoundation.org
```

Status: ✅ Done (2026-04-18 — cert expires 2026-07-17)

---

## Step 9 — Cloudflare SSL Mode

In Cloudflare dashboard → SSL/TLS → Encryption level, set to **Full (strict)** mode.

Status: ✅ Done

---

## Step 10 — Cloudflare DNS

| Type | Name | Target | Proxy |
|------|------|--------|-------|
| CNAME | catalog | aluposto.ddns.net | ✅ Proxied |

Status: ✅ Done (2026-04-18)

---

## Step 11 — Koha match rule for MARC import (KohaBiblio)

The app generates MARC files for **manual import** via Koha Staff Interface →
Cataloging → Stage MARC Records for Import. The correct settings are:

| Setting | Value |
|---------|-------|
| Match rule | **KohaBiblio** |
| Action on match | **Ignore incoming record** (for copy records — preserves existing bib) |
| Action on no match | **Add incoming record** |
| Item handling | **Always add items** |

**KohaBiblio match rule configuration** (set once in Koha Staff → Administration → MARC Bibliographic Framework → Matching Rules):

| Match point | Tag | Subfield | Index | Score |
|-------------|-----|----------|-------|-------|
| Local-Number | 999 | c | Local-Number | 100 |

Required score: 100.

- New book records have no `999$c` → no match → new bib + item added ✓
- Copy records have `999$c` = biblionumber → exact match → bib ignored, item attached ✓

Status: ✅ Done (2026-04-19 — switched from STRICT_CLE to KohaBiblio)

---

## Step 12 — Seed/reload SQLite registry

To clear and reload the dedup registry from Koha:

```bash
# 1. Clear existing rows
sqlite3 /home/dishari/koha-catalog-tools/catalog-app/dedup_registry.db \
    "DELETE FROM books;"

# 2. Reload from Koha (uses sudo mysql — no credentials needed)
sudo python3 /home/dishari/koha-catalog-tools/catalog-app/backfill_registry.py
```

Expected output:
```
Fetched ~1709 bibs from Koha
Done — inserted ~1667 new rows, skipped ~42 already present.
```

The ~42 skipped are genuine duplicate bibs already in Koha (same normalized
title+author+edition). Run this any time the registry drifts from Koha
(e.g. after direct Koha imports bypassing the app).

Status: ✅ Done (2026-04-19 — 1667 rows loaded, 42 Koha duplicates skipped)

---

## Step 13 — Fix 952\$t copy numbers

Already applied via SQL UPDATE on 2026-04-16 — 1789 rows updated.

Status: ✅ Done

---

## Step 14 — End-to-end smoke test

1. Upload a small Gronthee XLSX → review screen shows rows correctly
2. Process → result screen shows stats (X new, 0 errors), MARC download available
3. Download `.mrc` → import manually via Koha Staff → Stage MARC Records
   (match rule: KohaBiblio, action on match: Ignore, action on no match: Add, items: Always add)
4. Check Koha OPAC → book appears and is searchable
5. Re-upload same file → all rows flagged DUPLICATE with `— Select action —` dropdown
6. Select "Add as Copy 2" → process → download MARC → import → verify copy item attached to existing bib (not new bib)
7. Upload simultaneously in two tabs → barcodes don't collide (filelock test)
8. Confirm `koha_session_meta.json` `last_primary_barcode` incremented correctly

Status: ⏳ In progress (2026-04-19)

---

## Environment variables (systemd service)

| Variable | Value | Purpose |
|----------|-------|---------|
| `CATALOG_PASSWORD` | set strong password | Shared login for volunteers |
| `FLASK_SECRET_KEY` | random string | Flask session signing |
| `KOHA_INSTANCE` | `dishari_lib` | Koha instance name (used by get_biblionumber) |
| `CATALOG_SCRIPT` | `../scripts/clean_catalog.py` | Path to clean_catalog.py |
| `CATALOG_META` | `./koha_session_meta.json` | Path to koha_session_meta.json |

---

## Routine update procedure

```bash
# 1. Commit and push locally
git add catalog-app/ docs/ && git commit -m "..." && git push

# 2. Rsync to server
rsync -av -e "ssh -p 2222" \
--exclude='__pycache__' --exclude='*.db' \
--exclude='koha_session_meta.json' \
--exclude='uploads/' --exclude='output/' --exclude='sessions/' \
/Users/anirbanghosh/Code/koha-catalog-tools/catalog-app/ \
dishari@aluposto.ddns.net:/home/dishari/koha-catalog-tools/catalog-app/

# 3. If catalog-app.service changed, reload systemd first
ssh -p 2222 dishari@aluposto.ddns.net "sudo systemctl daemon-reload"

# 4. Restart service
ssh -p 2222 dishari@aluposto.ddns.net "sudo systemctl restart catalog-app"
```

---

## Git history cleanup (post go-live)

Session JSON files were committed in `bffe884` and `5347cac`. They are no
longer in the working tree but exist in git history. To purge them:

```bash
pip install git-filter-repo
git filter-repo --path catalog-app/sessions/ --invert-paths
git push origin main --force
```

**Note:** Anyone with a local clone must `git fetch --all && git reset --hard origin/main` afterwards.

Status: ⬜ Pending (non-blocking — no secrets in sessions files)

---

## Duplicate bib cleanup (pending)

~42 duplicate bib records identified in Koha (same normalized title/author/edition).
After merging duplicates in Koha Staff, re-run backfill (Step 12) to resync the registry.

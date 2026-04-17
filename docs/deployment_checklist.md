# Catalog App — Deployment Checklist

**App location on server:** `/home/dishari/koha-catalog-tools/catalog-app/`  
**URL:** `catalog.disharifoundation.org` (Cloudflare → Apache → localhost:5050)  
**Koha instance:** `dishari_lib`  
**SSH:** `ssh -p 2222 dishari@aluposto.ddns.net`

---

## Step 1 — rsync files to server

```bash
rsync -av -e "ssh -p 2222" \
--exclude='__pycache__' --exclude='*.db' \
--exclude='uploads/' --exclude='output/' --exclude='sessions/' \
/Users/anirbanghosh/Code/koha-catalog-tools/catalog-app/ \
dishari@aluposto.ddns.net:/home/dishari/koha-catalog-tools/catalog-app/
```

Status: ✅ Done

---

## Step 2 — Install Python dependencies

```bash
pip3 install flask gunicorn filelock openpyxl pymarc rapidfuzz
```

Status: ✅ Done

---

## Step 3 — koha_session_meta.json symlink

The app reads `koha_session_meta.json` from its working directory.
It is symlinked from the repo root:

```bash
ln -s /home/dishari/koha-catalog-tools/koha_session_meta.json \
      /home/dishari/koha-catalog-tools/catalog-app/koha_session_meta.json
```

**Important:** Never use the local test copy (`catalog-app/koha_session_meta.json`
with barcode 109000) in production.

Status: ✅ Done

---

## Step 4 — sudoers for koha-shell

Allows the Flask app (running as `dishari`) to invoke `bulkmarcimport.pl`
via `koha-shell` without a password.

```bash
sudo visudo -f /etc/sudoers.d/catalog-app
```

Add:
```
dishari ALL=(root) NOPASSWD: /usr/sbin/koha-shell dishari_lib -c *
```

Status: ⬜ Pending

---

## Step 5 — systemd service

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

## Step 6 — Apache vhost

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

Status: ⬜ Pending

---

## Step 7 — Cloudflare DNS

Add a CNAME record in Cloudflare:

| Type | Name | Target | Proxy |
|------|------|--------|-------|
| CNAME | catalog | aluposto.ddns.net | ✅ Proxied |

Status: ⬜ Pending

---

## Step 8 — Backfill SQLite registry from Koha MySQL

Seeds the dedup registry with all 1700+ existing books from Koha.
Safe to re-run — uses `INSERT OR IGNORE`.

```bash
# Clean run (reset first if re-seeding)
sqlite3 /home/dishari/koha-catalog-tools/catalog-app/dedup_registry.db "DELETE FROM books;"
sudo python3 /home/dishari/koha-catalog-tools/catalog-app/backfill_registry.py
```

Expected output:
```
Fetched 1709 bibs from Koha
Done — inserted 1709 new rows, skipped 0 already present.
```

**After merging duplicate bibs in Koha:** re-run this step to resync.

Status: ⬜ Pending (clean re-run needed)

---

## Step 9 — Verify Koha matching rule

Confirm `STRICT_CLE` exists in Koha:

```bash
sudo koha-shell dishari_lib -c "perl -e 'use Koha::MatchingRules; print \$_->code.\"\n\" for Koha::MatchingRules->search->as_list'"
```

If the rule has a different code, update `KOHA_MATCH_RULE` in the systemd
service file and restart.

Status: ✅ Done — STRICT_CLE confirmed present

---

## Step 10 — Fix 952\$t copy numbers

Already applied via SQL UPDATE on 2026-04-16 — 1789 rows updated.

Status: ✅ Done

---

## Step 11 — End-to-end smoke test

1. Upload a small Gronthee XLSX → review screen shows rows correctly
2. Process → result screen shows import stats (X new, 0 errors)
3. Check Koha OPAC → book appears and is searchable
4. Re-upload same file → all rows flagged DUPLICATE with correct copy action
5. Upload simultaneously in two tabs → barcodes don't collide (filelock test)
6. Confirm `koha_session_meta.json` `last_primary_barcode` incremented correctly

Status: ⬜ Pending

---

## Environment variables (systemd service)

| Variable | Value | Purpose |
|----------|-------|---------|
| `CATALOG_PASSWORD` | set strong password | Shared login for volunteers |
| `FLASK_SECRET_KEY` | random string | Flask session signing |
| `KOHA_INSTANCE` | `dishari_lib` | Koha instance for koha-shell |
| `KOHA_MATCH_RULE` | `STRICT_CLE` | Match rule for bulkmarcimport.pl |
| `CATALOG_SCRIPT` | `../scripts/clean_catalog.py` | Path to clean_catalog.py |
| `CATALOG_META` | `./koha_session_meta.json` | Path to koha_session_meta.json |

---

## Routine update procedure

```bash
# 1. Push latest code
rsync -av -e "ssh -p 2222" \
--exclude='__pycache__' --exclude='*.db' \
--exclude='uploads/' --exclude='output/' --exclude='sessions/' \
/Users/anirbanghosh/Code/koha-catalog-tools/catalog-app/ \
dishari@aluposto.ddns.net:/home/dishari/koha-catalog-tools/catalog-app/

# 2. Restart service
ssh -p 2222 dishari@aluposto.ddns.net "sudo systemctl restart catalog-app"
```

---

## Duplicate bib cleanup (pending)

~29 duplicate bib records identified in Koha MySQL (same title/author/edition).
Steps to merge: see `docs/catalog-app-functional-spec.md` or run the
**"Duplicate Bibs — Pending Review"** Koha report.

After merging, always resync the registry (Step 8).

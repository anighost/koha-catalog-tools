# Catalog App — Deployment Checklist

**App location on server:** `/home/dishari/koha-catalog-tools/catalog-app/`  
**URL:** `catalog.disharifoundation.org` (Cloudflare → Apache → localhost:5050)  
**Koha instance:** `dishari_lib`  
**SSH:** `ssh -p 2222 dishari@aluposto.ddns.net`

---

## Step 1 — Commit and push local changes

Commit G1/G3 dedup, test suite, and gitignore updates:

```bash
cd /Users/anirbanghosh/Code/koha-catalog-tools
git add .gitignore catalog-app/
git status   # confirm dedup_registry.db is NOT staged
git commit -m "Add G1/G3 dedup, test suite, gitignore db/sessions"
git push
```

Status: ⬜ Pending

---

## Step 2 — rsync files to server

```bash
rsync -av -e "ssh -p 2222" \
--exclude='__pycache__' --exclude='*.db' \
--exclude='uploads/' --exclude='output/' --exclude='sessions/' \
/Users/anirbanghosh/Code/koha-catalog-tools/catalog-app/ \
dishari@aluposto.ddns.net:/home/dishari/koha-catalog-tools/catalog-app/
```

Status: ⬜ Pending (re-sync needed for G1/G3 changes)

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

**Important:** Never use the local test copy (`catalog-app/koha_session_meta.json`
with barcode 109000) in production.

Status: ✅ Done

---

## Step 5 — sudoers for koha-shell

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

Status: ⬜ Pending

---

## Step 8 — Cloudflare DNS

Add a CNAME record in Cloudflare:

| Type | Name | Target | Proxy |
|------|------|--------|-------|
| CNAME | catalog | aluposto.ddns.net | ✅ Proxied |

Status: ⬜ Pending

---

## Step 9 — Reset and re-seed SQLite registry

G1 added `edition_norm` and changed the unique index. The old registry on the
server has the pre-G1 schema. Steps must be done in this order:

```bash
# 1. Restart app so init_db() runs and migrates the schema (adds edition_norm, recreates idx_dedup)
sudo systemctl restart catalog-app

# 2. Wipe the old registry rows (schema is now correct, data is stale)
sqlite3 /home/dishari/koha-catalog-tools/catalog-app/dedup_registry.db "DELETE FROM books;"

# 3. Re-seed from Koha MySQL
sudo python3 /home/dishari/koha-catalog-tools/catalog-app/backfill_registry.py
```

Expected output:
```
Fetched ~1709 bibs from Koha
Done — inserted ~1709 new rows, skipped 0 already present.
```

**After merging duplicate bibs in Koha:** re-run backfill only (no DELETE needed).

Status: ⬜ Pending

---

## Step 10 — Verify Koha matching rule

Confirm `STRICT_CLE` exists in Koha:

```bash
sudo koha-shell dishari_lib -c "perl -e 'use Koha::MatchingRules; print \$_->code.\"\n\" for Koha::MatchingRules->search->as_list'"
```

If the rule has a different code, update `KOHA_MATCH_RULE` in the systemd
service file and restart.

Status: ✅ Done — STRICT_CLE confirmed present

---

## Step 11 — Fix 952\$t copy numbers

Already applied via SQL UPDATE on 2026-04-16 — 1789 rows updated.

Status: ✅ Done

---

## Step 12 — End-to-end smoke test

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
# 1. Commit and push locally
git add catalog-app/ && git commit -m "..." && git push

# 2. Rsync to server
rsync -av -e "ssh -p 2222" \
--exclude='__pycache__' --exclude='*.db' \
--exclude='uploads/' --exclude='output/' --exclude='sessions/' \
/Users/anirbanghosh/Code/koha-catalog-tools/catalog-app/ \
dishari@aluposto.ddns.net:/home/dishari/koha-catalog-tools/catalog-app/

# 3. Restart service
ssh -p 2222 dishari@aluposto.ddns.net "sudo systemctl restart catalog-app"
```

---

## Git history cleanup (post go-live)

Session JSON files were committed in `bffe884` and `5347cac`. They are no
longer in the working tree but exist in git history. To purge them:

```bash
# Install git-filter-repo if not already installed
pip install git-filter-repo

# Remove sessions/ from all history
git filter-repo --path catalog-app/sessions/ --invert-paths

# Force-push (private repo — safe, only you use it)
git push origin main --force
```

**Note:** Anyone with a local clone must `git fetch --all && git reset --hard origin/main` afterwards.

Status: ⬜ Pending (non-blocking — no secrets in sessions files)

---

## Duplicate bib cleanup (pending)

~29 duplicate bib records identified in Koha MySQL (same title/author/edition).
Steps to merge: see `docs/catalog-app-functional-spec.md` or run the
**"Duplicate Bibs — Pending Review"** Koha report.

After merging, always resync the registry (Step 8).

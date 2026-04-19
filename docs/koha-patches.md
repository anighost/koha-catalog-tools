# Koha Database Patches

Manual fixes applied to the `koha_dishari_lib` database where Koha's migration system left schema drift. Each entry documents what was wrong, why it happened, and the exact fix applied.

---

## 2026-04-19 — `old_issues.renewals` renamed to `renewals_count`

### Symptom

Clicking a barcode link in the Koha Staff holdings table (e.g. `moredetail.pl?biblionumber=166&itemnumber=199`) produced a 500 error.

Plack error log showed:

```
DBD::mysql::st execute failed: Unknown column 'me.renewals_count' in 'SELECT'
```

### Root cause

A Koha upgrade renamed the column `renewals` → `renewals_count` in the `issues` table but silently skipped `old_issues`. The migration was marked as complete, so `koha-upgrade-schema dishari_lib` reported "No database change required" and offered no further help.

`moredetail.pl` queries `old_issues` to display checkout history and failed because the column it expected (`renewals_count`) did not exist.

### Diagnosis steps

```sql
-- Confirmed column missing from old_issues
SHOW COLUMNS FROM koha_dishari_lib.old_issues LIKE 'renewals_count';
-- Empty set

-- Confirmed expected schema from kohastructure.sql
-- /usr/share/koha/intranet/cgi-bin/installer/data/mysql/kohastructure.sql
-- Expected: renewals_count tinyint(4) NOT NULL DEFAULT 0
-- Actual:   renewals       tinyint(4) NOT NULL DEFAULT 0
```

### Fix

`CHANGE COLUMN` (not `ADD COLUMN`) — preserves all existing renewal count data for historical checkouts:

```sql
ALTER TABLE old_issues
  CHANGE COLUMN renewals renewals_count
  TINYINT(4) NOT NULL DEFAULT 0
  COMMENT 'lists the number of times the item was renewed';
```

No Plack, Zebra, or Apache restart required — the ALTER takes effect immediately.

### Verification

Barcode links in the holdings table (`moredetail.pl`) load without error.

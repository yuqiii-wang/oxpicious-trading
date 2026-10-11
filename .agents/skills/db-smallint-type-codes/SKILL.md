---
name: db-smallint-type-codes
description: Closed-vocabulary TYPE columns in this repo's PostgreSQL DB are SMALLINT label codes (the stats.label_dim contract) — never TEXT or PG enums. Use whenever creating or altering ANY table that has a type / kind / state / side / family / sub_type / resource_type style column (analysis, stats, monitor, live, strategy schemas alike), when adding or changing a label vocabulary domain, or when writing Python/TypeScript that reads or writes such a column.
---

# db-smallint-type-codes — type columns are SMALLINT label codes

Every DB column whose values form a CLOSED categorical vocabulary ("type",
"kind", "state", "side", "family", "sub_type", ...) stores a **0-based
SMALLINT code**, not TEXT and not a PostgreSQL ENUM type. The code → label
mapping lives in ONE place with three identical mirrors:

| Mirror | File | Who reads it |
| --- | --- | --- |
| DB source of truth | `database/sql/stats/21_label_dim.sql` (`stats.label_dim`) | SQL decode joins |
| Python | `_common/labels.py` (`LABEL_MAPS` / `XXX_CODE` dicts) | pipelines, writers |
| TypeScript | `data_viz/api/services/label-codes.ts` (`LABEL_CODE`) | API decode/params |

`temp_scripts/check_label_dim.py` (run via WSL) asserts DB == Python and
prints the TS mirror — a change that touches only one mirror is drift and
will fail that check.

## Why codes (not TEXT, not PG ENUM)

- Label strings riding cudf.pandas frames through sort/merge/concat fault
  at host transfer on cuDF 26.08 (`cudaErrorIllegalAddress`); int16 codes
  are cudf-native end-to-end. Decode happens ONLY at the API boundary
  (TS `labelDecodeSql` / SQL scalar subquery against `label_dim`) — never
  mid-compute, never in Python display code.
- One shared decode table beats per-table CHECK constraints and ENUM
  types: adding a vocabulary value is one INSERT in three mirrors, not an
  ALTER TYPE / table rebuild. ENUM types additionally break bulk COPY
  casts and are banned here.

## Rules for a new type column

1. Column is `SMALLINT NOT NULL` (nullable ONLY when the source row may
   legitimately carry no value — e.g. `stats.stock_identity.board`).
2. Codes are 0-based; code order should carry semantic order where one
   exists (e.g. `px_speed` sharp_dn→sharp_up). Use `99 = 'other'` as the
   unrecognized-host/fallback sentinel only where unknowns are possible
   (see `ext_http_source`).
3. Register the domain in ALL THREE mirrors (next section) BEFORE the
   first writer ships. In the column's SQL `COMMENT ON COLUMN`, name the
   domain and spell out the codes.
4. A sub-type column whose domain is selected by a parent type column is
   fine — document the selection rule in the column comment. Worked
   example: `monitor.external_resource_audit`
   (`database/sql/monitoring/05_external_resource_audit.sql`) —
   `resource_type` decodes via `ext_resource` (0 http | 1 llm);
   `sub_type` decodes via `ext_http_source` when http, `ext_llm_provider`
   when llm. Decode joins `label_dim` on `(domain, code)`.

## Adding a domain (the 4 steps)

1. `database/sql/stats/21_label_dim.sql`: add the rows to the INSERT
   block (and the domain to the header's converted list). This file is
   the fresh-install source of truth.
2. `_common/labels.py`: add `XXX_CODE`, register it in `LABEL_MAPS` and
   `LABEL_ORDER` (the `*_LABEL` exports derive automatically).
3. `data_viz/api/services/label-codes.ts`: extend the `LabelDomain` union
   and `LABEL_CODE`.
4. Apply to the live DB from a `temp_scripts/` script with
   `INSERT ... ON CONFLICT (domain, code) DO NOTHING` (do NOT re-run the
   whole 21_label_dim.sql on live — it DROPs the table), then run
   `temp_scripts/check_label_dim.py` through WSL and see `OK`.

## Do NOT convert (keep TEXT — deliberate exceptions)

API-facing identity and filter columns (`sec_type` outside the analysis
schema's converted tables, `industry_id`, `sector_id`, `exchange`,
`signal_type` / `signal_sub_type`), open-ended vocabularies (a value set
that grows without a code change — URLs, reasons, free-form tags), and
the `text.*` LLM-layer enums. These are listed in 21_label_dim.sql's
"NOT converted" header; when unsure, keep TEXT and add the domain as
PENDING vocabulary instead of half-converting a column.

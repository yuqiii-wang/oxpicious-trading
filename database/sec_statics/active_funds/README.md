# active_funds/ — curated active-funds registry

Hand-curated registry of the actively-managed funds (主动管理型 + 指数增强型)
of the two tracked issuers, selected 2026-11-14 by top 资产规模 + longest
成立历史 ("a few dozen, not all"). One JSON file per owner, keyed by the
`owner_id` vocabulary of `../sec_owners.json`:

- `e_fund.json` — 易方达基金 (23 funds: 7 listed active ETFs + 16 OTC)
- `pinebridge.json` — 华泰柏瑞基金 (18 funds: 3 listed active ETFs + 15 OTC)

## Entry schema

```json
{
  "code": "581050",            // A-share-class primary code, bare 6-digit
  "name": "易方达品质均衡主动管理ETF",
  "style": "active_mgmt",      // active_mgmt 主动管理 | enhanced_index 指数增强
  "venue": "etf",              // etf exchange-listed | otc off-exchange
  "exchange": "SS",            // listing venue (venue=etf only; null for OTC)
  "inception": "2026-10-28",   // 成立日期 (ISO)
  "aum_yi": 20.0,              // 资产规模(亿元), 2026-09-30 report unless noted
  "benchmark": "中证800价值",   // optional — tracking/benchmark index name
  "share_classes": ["004746"], // sibling A/C/Y class codes (NOT entries of their own)
  "note": "…"
}
```

## Consumers (edit the file, never the consumers)

- `builds.classification` — `venue=etf` entries get the explicit strategy
  tag `ACTIVE` (主动): `ACTIVE_ENHANCED` 指数增强 / `ACTIVE_MGMT` 主动管理
  (ETF rows only; OTC entries are reference data, never classified).
- `downloads.etf.huatai_pb.*` / `downloads.etf.efunds.*` — every PER-FUND
  service (archive / intro / composition / dividends) defaults to the
  owner's `venue=etf` codes. The whole-family trend services are NOT
  gated (one request for the whole family, never per-fund).
- `builds.etf.feeder_nav` — the listed-ETF NAV-history backfill loads
  exactly `active_etf_codes()`.

Loader: `../active_funds_store.py` (`load_active_funds`,
`active_etf_codes`, `require_active_etf_funds`).

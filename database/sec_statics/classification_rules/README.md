# Security classification rule configs (language agnostic)

All classification RULES live here as pure JSON — no Python (or any other
language) is needed to read, validate, diff, or reimplement them.  The
Python side is ONE small generic interpreter module
(`database/sec_statics/classification.py`) that loads these files and
produces the classification data; loading a different rules directory
changes the classification with zero code changes (`python -m
database.sec_statics.classification --rules <dir> --names <file>`).

## Files

| file             | contents                                                          |
|------------------|-------------------------------------------------------------------|
| `industry.json`  | INDUSTRY ruleset — real industries (FIN, HC, TECH, DEBT, …)        |
| `strategy.json`  | STRATEGY ruleset — themes/strategies (BROAD, DIV, REGION, STRATEGY, SOE, OVERSEAS) |
| `config.json`    | shared config — fallback defaults, strategy sector ids, ETF name-cleaning markers, overlapping catalog entries |

## Ruleset schema (`industry.json` / `strategy.json`)

```json
{
  "ruleset": "industry",
  "generic_penalty_sectors": [],
  "single_tag_sectors": [],
  "rules": [
    {
      "sector_id": "DEBT",
      "sector_label": "债券",
      "industry_id": "DEBT_TREASURY",
      "industry_label": "国债",
      "keywords": ["国债"],
      "note": "optional documentation — the engine ignores unknown fields"
    }
  ]
}
```

- **rules** — ordered list.  **Order matters**: lower index = higher
  priority for tie-breaking.  The same `(sector_id, industry_id)` key MAY
  appear twice (e.g. `STRATEGY/STRAT_THEMED`); both entries score with the
  LAST index of that key.
- **sector_id / industry_id** — the unified DB column pair.  For the
  strategy ruleset, sector_id holds the strategy (BROAD, DIV, …) and
  industry_id holds the theme (BROAD_CSI, DIV_SOE, …).
- **keywords** — substring match against the security name; empty list =
  never auto-matched (hand-authored tag only, e.g. `benchmark_broadmarket`).
- **generic_penalty_sectors** — sectors whose matches always LOSE to any
  match from a non-listed sector, regardless of keyword length (strategy
  ruleset: `["BROAD", "REGION"]` so specific strategies beat broad
  catch-alls — without it "中证红利" would classify as BROAD_CSI instead of
  DIV_GENERAL).
- **single_tag_sectors** — sectors that contribute at most ONE tag in the
  all-tags output, keeping only their best match (strategy ruleset:
  `["BROAD"]` — an index carries exactly one broad-market theme, so the
  generic 中证/上证 catch-all is dropped when a specific series matches).

## Scoring (interpreter contract)

For each rule whose keywords hit the name by substring:

```
score = (generic_penalty, total_hit_length, n_hits, longest_hit_length, -rule_order)
generic_penalty = 0 if rule.sector_id in ruleset.generic_penalty_sectors else 1
rule_order      = last 0-based index of the rule's (sector_id, industry_id) key
```

- **best match**: highest score; exact ties keep the EARLIEST rule.
- **all tags**: matches sorted by score descending (stable sort — equal
  scores keep rule order), deduplicated by `(sector_id, industry_id)`
  keeping the highest score, with `single_tag_sectors` contributing at most
  one tag.
- **unified model**: a name is classified against BOTH rulesets.  The
  industry match is PRIMARY when it exists (`is_industry_not_strategy` =
  true), otherwise the strategy match.  Tags list industry tags first,
  then strategy tags.

## config.json schema

```json
{
  "defaults": {"sector_id": "OTHER", "sector_label": "其他",
               "industry_id": "OTHER", "industry_label": "未分类"},
  "strategy_sector_ids": ["BROAD", "DIV", "OVERSEAS", "REGION", "SOE", "STRATEGY"],
  "etf_name_cleaning": {
    "ib_name_marker": "交易型开放式指数",
    "suffix_split": "交易型",
    "lof_markers": ["LOF", "lof", "Lof", "上市开放式基金"]
  },
  "overlapping_catalog": [
    {"sector_id": "ENG", "sector_label": "能源",
     "industry_id": "POWER_EQUIP", "industry_label": "电力设备"}
  ]
}
```

- **defaults** — the fallback classification when nothing matches.
- **strategy_sector_ids** — sectors that are strategy-type (used by
  stock-mapping logic to exclude broad-market indices, and to detect
  whether cached tags carry strategy tags).
- **etf_name_cleaning** — ETF name preprocessing markers: a name WITHOUT
  `ib_name_marker` is a foreign-branded "IB name" (classify by its
  underlying index name instead); standard names are cut at
  `suffix_split` (drops the legal suffix 交易型开放式指数证券投资基金,
  avoiding a false 证券 match) and at the first `lof_markers` occurrence
  (position > 0) before keyword matching.
- **overlapping_catalog** — catalog-only entries where the same
  industry_id conceptually spans multiple sector_ids; added to the catalog
  even when no security is assigned to them.

The **catalog** is derived: `{sector: {label, industries: {industry:
{label, slug}}}}` where `slug = industry_id.lower()`; sector order =
defaults first, then first-appearance order of the rules (industry ruleset
before strategy), then the overlapping entries.

## Editing rules

Add/reorder keywords here, then run `python -m builds.classification
--reclassify` to propagate the new rules to the cached index
classifications (hand-edited entries in `sec_classification/` are the
authoritative cache and are preserved).

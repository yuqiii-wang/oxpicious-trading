"""sec_statics -- security classification rules, JSON catalogs, and owner registry.

Consolidates the static (non-computed) classification assets:

  - classification.py      : INDUSTRY_RULES + STRATEGY_RULES + classify_* engine
  - sec_classification.json: authoritative, hand-editable index classification cache
  - sec_owners.json        : curated ETF manager / company owner registry

Migrated from _common/ top-level to _common/sec_statics/ subpackage.
"""

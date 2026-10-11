"""sec_statics -- security classification rules, JSON catalogs, and owner registry.

Consolidates the static (non-computed) classification assets:

  - classification.py       : the ONE Python module — a small generic
                              interpreter over the JSON rule configs
                              (keyword scoring, tie-breaks, tags, ETF-name
                              cleaning, catalog assembly, CLI)
  - classification_rules/   : ALL rules as language-agnostic JSON configs
                              (industry.json, strategy.json, config.json;
                              schema in classification_rules/README.md)
  - classification_store.py : shard-tree reader/writer for the
                              sec_classification/ cache (single source of
                              truth for the layout)
  - sec_classification/     : the authoritative, hand-editable index
                              classification cache — sharded into meta.json,
                              catalog/<NN_sector>.json and
                              indices/<sector>/<industry>.json (formerly the
                              single sec_classification.json)
  - sec_owners.json         : curated ETF manager / company owner registry

Lives under ``database/sec_statics/`` (migrated from the ``_common/``
subpackage).
"""

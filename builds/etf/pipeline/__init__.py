"""builds.etf.pipeline — staged ETF build pipeline.

Each module owns one stage of the build; ``main.run`` glues them in order:

    discover  → scope   → load      → features
                (DB)     (CSV+DB)    (merge/split/MA)
        ↓
    pe_scope  → universe → writes    → composition_write → quality
   (masks+PE)

The thin ``builds/etf/__main__.py`` entry keeps only pre-check + cudf
activation + argparse and calls ``pipeline.main.run(args)``.
"""

"""csindex.com.cn index PE downloader package — the WEEKLY PE service.

Service split (2026-11-23): the PE fetch lived inside the former
``downloads.index.csindex.quote`` package (nightly ``--pe-only`` step,
2026-11-20..22) — now its OWN service so the weekly cadence keys on
THIS name's audit history (price and PE audit, pace and gate
separately). The entry point is ``__main__``; the shared fetch
machinery is the sibling ``price`` package (whose
:class:`CsindexQuoteRunner` carries the ``pe_only`` path this service
drives).
"""

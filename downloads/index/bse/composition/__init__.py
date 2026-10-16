"""BSE index composition downloader (北证50 899050 + 北证专精特新 899601).

The 成分股列表 (constituent + weight snapshot) comes from the CSI
co-published closeweight autofile — the bse.cn index pages' own widget
endpoint serves only a 10-row live-quote list with no weights (see
``runner``). Fetch/parse/cache helpers are reused from
``downloads.index.csindex.composition``; this package adds the BSE scope,
the ``.BJ`` stock-code suffix, and its own external_request_config row.
"""

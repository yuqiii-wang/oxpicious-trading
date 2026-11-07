-- All board rows for ONE sec_type (index/etf/stock), pct DESC — the whole
-- per-type slice is cached in-process and filtered per code in JS. ETF/index
-- pct = composition-weight share; stock pct = 100. board decodes to its TEXT
-- label (UI contract). Serves GET /api/sec-board.
-- args:
--   sec_type: value text — 'index' | 'etf' | 'stock'
--   board_decode: fragment — labelDecodeSql("board", "board")

      SELECT code,
             {{board_decode}} AS board,
             pct,
             n_stocks,
             snapshot_date::text AS snapshot_date
        FROM stats.sec_board_map
       WHERE sec_type = {{sec_type}}
       ORDER BY code, pct DESC

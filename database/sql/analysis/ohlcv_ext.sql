-- ============================================================================
--  Table: analysis.ohlcv_ext
--
--  OHLCV EXTENDED TECH STATS — one row per (code, sec_type, date) with
--  the per-day intraday + prev-day anatomy the close-only signal tier
--  does not see: the open/high/low/close gaps, the close-relative wick
--  depths, the close location in range, the prev-day reference gaps
--  (open vs prev close, close vs prev high/low) and the participation
--  gauges (trading_amount vs its own trailing MA20 / MA252).
--
--  SELF-CONTAINED PRODUCER: computed by
--  python -m analysis.analysis_signals.signal_amplification directly
--  from stats.{index,etf,stock}_basic_stats — it does NOT read the
--  mov_ave_spread anatomy stores (analysis.mov_ave_spreads_detail_
--  ohlc_gaps / mov_ave_price_vs_amt), whose semantics it parallels
--  (gap conventions cited from that suite's ohlc_gaps step). The
--  signal-amplification amplifiers are its primary consumer.
--
--  ESTIMATED-DATA EXCLUSION (the mov_ave_spreads_detail_ohlc_gaps
--  convention): source rows with is_ohl_estimated = TRUE are excluded
--  ENTIRELY — synthetic OHLC (open = prev_close, high = low = close)
--  would zero out the wick/range gaps and read as a perfectly flat
--  day. The exclusion is also a prev-row break: every prev_* reference
--  points at the previous VALID (non-estimated) row of the same code,
--  so overnight legs never cross a synthetic row. Dates whose every
--  source row is estimated therefore have no rows here and stay
--  "missing" to incremental detection (harmless, logged).
--
--  ETF NORMALIZATION: ETFs use the ADJUSTED series where the source
--  carries one (COALESCE(adj_open, open) etc.) — same-date ratios are
--  invariant to the multiplicative adjustment and cross-date legs
--  become total-return gaps. Index/stock use raw OHLC.
--
--  All gap/ratio columns are signed FRACTIONS (not %). NULL when any
--  input is NaN or the denominator is zero/denormalized (|den| < 1e-12).
--
--  Populated by python -m analysis.analysis_signals.signal_amplification
--  (incremental per-code horizon check; --force purges the sec_type
--  scope and rebuilds).
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each security's daily OHLCV extended tech stats — intraday open/high/low/close gaps, wick depths, close position, prev-day reference gaps and volume (trading amount) participation ratios vs its own trailing MAs.
-- tags: ohlcv, K线, gap, 跳空, intraday, 日内, wick, 影线, prev day, 前一日, volume, 成交额, participation, 量比, tech stats, 技术统计

DROP TABLE IF EXISTS analysis.ohlcv_ext CASCADE;

CREATE TABLE analysis.ohlcv_ext (
    sec_type          SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,

    -- same-day intraday anatomy (close-relative unless stated)
    gap_oc            NUMERIC(10,6),  -- (close - open) / open — the intraday leg (高走/低走)
    gap_hc            NUMERIC(10,6),  -- (close - high) / high — close below the day high (rejection depth)
    gap_lc            NUMERIC(10,6),  -- (close - low) / low — close above the day low (support depth)
    uw                NUMERIC(10,6),  -- (high - GREATEST(open, close)) / prev_close — upper wick (冲高回落)
    lw                NUMERIC(10,6),  -- (LEAST(open, close) - low) / prev_close — lower wick (探底回升)
    rng               NUMERIC(10,6),  -- (high - low) / prev_close — daily range
    close_pos         NUMERIC(10,6),  -- (close - low) / (high - low) — close location in range [0, 1]

    -- prev-day reference gaps (prev_* = the previous VALID row per code)
    prev_close_gap    NUMERIC(10,6),  -- open / prev_close - 1 — the overnight gap (高开/低开)
    ret_1d            NUMERIC(10,6),  -- close / prev_close - 1 — the daily close-to-close return
    close_vs_prev_high NUMERIC(10,6), -- close / prev_high - 1 — breakout measure vs the prior day's high
    close_vs_prev_low  NUMERIC(10,6), -- close / prev_low - 1 — breakdown measure vs the prior day's low

    -- participation gauges (trading_amount vs its own trailing MA)
    amt_ratio_20      NUMERIC(10,6),  -- trading_amount / its trailing 20-row MA (incl. current) — the per-code 放量/缩量 gauge
    amt_ratio_252     NUMERIC(10,6),  -- trading_amount / its trailing 252-row MA (incl. current, min 60 obs) — the yearly participation level

    CONSTRAINT pk_ohlcv_ext PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'ohlcv_ext', 16);

COMMENT ON TABLE  analysis.ohlcv_ext IS 'OHLCV extended tech stats: one row per (code, sec_type, date) — intraday open/high/low/close gaps, wick depths, close position in range, prev-day reference gaps and trading-amount participation ratios. ESTIMATED-DATA EXCLUSION: source rows with is_ohl_estimated TRUE are excluded entirely (synthetic OHLC would zero the wick/range gaps); every prev_* reference points at the previous VALID row of the same code. ETFs use the adjusted series (total-return cross-date legs). All gap columns are signed fractions; NULL when any input is NaN or the denominator is zero/denormalized. Computed self-contained by python -m analysis.analysis_signals.signal_amplification from stats.{index,etf,stock}_basic_stats — parallels (does not read) the mov_ave_spread anatomy stores; gap conventions cited from analysis.mov_ave_spreads_detail_ohlc_gaps. Primary consumer: analysis_signals.signal_amplification (the OHLCV anatomy amplifiers).';
COMMENT ON COLUMN analysis.ohlcv_ext.sec_type IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf, 1 = index, 2 = stock.';
COMMENT ON COLUMN analysis.ohlcv_ext.code   IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000001"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.ohlcv_ext.date   IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.ohlcv_ext.gap_oc IS '(close - open) / open — the intraday leg: positive = 高走 (rose from the open), negative = 低走 (fell from the open). NULL when open is NULL or |open| < 1e-12.';
COMMENT ON COLUMN analysis.ohlcv_ext.gap_hc IS '(close - high) / high — how far the close sits below the day high (<= 0): the rejection depth (冲高回落 magnitude when combined with a small upper body). NULL when high is invalid.';
COMMENT ON COLUMN analysis.ohlcv_ext.gap_lc IS '(close - low) / low — how far the close sits above the day low (>= 0): the support depth. NULL when low is invalid.';
COMMENT ON COLUMN analysis.ohlcv_ext.uw     IS '(high - GREATEST(open, close)) / prev_close — upper wick in return units (冲高回落 spike-fade magnitude; 0 when the close is at/above the high). NULL when prev_close is invalid.';
COMMENT ON COLUMN analysis.ohlcv_ext.lw     IS '(LEAST(open, close) - low) / prev_close — lower wick in return units (探底回升 flush-recover magnitude; 0 when the close is at/below the low). NULL when prev_close is invalid.';
COMMENT ON COLUMN analysis.ohlcv_ext.rng    IS '(high - low) / prev_close — the day''s full range in return units. NULL when prev_close is invalid.';
COMMENT ON COLUMN analysis.ohlcv_ext.close_pos IS '(close - low) / (high - low) — close location within the day''s range: 0 = close at the low, 1 = close at the high. NULL when high = low.';
COMMENT ON COLUMN analysis.ohlcv_ext.prev_close_gap IS 'open / prev_close - 1 — the overnight gap (高开 positive / 低开 negative). prev_close = the previous VALID (non-estimated) row''s close of the same code ordered by date; NULL on the code''s first valid row.';
COMMENT ON COLUMN analysis.ohlcv_ext.ret_1d IS 'close / prev_close - 1 — the daily close-to-close return (prev_close per the valid-row chain). NULL on the code''s first valid row.';
COMMENT ON COLUMN analysis.ohlcv_ext.close_vs_prev_high IS 'close / prev_high - 1 — the close vs the PRIOR valid day''s high: positive = closed above yesterday''s high (the breakout side). NULL when prev_high is unavailable.';
COMMENT ON COLUMN analysis.ohlcv_ext.close_vs_prev_low IS 'close / prev_low - 1 — the close vs the PRIOR valid day''s low: negative = closed below yesterday''s low (the breakdown side). NULL when prev_low is unavailable.';
COMMENT ON COLUMN analysis.ohlcv_ext.amt_ratio_20 IS 'trading_amount / its trailing 20-row MA (including the current row) — the per-code 放量/缩量 gauge (> 1 expanding participation, < 1 shrinking). NULL under 20 valid rows or a denormalized MA.';
COMMENT ON COLUMN analysis.ohlcv_ext.amt_ratio_252 IS 'trading_amount / its trailing 252-row MA (including the current row, minimum 60 valid rows) — the participation level vs the yearly average. NULL under 60 valid rows.';

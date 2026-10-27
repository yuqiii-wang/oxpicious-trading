-- ============================================================================
--  static.docs seed — baselines (index/stock panels), live intraday
--  panels, strategy backtest/risk, code trend, signal-rules ask.
-- ============================================================================

DELETE FROM static.docs WHERE doc_id LIKE 'ui.index.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.stock.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.live.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.strategy.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.code-trend.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.signal-rules.ask%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.hedge-pairs.%';

INSERT INTO static.docs (doc_id, title, intro, series, notes, ordinal) VALUES
(
  'ui.index.panel',
  NULL,
  'Daily chart of index {{code}} {{name}}: {{#hasOhlc}}OHLC candles{{/hasOhlc}}{{^hasOhlc}}a close line{{/hasOhlc}} + MA5/20/60/120 on the left price axis (the weekly/monthly/quarterly/half-year anchors the ma-cross families trade), turnover bars (亿元) on the right, PE on an offset twin axis. Read price against the MA ladder — the MA120/255 regime line separates trending from mean-reverting tape; PE in its own historical tail (the 5y band edges) marks valuation stretch. % mode rebases OHLC + MAs to % change from the first valid close (Amount and PE stay absolute).',
  '[
    {"key": "ohlc", "name": "OHLC", "unit": "{{unit}}", "description": "daily candlesticks (open/high/low/close)"},
    {"key": "close", "name": "Close", "unit": "{{unit}}", "description": "daily closing price"},
    {"key": "ma5", "name": "MA5", "unit": "{{unit}}", "description": "5-day mean of close — the weekly anchor (ma20-cross retest watch)"},
    {"key": "ma20", "name": "MA20", "unit": "{{unit}}", "description": "20-day mean of close — the monthly anchor (Bollinger z numerator)"},
    {"key": "ma60", "name": "MA60", "unit": "{{unit}}", "description": "60-day mean of close — the quarterly swing anchor (ma60-cross / reclaim trigger)"},
    {"key": "ma120", "name": "MA120", "unit": "{{unit}}", "description": "120-day mean of close — the half-year regime line"},
    {"key": "amt", "name": "Trading Amt", "unit": "亿元", "description": "daily trading turnover (成交金额) — the participation behind the move"},
    {"key": "pe", "name": "PE", "description": "price/earnings ratio on the offset twin axis — read vs its own 5y percentile band, not as an absolute"}
  ]'::jsonb,
  '[
    {"key": "rebase", "text": "Percentage mode rebases OHLC + MAs to % change from the first valid close; Amount and PE are never rebased."}
  ]'::jsonb,
  0
),
(
  'ui.stock.panel',
  NULL,
  'Single-stock daily chart of {{code}} ({{name}}, {{industry}}): {{#hasOhlc}}candles (close line when sparse) {{/hasOhlc}}{{^hasOhlc}}close line {{/hasOhlc}}+ MA5/20/60/120 (the weekly→half-year anchor ladder the ma-cross families trade), amount bars on a twin axis (亿){{#hasPe}}, PE on an offset axis{{/hasPe}}, ex-dividend markers. A MA stack fanned upward = an entrenched uptrend (the reclaim events are the entry watches); ex-dividend price gaps are mechanical adjustments, not flow — read returns ex-dividend. % mode rebases prices + MAs to % change from the first valid close.',
  '[
    {"key": "ohlc", "name": "OHLC", "description": "daily open/high/low/close candles"},
    {"key": "close", "name": "Close", "unit": "元", "description": "daily closing price"},
    {"key": "ma5", "name": "MA5", "unit": "元", "description": "5-day mean of close — the weekly anchor"},
    {"key": "ma20", "name": "MA20", "unit": "元", "description": "20-day mean of close — the monthly anchor (Bollinger z numerator)"},
    {"key": "ma60", "name": "MA60", "unit": "元", "description": "60-day mean of close — the quarterly swing anchor (ma60-cross / reclaim trigger)"},
    {"key": "ma120", "name": "MA120", "unit": "元", "description": "120-day mean of close — the half-year regime line"},
    {"key": "amt", "name": "Amt (亿)", "unit": "亿", "description": "daily trading amount — the participation behind the move"},
    {"key": "pe", "name": "PE", "unit": "×", "description": "price/earnings ratio (SZSE stocks) — read vs its own trailing band"},
    {"key": "dividend", "name": "Dividend", "description": "ex-dividend dates — mechanical price adjustments, not flow"}
  ]'::jsonb,
  '[
    {"key": "rebase", "text": "Percentage mode rebases from the first VISIBLE close (zoom-dependent)."}
  ]'::jsonb,
  0
),
(
  'ui.live.intraday',
  NULL,
  '5-min intraday chart of {{code}} {{name}} on {{date}}: OHLC bars on a time axis pinned to 09:30–15:30 (lunch break 11:30–13:00 = gap; post-market bars excluded){{#hasVolume}}, volume bars on a twin axis{{/hasVolume}}. % mode rebases bars to % change from the first bar''s close (volume stays absolute). The read: volume bursts at session extremes = conviction behind the move; where the close sits in the day range = the carry into the next open (close at the high = continuation lean, at the low = gap-risk).',
  '[
    {"key": "ohlc", "name": "OHLC", "unit": "{{unit}}", "description": "5-minute candles (open/high/low/close)"},
    {"key": "volume", "name": "Volume", "unit": "shares", "description": "5-minute volume — the participation behind each bar"}
  ]'::jsonb,
  '[
    {"key": "rebase", "text": "Percentage mode rebases OHLC to % change from the first bar''s close; volume is not rebased."},
    {"key": "axis", "text": "The x-axis is pinned to trading hours 09:30–15:30 — the lunch break renders as a gap."},
    {"key": "barCount", "text": "{{n}} 5-minute bars on the day."}
  ]'::jsonb,
  0
),
(
  'ui.live.oi-skew',
  NULL,
  'Intraday OI-weighted moneyness skewness of {{underlying}} on {{date}}, on 5-minute bars: underlying spot (5-min closes) vs the OI-weighted moneyness skew price S × E[M], E[M] = Σ(OI·K/S)/Σ(OI) per expiry group (line width ∝ the group''s OI; the thick line blends all groups). Open interest per bar is ESTIMATED as the previous trading day''s daily OI plus the day-cumulative traded volume (an upper bound — closing trades are not netted out). The read: skew riding above spot = OI parked overhead (the supply zone spot chews through); below spot = support beneath; watch the skew crossing spot intraday as positioning flips.',
  '[
    {"key": "spot", "name": "Underlying Spot", "unit": "{{unit}}", "description": "5-minute close of the option''s underlying"},
    {"key": "skewMean", "name": "Skew (OI-wtd mean)", "unit": "{{unit}}", "description": "spot rebased by the all-expiry OI-weighted mean moneyness (sentinel expiry 9998-12-31 rows)"},
    {"key": "skewGroup", "name": "Skew <expiry>", "unit": "{{unit}}", "description": "per-expiry-group skew price; line width encodes the group''s OI"},
    {"key": "oiEst", "name": "Accum. OI (est.)", "unit": "contracts", "description": "Σ(prev-day OI base + day-cumulative traded volume) over all active contracts per 5-min bar — a final-bar jump of a whole day''s volume is the EOD-capture artifact"}
  ]'::jsonb,
  '[
    {"key": "serverSide", "text": "The series is computed server-side (python -m live.options_intraday_skewness) into live.options_intraday_skewness; the route spawns it on demand when missing/stale."},
    {"key": "sseBase", "text": "SSE contracts carry OI base 0 (no daily per-contract OI source) so their intraday OI is traded volume only; SZSE/CFFEX contracts hold the flat prev-day daily OI."}
  ]'::jsonb,
  0
),
(
  'ui.strategy.singleton',
  NULL,
  'Singleton strategy backtest for {{code}} (MA5/MA60 crossover, precomputed by strategy.singleton_trading): OHLC + MA5/MA60, amount bars, the Total P&L curve, and BUY/SELL/LAST-DAY-SELL markers at fill prices; price series are rebased so the FIRST BUY fill = 100. The read: the MA5/MA60 relationship is the trend signal (short over long = long bias); marker clusters show where the crossover acted — entries chasing strength, exits banking it; the P&L curve''s drawdowns are the pain budget the crossover must survive.',
  '[
    {"key": "ohlc", "name": "OHLC", "unit": "rebased (first BUY = 100)", "description": "daily candles, rebased to the first BUY fill"},
    {"key": "ma5", "name": "MA5", "unit": "rebased", "description": "5-day mean of close — the fast leg of the crossover signal"},
    {"key": "ma60", "name": "MA60", "unit": "rebased", "description": "60-day mean of close — the slow leg of the crossover signal"},
    {"key": "amt", "name": "Trading Amt", "unit": "元", "description": "daily trading amount on its own right axis (not rebased)"},
    {"key": "pnl", "name": "Total P&L", "unit": "元", "description": "total P&L curve on its offset axis — the equity curve whose drawdowns set the pain budget"},
    {"key": "buy", "name": "BUY", "description": "buy fills at the crossover''s buy decisions (dark = top-3 confidence)"},
    {"key": "sell", "name": "SELL", "description": "sell fills at the crossover''s sell decisions (dark = top-3 losses)"},
    {"key": "lastSell", "name": "LAST DAY SELL", "description": "the projected final-day liquidation fill"}
  ]'::jsonb,
  '[
    {"key": "rebase", "text": "Rebased axis: price series are normalized to the first BUY fill = 100."}
  ]'::jsonb,
  0
),
(
  'ui.strategy.risk',
  NULL,
  'Per-period strategy P&L (risk analytics): per period the worst intra-period unrealized MTM dip vs the peak gain (the period''s pain/gain envelope), the realized P&L (Σ SELLs, opacity = concentration), the Accumulated Total P&L equity curve (Σ realized + unrealized), and where the stress test exists the FT Amplified line. The read: deep unrealized dips behind flat realized = fragile periods (the pain was survived, not avoided); a widening FT gap vs the equity curve = the strategy thinning under amplified-fill stress.',
  '[
    {"key": "maxLoss", "name": "Max Unrealized Loss", "unit": "元", "description": "worst (most negative) intra-period MTM dip — the pain envelope"},
    {"key": "maxGain", "name": "Max Unrealized Gain", "unit": "元", "description": "peak (most positive) intra-period MTM gain — the gain envelope"},
    {"key": "realized", "name": "Realized P&L", "unit": "元", "description": "sum of SELL realized P&L per period"},
    {"key": "accum", "name": "Accumulated Total P&L", "unit": "元", "description": "cumulative realized + unrealized P&L (equity curve)"},
    {"key": "ft", "name": "FT Amplified P&L", "unit": "元", "description": "stress-test cumulative line — gap vs the equity curve = amplification cost"}
  ]'::jsonb,
  NULL, 0
),
(
  'ui.code-trend.panel',
  NULL,
  'Daily trend for {{code}} ({{secType}}): OHLC candles (close line when sparse) with MA5/20/60/120 overlays (the weekly→half-year anchor ladder the ma-cross families trade) and turnover bars; where data allows, margin fills (RZ cash borrow up / RQ sec borrow down) and a PE line on an offset axis, ex-dividend markers, and market-regime bands (calm ~71% of days / hot / panic / quiet — the splits every signal family conditions on). % mode expresses OHLC + MAs as % change from the first valid close. The read: price commanding the MA stack = trend regime (reclaim events are the entry watches); RZ swelling = leveraged chase, RQ swelling = short pressure — squeeze fuel either way.',
  NULL,
  '[
    {"key": "clientMAs", "text": "MAs are computed client-side from close; Amount bars are in 亿 (raw yuan / 1e8)."}
  ]'::jsonb,
  0
),
(
  'ui.signal-rules.ask',
  NULL,
  'Rule cells of {{name}} ({{secType}}) — one row per (sub signal · trend · regime · action) key of the picked family: the cell''s CONSTANT breach bar (self-adaptively picked per key on the code''s own history), its gate verdict and its measured evidence (episodes · blended forward return · hit). A cell fires when the live value pierces its bar from the trigger side under a matching day context; buys and sells are NOT mirror images — sharp falls revert (buy the capitulation), sharp rises continue (sells are exit timing). The metrics table resolves the cells'' INPUT values (trend state, the RSI 14/10/6 entry grid, the momentum axes) for this security at the latest close.',
  NULL,
  '[
    {"key": "measuredState", "text": "Rule cells are the CURRENT measured state (signal_rules is wiped and re-inserted per pipeline run) — there is no as-of replay; the metrics table resolves at the latest available close."},
    {"key": "confidence", "text": "Confidence is the cell''s measured dir_ave (the fixed 0/3/5/10/20d blend), depth- and sample-scaled — never a fitted curve; a ''Valid ✓'' cell cleared its side''s measured bar."}
  ]'::jsonb,
  0
),
(
  'ui.hedge-pairs.ratio',
  NULL,
  'Minimum-variance hedge ratio h² = −Cov(A,B)/Var(A) of the subject industry vs the benchmark industry over trailing 20/60/255-day windows, on the stride-grid dates. h² > 0 means the pair moves opposite (negative covariance — the hedge-grade anti-correlation regime, a minority of pairs): BUY the subject to hedge one unit long of the benchmark, at h² units; h² < 0 means positive covariance — short it instead. Market-offset mode strips the 沪深300 common factor from both sides first — the pure relative-value answer, and the mode the pair-hedge universe is screened in.',
  NULL,
  '[
    {"key": "strideGrid", "text": "Values appear only on stride-grid dates; null days are skipped, never zero-filled."}
  ]'::jsonb,
  0
),
(
  'ui.hedge-pairs.ratio.info',
  'Pair hedge ratio vs benchmark industry',
  'Both sides come from the nav''s pair selection — subject industry = the hedging instrument, benchmark industry = the position.
The plotted value is the minimum-variance hedge h² = −Cov(A,B) / Var(A) over trailing 20/60/255-day windows — how many units of the subject industry to hold per unit long of the benchmark industry. Above 0 = BUY the subject (negative covariance — the opposite-moves regime); below 0 = short it.
Raw mode uses the industries'' aggregate daily pct changes directly; Market offset subtracts the 沪深300 (000300) pct from both sides first — where the buy-to-hedge answer lives. Values appear only on stride-20 grid dates; null days are skipped, never zero-filled.',
  NULL, NULL, 0
),
(
  'ui.hedge-pairs.curves',
  NULL,
  'Hedge-pair portfolio: the two industries'' composite close curves (pool-all mean_close, each its own axis), with the broad-market benchmark rebased to 100 as reference. The pair qualifies when its trailing opposite score > 0.5 (market factor removed) — the anti-correlated-leg + broad-market trio the portfolio family trades: the entry is the trio wash (all three curves in their own RSI capitulation tail the same day — the market-wide wash bought through the negatively-correlated pair), marked per leg with each member''s own action.',
  NULL,
  '[
    {"key": "poolAggregate", "text": "Each leg''s curve is its industry''s pool-all aggregate (mean_close), not a single index."}
  ]'::jsonb,
  0
);

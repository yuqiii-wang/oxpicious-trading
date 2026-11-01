---
name: ohlcv
description: OHLCV data access and volume/price study conventions for the oxpicious-trading repo — where daily bars live (stats.v_index_daily etc.), what "volume" means (成交额 trading_amount), and the house semantics of the Chinese volume vocabulary (放量, 缩量, 量价背离, 天量/地量, 缩量弱反弹, 放量确认, 放量大反包). Use whenever studying daily OHLCV/K-line data, computing MA/RSI/MACD/momentum studies from the DB, or interpreting 放量/缩量 language in analysis output — even when the user just says "volume", "成交量", "成交额", or names a study like MA-spread or momentum confirmation.
---

# OHLCV data access + volume (放量/缩量) study conventions

Distilled from the 2026-10-29…31 deep-agent OHLCV study history (the
"MA-spread regime vs past year / momentum confirmation" runs on SSE
Composite, and the analysis-summary runs whose volume semantics they
encode) and verified against `database/sql/stats/` DDL.

## When this applies

ANY study that fetches or computes from daily OHLCV bars (index / etf /
stock / futures), and ANY interpretation of Chinese volume vocabulary
(放量, 缩量, 量价背离, 天量/地量, 缩量弱反弹, 放量确认/放量收复). The user
may just say "volume", "成交量", "成交额", "K线", "MA-spread", or "did
momentum confirm" — trigger then too.

## 1. Where daily OHLCV lives (verified against DDL)

- Index daily bars: **`stats.v_index_daily`** — the reconstructed view
  (`database/sql/stats/99_reconstruct_views.sql`) JOINing
  `stats.index_identity` + `stats.index_basic_stats` (OHLCV) +
  `stats.index_valuation` (pe) + `stats.index_tech_stats` (MAs/EMAs),
  keyed `(code, date)`, HASH-partitioned by code.
- There is NO `index_daily` table — that guess has been made (and
  self-corrected) in past study runs. Columns are `code` / `date` /
  `close`, NOT `ts_code` / `trade_date`.
- `stats.index_basic_stats`: `open, high, low, close`,
  `trading_shares` (交易量, shares), `trading_amount` (成交金额, yuan —
  source CSV 亿元 × 1e8), `change`, `change_pct`,
  `is_ohl_estimated`, `has_intraday_5mins`.
- `stats.index_tech_stats`: `ma5/ma20/ma60/ma120/255`, `ma5_ratio`,
  `ema6/12/20/26/60/120/255`, `trading_amt_per_pct_change` — use these
  precomputed MAs instead of recomputing when they cover the window.
- ETF / stock amount: `stats.etf_liquidity_margin` /
  `stats.stock_liquidity_margin` (both `trading_amount`). Futures:
  `stats.futures_basic_stats` (`trading_shares` = contracts, not shares).
- Code formats (CHECK on `index_identity`): `^(\d{6}|H\d{5}|[A-Z][A-Z0-9]{3,11})$`
  — SSE Composite is plain `000001`, no exchange suffix.

## 2. OHLCV data caveats

- **Volume means 成交额 (trading_amount, yuan), not share count**, unless
  the question explicitly asks 交易量 (`trading_shares`). All repo volume
  metrics (participation, market share, Bollinger on amount) are built on
  `trading_amount`.
- `is_ohl_estimated = TRUE` → open/high/low were synthesized
  (open = prev close, high = low = close) because CSIndex published
  close-only rows before 2024-10-24; treat O/H/L as ≈ close there.
- Calendar gaps (CNY / National Day, max ~11 days) are market closures,
  not missing data — never "fill" them.
- Fetch warmup history BEFORE the analysis window: one extra year for
  MA200/MA255 to be defined on the first studied day (the 2026-10-31
  study pulled from 2020-01-01 for a 2-year MA200 window). Index history
  starts 2020-01-01.
- Read side only: studies hit the read replica
  (`get_read_db_connection`), narrow aggregate queries; analysis runs
  through WSL conda per `oxpicious-dev-conventions`.

## 3. Volume vocabulary (放量 etc.) — house semantics

The repo's analysis summaries use these terms with SPECIFIC quantitative
meaning. Keep them consistent when writing or reading reports:

| Term | Meaning here | Quantitative gauge |
|---|---|---|
| 放量 (expansion) | participation ABOVE its own MA — buying/selling interest surging | `trading_amt_market_share_vs_ma{W}` > 0 (`analysis.mov_ave_trading_amt`): market share of 成交额 vs its own W-day MA (W=20 is the default quote) |
| 缩量 (contraction) | participation below its own MA — willingness shrinking | same gauge < 0 (e.g. −1.68% = "参与度衰减, 非恐慌") |
| 量价背离 | price move NOT confirmed by participation — credibility discounted | price down + share-vs-MA ≈ flat/negative → "下跌缺乏承接也缺乏抛压释放" |
| 放量下跌 | high-volume decline = panic selling / capitulation; sell pressure releasing | large positive share-gap on a down day |
| 缩量下跌 | low-volume decline = no panic, no support; grind-lower (阴跌) risk dominates | negative/flat share-gap on a down day |
| 缩量弱反弹 | rebound on shrinking participation — reliability discounted, fade-prone | rebound day with share-gap still negative |
| 放量确认 / 放量收复 MA20 | rebound confirmed by expanding participation — the required confirmation for trend-repair calls | rebound day with share-gap turning positive |
| 放量大反包 | high-volume engulfing reversal — bullish reversal marker | down day followed by up day that engulfs it on a volume spike |
| 天量 / 地量 | extreme-volume tails of own history | rarity percentile (top / bottom tail of ~10y daily series) |

- The participation gauge is market-share based (share of
  `stats.exchange_trading_amt.total_trading_amount` over primary
  exchanges), NOT raw amount — raw amount drifts with overall market
  activity; share isolates the security's own participation.
- 放量/缩量 judgements are always **relative to the security's own MA20
  (or stated window) or own history percentile** — never absolute yuan
  thresholds. Cross-check with the rarity-ranking convention (percentile
  vs ~10y own series) from `oxpicious-dev-conventions`.

## 4. Study playbook (as executed in the 2026-10-31 study)

**MA-spread regime study** ("how stretched is the regime vs the past
year"):
1. Fetch daily bars from `stats.v_index_daily` with warmup.
2. Compute MA20 / MA50 / MA200 (or reuse the view's MA5/20/60/120/255)
   and the spreads `price−MA50`, `MA20−MA50`, `MA50−MA200`.
3. For each spread report: current value, percentile rank within the
   trailing 252 trading days, window min/max, z-score.
4. Verdict: stretched / neutral / compressed, named directionally
   ("stretched to the downside"). Bearish alignment = Price < MA20 <
   MA50 < MA200.

**Momentum confirmation study** ("did momentum confirm the latest
move"):
1. Compute RSI(14) (zone: overbought >70 / oversold <30 / neutral),
   MACD(12,26,9) histogram (sign + 5-session trend), ROC(20).
2. Direction of the latest move from close-to-close over ~5–10 sessions.
3. Classify each indicator confirming / diverging vs that move; overall
   verdict ("momentum confirmed", with exhaustion caveats when RSI is
   deeply oversold while the histogram is fading).

**Output style**: value + percentile + z-score + verdict per metric, then
a synthesis paragraph; Chinese terms (放量/缩量) carry the §3 meanings.
The 2026-10-31 worked example produced: close 3,599.02, all three MA
spreads in the bottom ~2% of their 1-year distribution (z ≈ −1.8…−2.25),
RSI14 28.34, MACD hist −7.89, ROC20 −7.44% → "stretched down, momentum
confirms the down-move, exhaustion-prone".

## 5. Guardrails

- Study scope: default to the 10 named indices only, per
  `oxpicious-dev-conventions` — widen only when explicitly instructed.
- New dataframe compute follows `cudf-dtypes`; new tables/columns follow
  `db-smallint-type-codes` + `db-sql-write`; these route via
  `oxpicious-dev-conventions`.
- This skill is methodology only — it introduces no DB writes.

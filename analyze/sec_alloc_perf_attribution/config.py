"""Configuration constants for analyze.sec_alloc_perf_attribution.

Daily Composition Overlap + Liquidity + Rolling Correlations
(Index subjects x Index benchmarks).

Populates analysis.sec_alloc_perf_attribution with daily composition
overlap, ETF-market liquidity, and rolling close-price correlations for
Index subjects (ALL indices with composition data) vs ALL indices as
benchmarks (excl. self-pairs).

ETF subjects are currently BYPASSED — the ETF selection + return-fetch
code has been removed to keep this script focused on the index x index
cross product that the UI actually consumes. Re-introducing ETF subjects
would require porting select_etf_subjects / fetch_etf_returns back from
git history.

Subject / benchmark pairing:
  code            = bare index code (e.g. "000300") for sec_type='index'
  sec_type        = 'index' (determines source table for close prices)
  benchmark_code  = any index code from stats.index_identity (e.g. "000300")

Per-row fields:
  code_sec_shared_weight         = Sum w_subject   on stocks held by BOTH
  benchmark_sec_shared_weight    = Sum w_benchmark on stocks held by BOTH
    (Computed from latest snapshot in stats.sec_composition; same for all dates.)

  ETF-MARKET AMOUNT (liquidity view, NOT price attribution):
    benchmark_etf_trading_amount = stats.index_exts.total_etf_trading_amount for
                           benchmark_code on this date (precomputed by
                           build_index_exts.py).
    code_etf_trading_amount      = stats.index_exts.total_etf_trading_amount for
                           the subject index.
    etf_trading_amount_ratio_benchmark_to_code = GENERATED (not inserted) =
                           benchmark_etf_trading_amount / code_etf_trading_amount.
    etf_trading_amount_ratio_benchmark_to_code_ma5 = 5-trading-day moving average
                           of the ratio (computed in pandas, NOT generated).

  STATISTICAL ATTRIBUTION (rolling correlations):
    corr_5d/20d/60d/255d = rolling Pearson corr of subject vs benchmark close.
"""
ANALYSIS_NAME = "sec_alloc_perf_attribution"
TABLE = "analysis.sec_alloc_perf_attribution"

# Benchmark selection: keep ALL broad-market indices + top-N highest-traded
# non-broad indices (ranked by aggregate ETF turnover). Bounds the
# subject x benchmark cross product while retaining the most liquid
# sector/industry benchmarks.
TOP_N_NON_BROAD = 3

# Rolling-correlation windows (trading days). 255 ~= 1 year of trading
# days; 60 ~= 1 quarter. min_periods is set to max(2N/3, 3) so that
# up to 1/3 of the window can be NaN (handles benchmarks with
# occasional data gaps). Combined with close estimation in the
# build scripts (is_close_estimated), this eliminates most NULLs.
CORR_WINDOWS = (5, 20, 60, 255)

# Cap on |benchmark_etf_trading_amount / code_etf_trading_amount| mirrored
# from the SQL GENERATED column's NUMERIC(10,4) limit (max 999,999.9999).
# Ratios exceeding this cap — e.g. a tiny subject ETF turnover vs a large
# broad-market benchmark — are set to NULL in BOTH the SQL GENERATED
# column and the MA5 computation, so the two stay consistent. Without the
# cap, the GENERATED column would overflow on insert.
RATIO_CAP = 1_000_000

DESCRIPTION = (
    "Daily composition overlap + ETF-market liquidity + rolling close "
    "correlations for Index subjects vs all index benchmarks. Index subjects: "
    "only indices with composition data (44 CSI indices with closeweight "
    "CSVs) vs all indices (excl. self-pairs). For each (code, benchmark_code, "
    "date) tuple: code_sec_shared_weight and benchmark_sec_shared_weight "
    "computed from latest stats.sec_composition snapshot overlap (stocks held "
    "by BOTH subject and benchmark). "
    "ETF-MARKET AMOUNT (liquidity view): benchmark_etf_trading_amount = "
    "stats.index_exts.total_etf_trading_amount for benchmark_code on this date "
    "(precomputed by build_index_exts.py = Sum "
    "etf_liquidity_margin.trading_amount across ALL ETFs tracking "
    "benchmark_code via stats.sec_classification.parent_index_code); NULL "
    "when no ETF tracks the benchmark (e.g. 000001 上证指数 has no direct "
    "ETF). code_etf_trading_amount = stats.index_exts.total_etf_trading_amount for the subject "
    "index (same aggregation as benchmark_etf_trading_amount but keyed on subject "
    "code). Both in yuan; "
    "etf_trading_amount_ratio_benchmark_to_code (GENERATED ALWAYS column) = "
    "benchmark_etf_trading_amount / code_etf_trading_amount; its INVERSE is the subject's "
    "share of the benchmark ETF market. NOTE: this is a LIQUIDITY ratio, not "
    "a price-attribution proportion. "
    "STATISTICAL ATTRIBUTION: corr_5d/20d/60d/255d = Pearson correlation "
    "between subject and benchmark close prices over trailing N trading days "
    "(computed via pandas vectorized df.rolling(N, min_periods=max(2N/3, 3))."
    "corr(series) against a wide-format benchmark-close pivot; min_periods="
    "2N/3 allows up to 1/3 of window data missing). "
    "Subject close = index_basic_stats.close. "
    "NOTE: benchmark indices WITHOUT composition data are still included as "
    "benchmarks; only their shared_weight columns are NULL."
)

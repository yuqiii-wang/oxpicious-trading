"""builds/options/sse/builders.py — Pure SSE options source-frame builders.

Batch D code motion: these builder functions and their path/column
constants moved verbatim (byte-identical bodies) out of ``__main__.py``,
which now imports them and keeps only the ``SseOptionsBuild`` entry class
(conn-first DB helpers live there as methods). The path constants resolve
identically here — ``dirname(__file__)`` is the same package directory the
``__main__`` module occupied. Importing this module does NOT activate
cudf.pandas; the entry ``__main__.py`` bootstraps first and imports these
builders afterwards (same pattern as ``builds.options.cffex.loader``).
"""

import datetime
import os

import numpy as np
import pandas as pd

from downloads._common import read_csv_preferred
from downloads._common.exchanges.sse_options import OPTIONS_CSV_COLUMNS
from _common.df_utils import safe_columns, grouped_shift, compute_iv_and_greeks
from _common.labels import OPTION_TYPE_CODE

from _common.build_commons import ymd_from_filename
from builds._commons.safe_parse import safe_to_numeric, safe_to_datetime
from _common.log_setup import setup_logging

logger = setup_logging("sse_options")


# ============================================================================
# Paths
# ============================================================================
SSE_OPTIONS_DAILY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_options_daily")
SSE_OPTIONS_INTRADAY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_options_intraday")
SSE_OPTIONS_PRICE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_options_price")
SSE_OPTIONS_RISK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_options_risk")
SSE_TREND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_trend")
SSE_ETF_ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_archive")


def _resolve(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


SSE_OPTIONS_DAILY_DIR = _resolve(SSE_OPTIONS_DAILY_DIR)
SSE_OPTIONS_PRICE_DIR = _resolve(SSE_OPTIONS_PRICE_DIR)
SSE_OPTIONS_RISK_DIR = _resolve(SSE_OPTIONS_RISK_DIR)
SSE_TREND_DIR = _resolve(SSE_TREND_DIR)
SSE_ETF_ARCHIVE_DIR = _resolve(SSE_ETF_ARCHIVE_DIR)


# The 5 SSE option underlying ETFs — the per-code dayk close archives under
# temps/sse_archive/ use these bare codes as filenames.
SSE_OPTIONS_UNDERLYING_CODES = ("510050", "510300", "510500", "588000", "588080")


# ============================================================================
# Source frames
# ============================================================================


def backfill_daily_from_intraday(build_ymd, verbose=True):
    """Materialize missing daily CSVs from the intraday raw archives.

    Backfill fix for build dates whose EOD capture never landed a
    sse_options_daily file: the intraday archive's LAST sample per
    contract already holds the day-cumulative 成交量/成交额 at that moment,
    so one dedup pass writes the day's daily row. No external requests —
    intraday aggregation is the only backfill source. Partial-day archives
    (stream died pre-close) yield partial totals; the written
    ``update_time`` keeps that visible.
    """
    made = []
    for ymd in sorted(build_ymd):
        daily_path = os.path.join(SSE_OPTIONS_DAILY_DIR, f"sse_options_daily_{ymd}.csv")
        if os.path.exists(daily_path):
            continue
        intraday_path = os.path.join(SSE_OPTIONS_INTRADAY_DIR, f"sse_options_intraday_{ymd}.csv")
        if not os.path.exists(intraday_path):
            continue
        try:
            df = read_csv_preferred(intraday_path, dtype={"code": str})
        except Exception:
            continue
        if df is None or len(df) == 0 or "code" not in safe_columns(df):
            continue
        df = df.sort_values("update_time", kind="stable")
        df = df.drop_duplicates(subset=["code"], keep="last")
        cols = [c for c in OPTIONS_CSV_COLUMNS if c in safe_columns(df)]
        df = df[cols]
        # cuDF's CSV writer emits full float64 reprs (0.09899999999999999) —
        # round the numeric legs back to quoted precision for clean files
        for c in ("open", "high", "low", "last", "prev_settle", "chg_rate",
                  "volume", "amount"):
            if c in safe_columns(df):
                df[c] = df[c].round(6)
        df.to_csv(daily_path, index=False, encoding="utf-8-sig")
        made.append(ymd)
        if verbose:
            logger.info(f"    [BACKFILL] {ymd}: daily CSV reconstructed from intraday — "
                  f"{len(df)} contracts, last sample {df['update_time'].max()}")
    return made


def build_quotes_df(files, verbose=True):
    """EOD quotes: the day's DAILY snapshot rows (price page, post-close).

    Each file is one trading day's EOD capture (day OHLC + day-total
    成交量(手)/成交额(元), raw cumulative values); a re-run capture appends
    duplicate rows, so the LAST sample per contract wins. close = ``last``,
    day volume = ``volume`` (张), amount = ``amount`` (元),
    prev_settle = ``prev_settle``, pct_change = ``chg_rate``.
    """
    if verbose:
        logger.info(f"    [OPTIONS] reading {len(files)} sse_options_daily_*.csv files")

    frames: list[pd.DataFrame] = []
    n_empty = 0
    n_ok = 0

    for path in files:
        ymd = ymd_from_filename(path, "sse_options_daily_")
        if not ymd:
            continue
        try:
            df = read_csv_preferred(path, dtype={"code": str})
        except Exception:
            n_empty += 1
            continue
        if df is None or len(df) == 0 or "code" not in safe_columns(df):
            n_empty += 1
            continue

        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        trade_date_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")

        # Last sample per contract = EOD state (idempotent re-captures append)
        df = df.sort_values("update_time", kind="stable")
        df = df.drop_duplicates(subset=["code"], keep="last")

        part = pd.DataFrame({
            "trade_code": df["code"].astype(str).str.strip(),
            "date": np.datetime64(trade_date_dt.date(), "ns"),
        })
        for src, dst in (
            ("last", "close"),
            ("prev_settle", "prev_settle"),
            ("chg_rate", "pct_change"),
            ("volume", "volume"),
            ("amount", "amount"),
        ):
            part[dst] = safe_to_numeric(df[src]) if src in safe_columns(df) else np.nan
        frames.append(part)
        n_ok += 1

    if not frames:
        if verbose:
            logger.info(f"    [OPTIONS] {n_ok} files with data, {n_empty} empty, 0 rows")
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    if verbose:
        logger.info(f"    [OPTIONS] {n_ok} files with data, {n_empty} empty, {len(out):,} EOD quote rows")
    return out


def build_contract_meta_df(files, verbose=True):
    """Contract listing (当日合约): numeric code, type, strike, expiry, underlying.

    Uses the authoritative columns directly (类型 认购/认沽, 行权价, 到期日,
    标的券名称及代码) — SSE 合约简称 patterns (50ETF/科创50/科创板50…) are not
    name-parseable the way SZSE's are.
    """
    if verbose:
        logger.info(f"    [CONTRACTS] reading {len(files)} sse_options_price_*.csv files")

    frames: list[pd.DataFrame] = []
    n_empty = 0

    for path in files:
        ymd = ymd_from_filename(path, "sse_options_price_")
        if not ymd:
            continue
        try:
            df = read_csv_preferred(
                path,
                dtype={"合约编码": str, "合约交易代码": str, "合约简称": str, "标的券名称及代码": str},
            )
        except Exception:
            n_empty += 1
            continue
        if df is None or len(df) == 0 or "合约交易代码" not in safe_columns(df):
            n_empty += 1
            continue

        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        trade_date_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")

        under = df["标的券名称及代码"].astype(str).str.strip()
        # "50ETF(510050)" / "科创50(588000)" → name + bare 6-digit code
        under_name = under.str.replace(r"\(\d{6}\)$", "", regex=True)
        under_code = under.str.extract(r"\((\d{6})\)", expand=False)

        part = pd.DataFrame({
            "date": np.datetime64(trade_date_dt.date(), "ns"),
            "contract_code": df["合约编码"].astype(str).str.strip(),
            "contract_name": df["合约简称"].astype(str).str.strip(),
            "trade_code": df["合约交易代码"].astype(str).str.strip(),
            # 认购 = right to buy → CALL; 认沽 → PUT — stored as the
            # static.label_dim 'option_type' SMALLINT code (0 CALL / 1 PUT)
            "option_type": np.where(
                df["类型"].astype(str).str.strip().eq("认购"),
                OPTION_TYPE_CODE["CALL"], OPTION_TYPE_CODE["PUT"],
            ),
            "underlying_name": under_name,
            "underlying_code": under_code,
        })
        # strike in 厘 (×1000 yuan) — same ×1000 convention as options_strike
        part["strike_price_raw"] = (safe_to_numeric(df["行权价"]) * 1000.0).round(2)
        part["strike_price"] = part["strike_price_raw"]
        # contractid series marker at index 11 ('M' standard, 'A' adjusted)
        tc = part["trade_code"]
        part["has_a_suffix"] = tc.str.slice(11, 12).eq("A").astype(int)
        # 前结算价 (previous settlement, yuan) — the listing's own price
        # column; feeds listing-only quotes and the settle(T+1) recovery
        part["prev_settle"] = (safe_to_numeric(df["前结算价"])
                               if "前结算价" in safe_columns(df) else np.nan)
        # 到期日 YYYYMMDD → datetime64 (authoritative expiry; no weekday math)
        # Clean-data fast path — to_datetime(errors="coerce") on a Series
        # is a cudf fallback per call (non-scalar arg); coerce only on junk.
        expiry_str = df["到期日"].astype(str).str.strip()
        try:
            part["expiry_date"] = pd.to_datetime(expiry_str, format="%Y%m%d")
        except (ValueError, TypeError):
            part["expiry_date"] = pd.to_datetime(
                expiry_str, format="%Y%m%d", errors="coerce")
        frames.append(part)

    if not frames:
        if verbose:
            logger.info(f"    [CONTRACTS] 0 usable files ({n_empty} empty)")
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out = out.dropna(subset=["expiry_date", "underlying_code", "contract_code", "strike_price_raw"])
    # strike_str display label ("2650" / "2850A") from the 厘 strike + series marker
    out["strike_str"] = out["strike_price_raw"].round(0).astype("int64").astype(str).str.cat(
        out["has_a_suffix"].map({1: "A", 0: ""}))
    if verbose:
        logger.info(f"    [CONTRACTS] {len(frames)} files with data, {n_empty} empty, {len(out):,} contract rows")
    return out


def build_settle_map(meta_df, verbose=True):
    """Recover official settle(T) from the next listing date's 前结算价.

    The 当日合约 listing carries 前结算价 = the PREVIOUS trading day's
    settlement, so within one contract's listing-date sequence the row at
    T+1 holds date T's official settle (SSE publishes settlement with a
    one-day lag — the same fact that makes the intraday ``prev_settle``
    and the listing column agree). Contracts absent from every later
    listing (expiry-day drops, delisted) keep settle = NaN.

    ``meta_df`` must span the build dates AND all later listing files so
    the look-ahead row exists; only (date, contract_code, settle) comes
    back, left-merged onto the options frame by the caller.
    """
    if meta_df is None or len(meta_df) == 0 or "prev_settle" not in safe_columns(meta_df):
        return pd.DataFrame()

    keys = meta_df[["contract_code", "date", "prev_settle"]].sort_values(
        ["contract_code", "date"], kind="stable")
    # sort=False: already sorted on the FULL key above — grouped_shift's own
    # group-key-only sort would not preserve within-group date order
    keys = grouped_shift(keys, ["contract_code"], "prev_settle",
                         out_names="settle", periods=-1, sort=False)
    out = keys[["date", "contract_code", "settle"]]
    if verbose:
        n = int(out["settle"].notna().sum())
        logger.info(f"    [SETTLE] next-listing settle recovered for {n:,}/{len(out):,} contract-days")
    return out


def build_listing_only_quotes(meta_df, ymd_set, verbose=True):
    """Settle-only EOD rows for build dates lacking a final quotes file.

    The intraday stream is younger than the 当日合约 listing and crashes
    can cut a day before 15:00, so those dates build from the listing
    alone: full terms/strike/identity, ``prev_settle`` from the listing's
    前结算价, settle via ``build_settle_map``. close/pct_change stay NaN
    (no source) and volume is 0 — IV prices off settle.
    """
    if meta_df is None or len(meta_df) == 0 or not ymd_set:
        return pd.DataFrame()

    ymd_key = meta_df["date"].dt.strftime("%Y%m%d")
    lo = meta_df[ymd_key.isin(ymd_set)]
    if len(lo) == 0:
        return pd.DataFrame()

    part = pd.DataFrame({
        "trade_code": lo["trade_code"].astype(str).str.strip(),
        "date": lo["date"],
        "close": np.nan,
        "prev_settle": lo["prev_settle"],
        "pct_change": np.nan,
        "volume": 0.0,
    })
    if verbose:
        n_days = int(lo["date"].dt.strftime("%Y%m%d").nunique())
        logger.info(f"    [OPTIONS] {n_days} listing-only dates → {len(part):,} settle-only quote rows")
    return part


def _load_archive_etf_close():
    """Full-history closes for the option underlying ETFs from dayk archives.

    temps/sse_archive/{code}_trend.csv (downloads.stock.sse.archive, yunhq
    ``sh1/dayk`` endpoint) carries each security's complete immutable daily
    history — used to fill (etf_code, date) pairs the sse_trend_etf daily
    snapshots don't cover. Dates stay strings so the shared tail's single
    safe_to_datetime parse handles snapshot + archive rows alike.
    """
    parts: list = []
    for code in SSE_OPTIONS_UNDERLYING_CODES:
        path = os.path.join(SSE_ETF_ARCHIVE_DIR, f"{code}_trend.csv")
        if not os.path.exists(path):
            continue
        try:
            df = read_csv_preferred(path, dtype={"证券代码": str})
        except Exception:
            continue
        if df is None or len(df) == 0 or "今收" not in safe_columns(df):
            continue
        code_s = df["证券代码"].astype(str).str.strip().str.replace(".SS", "", regex=False)
        close_s = safe_to_numeric(df["今收"])
        date_s = df["交易日期"].astype(str).str.strip()
        m = (code_s.str.fullmatch(r"\d{6}") & close_s.notna()).to_numpy()
        if not bool(m.any()):
            continue
        part = pd.DataFrame({"etf_code": code_s[m]})
        part["date"] = date_s[m]
        part["etf_close"] = close_s[m].astype(float)
        parts.append(part)
    return parts


def load_etf_close(files, verbose=True):
    """Underlying ETF closes (今收) → moneyness.

    Primary: sse_trend_etf_*.csv same-day snapshots. The per-code dayk
    archives fill any (etf_code, date) pair the snapshots lack — the
    snapshot downloader started 2026-07-31, the options listings
    2026-07-17, and moneyness/IV need the spot on every build date.
    """
    if verbose:
        logger.info(f"    [ETF-CLOSE] reading {len(files)} sse_trend_etf_*.csv files")

    parts: list = []
    for path in files:
        ymd = ymd_from_filename(path, "sse_trend_etf_")
        if not ymd:
            continue
        try:
            df = read_csv_preferred(path, dtype={"证券代码": str})
        except Exception:
            continue
        if df is None or len(df) == 0 or "今收" not in safe_columns(df):
            continue
        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        code_s = df["证券代码"].astype(str).str.strip().str.replace(".SS", "", regex=False)
        close_s = safe_to_numeric(df["今收"])
        m = (code_s.str.fullmatch(r"\d{6}") & close_s.notna()).to_numpy()
        if not bool(m.any()):
            continue
        part = pd.DataFrame({"etf_code": code_s[m]})
        part["date"] = date_str
        part["etf_close"] = close_s[m].astype(float)
        parts.append(part)

    parts.extend(_load_archive_etf_close())

    if not parts:
        if verbose:
            logger.warning("    [WARN] No ETF close data loaded — moneyness will be 0")
        return pd.DataFrame()

    out = pd.concat(parts, ignore_index=True)
    out["date"] = safe_to_datetime(out["date"]).astype("datetime64[ns]")
    # archive rows only fill gaps — the same-day snapshot wins on collision
    out = out.drop_duplicates(subset=["etf_code", "date"], keep="first")
    out = out.dropna(subset=["date"]).sort_values(["etf_code", "date"]).reset_index(drop=True)
    if verbose:
        logger.info(f"    → {len(out):,} ETF close rows  ·  {out['etf_code'].nunique()} ETFs")
    return out


# ============================================================================
# Exchange risk-indicator table (期权风险指标) — published greeks loaded
# DIRECTLY, never recomputed (see add_derived_columns)
# ============================================================================
# downloadRisk.do emits comma-separated fields padded with tabs/spaces and a
# trailing separator per row — read positional, then strip every field.
_RISK_SOURCE_COLS = ["日期", "合约编码", "交易代码", "合约简称",
                     "Delta", "Theta", "Gamma", "Vega", "Rho"]
_RISK_COLS = ["日期", "合约编码", "Delta", "Theta", "Gamma", "Vega", "Rho"]


def load_risk_indicators(files, verbose=True):
    """Per-contract published greeks from sse_options_risk_*.csv.

    Returns [date, contract_code, r_delta, r_theta, r_gamma, r_vega, r_rho]
    keyed by the CSV's own 日期/合约编码 (authoritative; the numeric
    合约编码 matches options_identity.contract_code).
    """
    if verbose:
        logger.info(f"    [RISK] reading {len(files)} sse_options_risk_*.csv files")

    frames: list[pd.DataFrame] = []
    n_empty = 0
    for path in files:
        try:
            df = read_csv_preferred(
                path,
                header=None, skiprows=1,
                names=_RISK_SOURCE_COLS + ["_pad"], usecols=_RISK_COLS,
                dtype={"合约编码": str},
            )
        except Exception:
            n_empty += 1
            continue
        if df is None or len(df) == 0:
            n_empty += 1
            continue

        date_s = df["日期"].astype(str).str.strip().str.replace("-", "", regex=False)
        code_s = df["合约编码"].astype(str).str.strip()
        keep = code_s.str.fullmatch(r"\d+")
        part = pd.DataFrame({
            "date": pd.to_datetime(date_s[keep], format="%Y%m%d", errors="coerce"),
            "contract_code": code_s[keep],
        })
        # normalize to the [ns] unit the options frame's date column uses —
        # datetime-key merges across [us]/[ns] units do not match
        part["date"] = part["date"].astype("datetime64[ns]")
        for col in ("Delta", "Theta", "Gamma", "Vega", "Rho"):
            part["r_" + col.lower()] = safe_to_numeric(df[col].astype(str).str.strip())
        part = part.dropna(subset=["date"])
        frames.append(part)

    if not frames:
        if verbose:
            logger.info(f"    [RISK] 0 usable files ({n_empty} empty) — greeks fall back to BS pipeline")
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["date", "contract_code"], keep="last")
    if verbose:
        logger.info(f"    [RISK] {len(frames)} files with data, {n_empty} empty, {len(out):,} published greek rows")
    return out


# ============================================================================
# Accumulated-volume OI chain — SSE's open_interest estimator (no published
# per-contract OI; volume only exists in the intraday captures)
# ============================================================================


def chain_open_interest(df, seed_df=None, verbose=True):
    """open_interest(T) = open_interest(prev trading row) + volume(T).

    SSE publishes no per-contract OI, so the accepted approximation is the
    per-contract accumulated VOLUME (the intraday stream is the only
    volume source — the same upper-bound estimator
    ``live.options_intraday_oi`` chains from this column as oi_base).
    Within the build window this is one grouped cumsum over volume seeded
    with each contract's latest stored OI before the window; listing-only
    dates (volume 0) carry the level forward, contracts past expiry keep
    their frozen value only on rows that still exist for them.
    """
    if df is None or len(df) == 0:
        return df

    work = df[["contract_code", "date", "volume"]].sort_values(
        ["contract_code", "date"], kind="stable")
    if seed_df is not None and len(seed_df):
        work = work.merge(seed_df, on="contract_code", how="left")
    else:
        work = work.assign(open_interest=np.nan)
    # seed is constant per contract: seed + cumsum(volume) adds it exactly
    # once at the chain head of every row's running total; volume NaN
    # (no quote leg) counts as 0 — the level carries forward
    work["volume"] = work["volume"].fillna(0.0)
    seed = work["open_interest"].fillna(0.0)
    work["_oi"] = seed + work.groupby("contract_code", sort=False)["volume"].cumsum()

    if verbose:
        n_pos = int((work["_oi"] > 0).sum())
        logger.info(f"    [OI-CHAIN] accumulated-volume OI: {n_pos:,}/{len(work):,} "
              f"contract-days carry a positive level")
    return df.merge(
        work[["contract_code", "date", "_oi"]].rename(columns={"_oi": "open_interest"}),
        on=["contract_code", "date"], how="left")


# ============================================================================
# Derived columns (mirrors builds.options.szse.add_derived_columns; SSE gaps:
# settle NULL → IV priced off close; open_interest = accumulated volume)
# ============================================================================


def add_derived_columns(df, etf_close=None, risk_df=None, verbose=True):
    """Add normalized prices, moneyness, per-underlying aggregates, IV, Greeks."""
    if df is None or len(df) == 0:
        return df

    df = df.copy()
    df["_date_key"] = df["date"].dt.strftime("%Y-%m-%d")

    # settle arrives from build_settle_map (next listing's 前结算价);
    # dates/contracts without a later listing keep NULL and price IV off
    # close below (the fillna order encodes that preference)
    if "settle" not in safe_columns(df):
        df["settle"] = np.nan
    df["volume_wan"] = df["volume"] / 10000.0
    # open_interest arrives chained (accumulated-volume estimator — see
    # chain_open_interest); the guard keeps direct add_derived callers
    # working. NOT NULL column → 0, never NaN.
    if "open_interest" not in safe_columns(df):
        df["open_interest"] = 0.0
    df["open_interest_wan"] = df["open_interest"] / 10000.0

    df["prev_settle_norm"] = df["prev_settle"] / 100.0
    df["close_norm"] = df["close"] / 100.0
    df["settle_norm"] = df["settle"] / 100.0

    if etf_close is not None and len(etf_close) > 0:
        etf = etf_close.copy()
        etf["_date_key"] = etf["date"].dt.strftime("%Y-%m-%d")
        df = df.merge(etf[["_date_key", "etf_code", "etf_close"]],
                      left_on=["_date_key", "underlying_code"],
                      right_on=["_date_key", "etf_code"], how="left")
        uc = df["etf_close"].fillna(0.0)
        # strikes live on the exchange's ×1000 (厘) naming scale — rescale the
        # yuan ETF close (<50) onto it so moneyness / IV are meaningful
        df["underlying_close"] = np.where((uc > 0) & (uc < 50), uc * 1000.0, uc)
        df["moneyness_ratio"] = np.where(df["underlying_close"] > 0,
                                         df["strike_price"] / df["underlying_close"], 0.0)
    else:
        df["underlying_close"] = 0.0
        df["moneyness_ratio"] = 0.0

    # days_to_expiry via epoch ints (cudf-safe; no .dt.date object column)
    date_epoch = df["date"].astype("datetime64[ns]").astype("int64") // 86_400_000_000_000
    exp_epoch = df["expiry_date"].astype("datetime64[ns]").astype("int64") // 86_400_000_000_000
    df["days_to_expiry"] = (exp_epoch - date_epoch).clip(lower=0)

    # expiry_month display label ("9月" / "12月") — month formatting on the
    # tiny unique-expiry frame host-side, merged back on an INT epoch-day key
    # (datetime-key merges between host and cudf-backed frames trip the
    # "all inputs must be Index" concat bug — same reason _date_key exists)
    df["_exp_day"] = exp_epoch
    uniq_exp = df[["_exp_day"]].drop_duplicates()
    if hasattr(uniq_exp, "to_pandas"):
        uniq_exp = uniq_exp.to_pandas()
    # host-side month labels from epoch-day ints (Arrow-backed setitem on the
    # to_pandas() frame breaks; the unique set is ≤ a handful of dates)
    _label_by_day = {
        int(d): f"{(datetime.date(1970, 1, 1) + datetime.timedelta(days=int(d))).month}月"
        for d in np.asarray(uniq_exp["_exp_day"]).tolist()
    }
    uniq_exp = uniq_exp.assign(
        expiry_month=[_label_by_day[int(d)] for d in np.asarray(uniq_exp["_exp_day"]).tolist()]
    )
    df = df.merge(uniq_exp, on="_exp_day", how="left")

    grouped = df.groupby(["_date_key", "underlying_code"])

    df["total_volume_underlying"] = grouped["volume"].transform("sum")
    df["total_oi_underlying"] = grouped["open_interest"].transform("sum").fillna(0.0)

    df["volume_pct"] = np.where(df["total_volume_underlying"] > 0,
                                df["volume"] / df["total_volume_underlying"] * 100.0, 0.0)
    df["open_interest_pct"] = np.where(df["total_oi_underlying"] > 0,
                                       df["open_interest"] / df["total_oi_underlying"] * 100.0, 0.0)

    _grp_keys = ["_date_key", "underlying_code", "strike_price"]
    call_oi = df[df["option_type"] == OPTION_TYPE_CODE["CALL"]].groupby(_grp_keys)["open_interest"].sum().reset_index().rename(columns={"open_interest": "call_oi"})
    put_oi = df[df["option_type"] == OPTION_TYPE_CODE["PUT"]].groupby(_grp_keys)["open_interest"].sum().reset_index().rename(columns={"open_interest": "put_oi"})
    call_vol = df[df["option_type"] == OPTION_TYPE_CODE["CALL"]].groupby(_grp_keys)["volume"].sum().reset_index().rename(columns={"volume": "call_vol"})
    put_vol = df[df["option_type"] == OPTION_TYPE_CODE["PUT"]].groupby(_grp_keys)["volume"].sum().reset_index().rename(columns={"volume": "put_vol"})
    ratios = pd.merge(call_oi, put_oi, on=_grp_keys, how="outer")
    ratios = pd.merge(ratios, call_vol, on=_grp_keys, how="outer")
    ratios = pd.merge(ratios, put_vol, on=_grp_keys, how="outer")
    ratios["oi_call_put_ratio"] = np.where(ratios["put_oi"] > 0, ratios["call_oi"] / ratios["put_oi"], np.nan)
    ratios["vol_call_put_ratio"] = np.where(ratios["put_vol"] > 0, ratios["call_vol"] / ratios["put_vol"], np.nan)

    df = df.merge(ratios[["_date_key", "underlying_code", "strike_price",
                          "oi_call_put_ratio", "vol_call_put_ratio"]],
                  on=_grp_keys, how="left")
    df["oi_call_put_ratio"] = df["oi_call_put_ratio"].fillna(0.0)
    df["vol_call_put_ratio"] = df["vol_call_put_ratio"].fillna(0.0)

    df["open_interest_call"] = np.where(df["option_type"] == OPTION_TYPE_CODE["CALL"], df["open_interest"], 0.0)
    df["open_interest_put"] = np.where(df["option_type"] == OPTION_TYPE_CODE["PUT"], df["open_interest"], 0.0)
    df["volume_call"] = np.where(df["option_type"] == OPTION_TYPE_CODE["CALL"], df["volume"], 0.0)
    df["volume_put"] = np.where(df["option_type"] == OPTION_TYPE_CODE["PUT"], df["volume"], 0.0)
    call_oi_total = df.groupby(["_date_key", "underlying_code"])["open_interest_call"].transform("sum")
    put_oi_total = df.groupby(["_date_key", "underlying_code"])["open_interest_put"].transform("sum")
    df["oi_total_call_put_ratio"] = np.where(put_oi_total > 0, call_oi_total / put_oi_total, 0.0)

    if verbose:
        logger.info(f"    → Computing implied volatility and Greeks for {len(df):,} rows …")

    # IV calibration price: settle when recovered (official), else close
    # (settle publishes with a one-day lag — see build_settle_map)
    iv_frame = df.assign(settle=df["settle"].fillna(df["close"]))
    iv, delta, theta, gamma, vega, rho = compute_iv_and_greeks(iv_frame)
    df["implied_vol"] = iv
    df["delta"] = delta
    df["theta"] = theta
    df["gamma"] = gamma
    df["vega"] = vega
    df["rho"] = rho

    # Exchange-published risk indicators WIN over the model values — loaded
    # from the 期权风险指标 table directly, never recomputed. The BS values
    # above stay only as the fallback for dates/contracts the table lacks
    # (implied_vol has no published SSE source and stays model-computed).
    if risk_df is not None and len(risk_df) > 0:
        df = df.merge(risk_df, on=["date", "contract_code"], how="left")
        n_published = int(df["r_delta"].notna().sum())
        for pub, col in (
            ("r_delta", "delta"), ("r_theta", "theta"), ("r_gamma", "gamma"),
            ("r_vega", "vega"), ("r_rho", "rho"),
        ):
            df[col] = df[pub].fillna(df[col])
        df = df.drop(columns=["r_delta", "r_theta", "r_gamma", "r_vega", "r_rho"])
        if verbose:
            logger.info(f"    [RISK] published greeks loaded for {n_published:,}/{len(df):,} rows "
                        f"(rest: BS fallback / NULL)")

    df = df.round({
        "prev_settle_norm": 4, "close_norm": 4, "settle_norm": 4,
        "volume_wan": 4,
        "volume_pct": 4,
        "oi_call_put_ratio": 4, "vol_call_put_ratio": 4,
        "oi_total_call_put_ratio": 4,
        "implied_vol": 4, "delta": 6, "theta": 6, "gamma": 6, "vega": 6, "rho": 6,
    })

    if verbose:
        logger.info("    → Derived columns added: moneyness, ratios, normalized prices, IV, Greeks")

    return df


# ============================================================================
# Missing-date detection (exchange column on every options_* table)
# ============================================================================


def _newest_listing_underlyings(meta_files) -> set:
    """Underlying codes of the newest 当日合约 file — the current SSE universe.

    Keeps missing-date detection honest about brand-new underlyings: a code
    with no options_terms rows at all would stay invisible to a
    table-derived universe.
    """
    newest = max(
        (f for f in meta_files
         if ymd_from_filename(f, "sse_options_price_")),
        key=lambda f: ymd_from_filename(f, "sse_options_price_") or "",
        default=None,
    )
    if newest is None:
        return set()
    try:
        df = read_csv_preferred(newest, dtype={"标的券名称及代码": str})
    except Exception:
        return set()
    if df is None or len(df) == 0 or "标的券名称及代码" not in safe_columns(df):
        return set()
    under = df["标的券名称及代码"].astype(str).str.strip()
    return set(under.str.extract(r"\((\d{6})\)", expand=False).dropna())


# ============================================================================
# EOD-finality check (quotes CSV reached the 15:00 snapshot)
# ============================================================================


def _quotes_file_is_final(path) -> bool:
    """True if the day's raw-snapshot CSV's last update_time is >= 15:00."""
    try:
        df = read_csv_preferred(path, usecols=["update_time"])
    except Exception:
        return False
    if df is None or len(df) == 0:
        return False
    max_t = str(df["update_time"].max())
    try:
        return max_t[11:13] >= "15"
    except (IndexError, TypeError):
        return False


def _scan_final_quote_ymds(quote_files, meta_ymds, forced_date):
    """(final daily-file ymds, non-final count) over the CURRENT file set.

    Runs AFTER ``backfill_daily_from_intraday`` so regenerated daily files
    count as quote sources for their dates. --date rebuilds accept a
    non-final file (a day cut short by a streamer crash will never gain a
    15:00 sample); regular dates take the listing-only path instead
    (settle is official there; a partial close is not).
    """
    final = set()
    n_not_final = 0
    for f in quote_files:
        ymd = ymd_from_filename(f, "sse_options_daily_")
        if not ymd or ymd not in meta_ymds:
            continue
        if not _quotes_file_is_final(f):
            if not (forced_date is not None
                    and ymd == forced_date.strftime("%Y%m%d")):
                n_not_final += 1
                continue
            logger.info(
                f"    → {ymd}: non-final quotes file accepted for "
                f"--date rebuild (last snapshot wins)"
            )
        final.add(ymd)
    return final, n_not_final

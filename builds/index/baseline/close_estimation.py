"""Fill missing close prices for indices with date gaps.

Some indices (e.g. 399001 深证成指) are missing trading days that other
indices have — holidays, late starts, data gaps.  When the analysis script
pivots to wide format, these gaps become NaN, causing widespread NULL
correlations.  This module estimates the missing closes using the best
proxy index (highest shared weight > SHARED_WEIGHT_THRESHOLD) when
available, or carries forward the previous close as a fallback.

PERFORMANCE CONTRACT (cudf.pandas): NO Timestamp objects are ever put into
python dicts/sets/loops — hashing or comparing a proxied Timestamp is one
cudf slow-path fallback PER OPERATION (the former dict-scan implementation
emitted 5.5M fallback lines and stalled the market-wide run >25 min inside
this module). All date math is numpy datetime64[D]; proxy/pct lookups are
grid-aligned numpy arrays; prev-close chaining across consecutive missing
dates uses a cumulative product within each missing run.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import host_dtypes, safe_columns

from builds._commons.safe_parse import safe_to_numeric
from builds.index.baseline.paths import SHARED_WEIGHT_THRESHOLD


def fill_missing_closes(combined: pd.DataFrame,
                        shared_weights: dict,
                        verbose: bool = True,
                        pct_supplement: pd.DataFrame = None) -> pd.DataFrame:
    """Fill missing trading days with estimated close prices.

    1. Builds a complete date grid (union of all dates in `combined`,
       numpy datetime64[D] — no Timestamp objects).
    2. Per code (grid-aligned numpy arrays):
       a. prev_close at each grid date = last REAL close before it (ffill).
       b. Proxy pct at each grid date = first non-NaN pct among the code's
          qualifying proxies (weight desc), else NaN → carry-forward.
          *pct_supplement* (date/code/changePct rows from the DB) feeds
          proxy pcts for up-to-date codes that were not loaded from CSVs.
       c. Each consecutive missing run is estimated in one cumulative
          product: value_k = prev_close * Π (1 + pct_t/100), pct NaN → 1.0
          — exactly the chronological chained semantics of the original.
    3. Appends estimated rows to `combined` with is_close_estimated=True.

    Original rows get is_close_estimated=False.

    Returns the augmented DataFrame.
    """
    if combined.empty:
        combined["is_close_estimated"] = False
        return combined

    # Mark original rows as non-estimated
    combined["is_close_estimated"] = False

    # ---- shared date grid: numpy datetime64[D], sorted --------------------
    # np.unique over ONE host transfer — Series.unique() on datetime64
    # falls back (cuDF does not implement DatetimeArray)
    all_dates = np.unique(
        np.asarray(combined["date"]).astype("datetime64[D]"))
    n_grid = len(all_dates)
    date_pos: dict = {d: i for i, d in enumerate(np.asarray(all_dates).tolist())}

    # np.asarray host transfer — Series.unique() on cudf-parsed string
    # columns falls back to CPU (ExtensionArrays)
    codes = sorted(set(np.asarray(combined["code"]).tolist()))

    # (date, code) -> changePct lookup as GRID arrays (no Timestamp keys):
    # per code, a pct array aligned to the grid.
    pct_by_code: dict = {}
    if "changePct" in safe_columns(combined):
        d64 = np.asarray(combined["date"]).astype("datetime64[D]")
        cvals = np.asarray(combined["code"]).tolist()
        pvals = np.asarray(safe_to_numeric(combined["changePct"]))
        pos = np.searchsorted(all_dates, d64)
        for code in codes:
            pct_by_code[code] = np.full(n_grid, np.nan)
        # one pass over rows (numpy scalars only — no proxy calls)
        for i, code in enumerate(cvals):
            pct_by_code[code][pos[i]] = pvals[i]

    # Overlay the DB change_pct supplement (up-to-date codes that were not
    # loaded from CSVs). Combined-derived pcts win; the supplement only
    # fills grid positions that are still NaN. *pct_supplement* is a
    # numpy/host tuple (dates_d64, codes, pcts) — see _fetch_pct_supplement.
    if pct_supplement is not None:
        s_d64, s_codes, s_pcts = pct_supplement
        s_pos = np.searchsorted(all_dates, s_d64)
        for i, code in enumerate(s_codes):
            if code not in pct_by_code:
                pct_by_code[code] = np.full(n_grid, np.nan)
            p = s_pos[i]
            # exact grid-date match only (searchsorted inserts before the
            # next grid date when the supplement date is absent)
            if 0 <= p < n_grid and np.datetime64(all_dates[p], "D") == np.datetime64(s_d64[i], "D") \
                    and np.isnan(pct_by_code[code][p]):
                pct_by_code[code][p] = s_pcts[i]

    # Per-code proxy candidates: code -> [(proxy_code, weight), ...] weight desc
    proxy_map: dict = {}
    for code in codes:
        candidates = [
            (cb, sw) for (ca, cb), sw in shared_weights.items()
            if ca == code and cb in pct_by_code
        ]
        candidates.sort(key=lambda x: x[1], reverse=True)
        proxy_map[code] = candidates

    estimated_rows: list[dict] = []
    n_filled = 0
    n_carry = 0

    # Per-code name lookup (no per-row work)
    name_by_code: dict = {}
    if "indexName" in safe_columns(combined):
        cvals = np.asarray(combined["code"]).tolist()
        nvals = np.asarray(combined["indexName"]).tolist()
        for c, n in zip(cvals, nvals):
            name_by_code.setdefault(c, "" if n is None or n != n else n)

    for code in codes:
        sub = combined[combined["code"] == code]
        if len(sub) == 0:
            continue
        d64 = np.asarray(sub["date"]).astype("datetime64[D]")
        pos = np.searchsorted(all_dates, d64)          # exact grid positions
        close_grid = np.full(n_grid, np.nan)
        close_grid[pos] = np.asarray(
            safe_to_numeric(sub["close"]))

        # last REAL close at-or-before each grid position (-1 = none yet)
        known = ~np.isnan(close_grid)
        prev_idx = np.maximum.accumulate(np.where(known, np.arange(n_grid), -1))
        prev_close = np.where(prev_idx >= 0, close_grid[np.clip(prev_idx, 0, None)],
                              np.nan)

        # missing grid positions with a known predecessor
        missing = ~known & (prev_idx >= 0)
        if not missing.any():
            continue

        # proxy pct grid: first non-NaN pct among qualifying proxies
        pct_grid = np.full(n_grid, np.nan)
        for proxy_code, sw in proxy_map.get(code, ()):
            if sw < SHARED_WEIGHT_THRESHOLD:
                break  # sorted desc, so no more qualify
            pp = pct_by_code.get(proxy_code)
            take = np.isnan(pct_grid) & ~np.isnan(pp)
            pct_grid[take] = pp[take]
        pct_grid[~missing] = np.nan  # only estimate on missing positions

        index_name = name_by_code.get(code, "")

        # process missing RUNS (consecutive missing positions) — chaining
        # via cumprod reproduces the original chronological loop
        mpos = np.flatnonzero(missing)
        run_start = 0
        i = 0
        while i < len(mpos):
            j = i
            while j + 1 < len(mpos) and mpos[j + 1] == mpos[j] + 1:
                j += 1
            # run = mpos[i .. j] (consecutive grid positions)
            start = mpos[i]
            base = prev_close[start]     # last REAL close before the run
            factors = 1.0 + np.nan_to_num(pct_grid[start:mpos[j] + 1]) / 100.0
            est = base * np.cumprod(factors)
            for k, gp in enumerate(mpos[i:j + 1]):
                p = float(base) if k == 0 else float(est[k - 1])
                e = float(est[k])
                pct = float(pct_grid[gp]) if not np.isnan(pct_grid[gp]) else None
                if pct is None:
                    n_carry += 1
                else:
                    n_filled += 1
                estimated_rows.append({
                    "date": np.datetime64(all_dates[gp], "D"),
                    "code": code,
                    "indexCode": code,
                    "indexName": index_name,
                    "open": np.nan,
                    "high": np.nan,
                    "low": np.nan,
                    "close": round(e, 4),
                    "trading_shares": np.nan,
                    "trading_amount": np.nan,
                    "change": round(e - p, 4),
                    "changePct": round((e - p) / p * 100.0, 4) if p else np.nan,
                    "pe": np.nan,
                    "consNumber": np.nan,
                    "is_close_estimated": True,
                })
            # chain: estimated closes become predecessors for later dates
            close_grid[mpos[i:j + 1]] = est
            # refresh prev_close for the remainder of the grid
            known = ~np.isnan(close_grid)
            prev_idx = np.maximum.accumulate(
                np.where(known, np.arange(n_grid), -1))
            prev_close = np.where(prev_idx >= 0,
                                  close_grid[np.clip(prev_idx, 0, None)], np.nan)
            i = j + 1

    if estimated_rows:
        # Column-wise ctor with explicit dtypes — a list-of-dicts ctor
        # infers OBJECT dtype for the all-NaN numeric columns (open/high/
        # low/pe/consNumber/… are np.nan in EVERY estimated row) → cudf
        # MixedTypeError fallback, and the poisoned real-pandas frame then
        # cascades "Fast-to-slow transfer is blocked" into every downstream
        # op (sort, MAs, EMAs, the new-vs-DB filter).
        est_df = pd.DataFrame({
            "date": np.asarray([r["date"] for r in estimated_rows],
                               dtype="datetime64[D]"),
            "code": [r["code"] for r in estimated_rows],
            "indexCode": [r["indexCode"] for r in estimated_rows],
            "indexName": [r["indexName"] for r in estimated_rows],
            **{
                k: np.asarray([r[k] for r in estimated_rows], dtype="float64")
                for k in ("open", "high", "low", "close", "trading_shares",
                          "trading_amount", "change", "changePct", "pe",
                          "consNumber")
            },
            "is_close_estimated": np.ones(len(estimated_rows), dtype=bool),
        })
        # cudf.pandas-safe: concat hits the cudf "All columns must be the
        # same type" bug when the dtypes drift from the source frame. Host
        # compare via host_dtypes (raw numpy dtypes — a direct
        # ``est_df[c].dtype != tgt`` proxy compare dispatches
        # ExtensionDtype.__ne__ and falls back per column); only the
        # mismatched columns are astype'd (date precision — GPU-clean).
        cols = safe_columns(combined)
        comb_d = dict(zip(cols, host_dtypes(combined)))
        # reindex AFTER extracting comb_d but BEFORE est_d — reindex adds
        # combined-only columns (all-NaN object) whose astype fills nulls
        est_df = est_df.reindex(columns=cols)
        est_d = dict(zip(cols, host_dtypes(est_df)))
        for _c in cols:
            if est_d[_c] != comb_d[_c]:
                est_df[_c] = est_df[_c].astype(comb_d[_c])
        combined = pd.concat([combined, est_df], ignore_index=True)
        combined = combined.sort_values(["code", "date"]).reset_index(drop=True)

    if verbose:
        print(f"    [EST] Close estimation: {n_filled} dates filled via proxy, "
              f"{n_carry} carried forward, {len(estimated_rows)} total estimated rows", flush=True)

    return combined

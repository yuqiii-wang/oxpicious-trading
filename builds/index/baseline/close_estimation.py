"""Fill missing close prices for indices with date gaps.

Some indices (e.g. 399001 深证成指) are missing trading days that other
indices have — holidays, late starts, data gaps.  When the analysis script
pivots to wide format, these gaps become NaN, causing widespread NULL
correlations.  This module estimates the missing closes using the best
proxy index (highest shared weight > SHARED_WEIGHT_THRESHOLD) when
available, or carries forward the previous close as a fallback.
"""
from __future__ import annotations

import pandas as pd

from builds.index.baseline.paths import SHARED_WEIGHT_THRESHOLD


def fill_missing_closes(combined: pd.DataFrame,
                        shared_weights: dict,
                        verbose: bool = True) -> pd.DataFrame:
    """Fill missing trading days with estimated close prices.

    1. Builds a complete date grid (union of all dates in `combined`).
    2. For each code, finds dates in the grid that are missing.
    3. For each missing (date, code):
       a. Finds the best proxy index = the index with the highest shared
          weight (> SHARED_WEIGHT_THRESHOLD) that HAS data for that date.
       b. If a proxy is found:
          estimated_close = prev_close * (1 + proxy_pct_change / 100)
       c. If no proxy qualifies: estimated_close = prev_close (carry forward).
    4. Appends estimated rows to `combined` with is_close_estimated=True.

    Original rows get is_close_estimated=False.

    Returns the augmented DataFrame.
    """
    if combined.empty:
        combined["is_close_estimated"] = False
        return combined

    # Mark original rows as non-estimated
    combined["is_close_estimated"] = False

    # Build complete date grid (union of all dates across all codes)
    all_dates = sorted(combined["date"].unique())
    date_set = set(all_dates)

    # For each code, find missing dates
    codes = sorted(combined["code"].unique())
    estimated_rows = []

    # Build a lookup: (date, code) -> row data for quick proxy lookup
    # We need changePct for each (date, code) to use as proxy
    date_code_lookup = {}
    for _, row in combined.iterrows():
        date_code_lookup[(row["date"], row["code"])] = row

    # Build per-code proxy mapping: code -> list of (proxy_code, shared_weight) sorted desc
    proxy_map = {}
    for code in codes:
        candidates = []
        for (ca, cb), sw in shared_weights.items():
            if ca == code and cb in codes:
                candidates.append((cb, sw))
        candidates.sort(key=lambda x: x[1], reverse=True)
        proxy_map[code] = candidates

    n_filled = 0
    n_carry = 0

    for code in codes:
        sub = combined[combined["code"] == code].sort_values("date").reset_index(drop=True)
        existing_dates = set(sub["date"].unique())
        missing_dates = date_set - existing_dates

        if not missing_dates:
            continue

        # Build a date -> prev_close map for this code
        # We need prev_close for each missing date. Since missing dates
        # are interspersed, we process chronologically and carry forward.
        sub_sorted = sub.sort_values("date").reset_index(drop=True)
        # Use a plain dict for close tracking (supports mutation)
        close_map = dict(zip(sub_sorted["date"], sub_sorted["close"]))
        index_name = str(sub_sorted["indexName"].iloc[0]) if "indexName" in sub_sorted.columns and len(sub_sorted) else ""

        # Get this code's proxy candidates
        proxies = proxy_map.get(code, [])

        for missing_date in sorted(missing_dates):
            # Find prev_close: the last known close before missing_date
            known_before = {d: c for d, c in close_map.items() if d < missing_date}
            if not known_before:
                # No prior data — skip (can't estimate without a base)
                continue
            prev_date = max(known_before.keys())
            prev_close = known_before[prev_date]

            # Try to find a proxy index with data for this date
            estimated_close = None
            proxy_used = None

            for proxy_code, sw in proxies:
                if sw < SHARED_WEIGHT_THRESHOLD:
                    break  # sorted desc, so no more qualify
                proxy_row = date_code_lookup.get((missing_date, proxy_code))
                if proxy_row is not None:
                    proxy_pct = proxy_row.get("changePct")
                    if proxy_pct is not None and pd.notna(proxy_pct):
                        estimated_close = prev_close * (1.0 + float(proxy_pct) / 100.0)
                        proxy_used = proxy_code
                        break

            if estimated_close is None:
                # No proxy found — carry forward prev_close
                estimated_close = prev_close
                n_carry += 1
            else:
                n_filled += 1

            est_row = {
                "date": missing_date,
                "code": code,
                "indexCode": code,
                "indexName": index_name,
                "open": None,
                "high": None,
                "low": None,
                "close": round(estimated_close, 4),
                "trading_shares": None,
                "trading_amount": None,
                "change": round(estimated_close - prev_close, 4),
                "changePct": round((estimated_close - prev_close) / prev_close * 100.0, 4) if prev_close else None,
                "pe": None,
                "consNumber": None,
                "is_close_estimated": True,
            }
            estimated_rows.append(est_row)

            # Update close_map so subsequent missing dates can chain
            close_map[missing_date] = estimated_close

    if estimated_rows:
        est_df = pd.DataFrame(estimated_rows)
        combined = pd.concat([combined, est_df], ignore_index=True)
        combined = combined.sort_values(["code", "date"]).reset_index(drop=True)

    if verbose:
        print(f"    [EST] Close estimation: {n_filled} dates filled via proxy, "
              f"{n_carry} carried forward, {len(estimated_rows)} total estimated rows", flush=True)

    return combined

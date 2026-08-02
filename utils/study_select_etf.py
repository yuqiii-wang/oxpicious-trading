"""
_study_select_etf.py — Study & select SZSE ETFs by theme/industry.

Studying unique ETF names from the database, mapping them by
similar theme and industry, ordering by quantity per theme. Exports both a
study report CSV and a selection plan consumed by plot_szse_etf_and_margin.py.

Pipeline:
  1. Load combined OHLCV + margin data from the database (stats.etf_identity
     JOIN etf_basic_stats LEFT JOIN etf_liquidity_margin)
  2. Extract unique ETF names & codes
  3. Classify each into a theme via keyword rules
  4. Count per theme, order themes by quantity (descending)
  5. Within each theme, rank ETFs by data quality (OHLCV days, margin coverage)
  6. Quota-distribute ~200 panels across themes
  7. Split oversized themes into multi-figure chunks

Outputs:
  - study/etf_theme_study.csv    — full per-ETF classification + quality metrics
  - study/etf_theme_summary.csv   — per-theme counts (ordered by quantity desc)
  - Returns figure_specs list to caller

Usage:
  python _study_select_etf.py                     # study + export CSVs
  python _study_select_etf.py --limit N           # limit ETFs studied
  python _study_select_etf.py --target-panels 300 # override panel quota
"""
import os, sys, re, argparse
from collections import OrderedDict
from datetime import datetime

import numpy as np
import pandas as pd

from downloads._common.core import strip_exchange_suffix
from utils.db_commons import get_db_connection

# ---------------------------------------------------------------------------
# Classification — loaded from sec_classification.json (replaces _classification.py)
# ---------------------------------------------------------------------------
import json as _json
from pathlib import Path as _Path

_CLASSIFICATION_JSON = _Path(__file__).resolve().parent / "sec_classification.json"
with _CLASSIFICATION_JSON.open("r", encoding="utf-8") as _f:
    _CLASSIFICATION = _json.load(_f)

_CATALOG = _CLASSIFICATION.get("catalog", {})
_ETFS_JSON = _CLASSIFICATION.get("etfs", {})

# Build bare-code → {sector_id, industry_id, name} lookup from the JSON etfs section.
_BARE_CODE_TO_ETF: dict = {}
for _full_code, _info in _ETFS_JSON.items():
    _bare = _full_code.rsplit(".", 1)[0] if "." in _full_code else _full_code
    _BARE_CODE_TO_ETF[_bare] = _info

# Build industry_id → (sector_id, sector_label, industry_id, industry_label, slug) lookup
# and industry_id → [keywords] lookup from the catalog + build_classification.INDEX_RULES.
from utils.classification import INDEX_RULES as _INDEX_RULES

_INDUSTRY_LOOKUP: dict = {}
_KEYWORDS_BY_INDUSTRY: dict = {}
for _sid, _sdata in _CATALOG.items():
    _s_label = _sdata.get("label", _sid)
    for _iid, _idata in (_sdata.get("industries") or {}).items():
        _i_label = _idata.get("label", _iid)
        _slug = _idata.get("slug", _iid.lower())
        _INDUSTRY_LOOKUP[_iid] = (_sid, _s_label, _iid, _i_label, _slug)
        _KEYWORDS_BY_INDUSTRY[_iid] = []

# Fill keywords from INDEX_RULES (same keywords as the former _classification.TAXONOMY).
for _sid, _s_label, _iid, _i_label, _kws in _INDEX_RULES:
    _KEYWORDS_BY_INDUSTRY[_iid] = list(_kws)

# ETF_THEMES: OrderedDict keyed by industry_id (drop-in replacement for ETF_THEMES_COMPAT).
ETF_THEMES: "OrderedDict[str, dict]" = OrderedDict()
for _iid, (_sid, _s_label, _, _i_label, _slug) in _INDUSTRY_LOOKUP.items():
    ETF_THEMES[_iid] = {
        "theme_label": _i_label,
        "slug": _slug,
        "kw": _KEYWORDS_BY_INDUSTRY.get(_iid, []),
        "theme_group_id": _sid,
        "theme_group_label": _s_label,
        "industry_id": _iid,
        "industry_label": _i_label,
    }
ETF_THEMES["OTHER"] = {
    "theme_label": "其他｜未分类  Unclassified",
    "slug": "other",
    "kw": [],
    "theme_group_id": "OTHER",
    "theme_group_label": "其他",
    "industry_id": "OTHER",
    "industry_label": "未分类",
}


def classify_etf(code: str, name: str = ""):
    """Classify an ETF by code (JSON lookup) with keyword fallback by name.

    Returns (theme_id, theme_label, slug) — same tuple shape as the former
    _classification.classify_etf for backward compatibility.
    """
    info = _BARE_CODE_TO_ETF.get(code)
    if info is not None:
        iid = info.get("industry_id", "OTHER")
        lookup = _INDUSTRY_LOOKUP.get(iid)
        if lookup:
            _, _, _, label, slug = lookup
            return (iid, label, slug)
    # Fallback: keyword matching using INDEX_RULES keywords.
    s = str(name or "")
    if not s:
        return ("OTHER", "其他｜未分类  Unclassified", "other")
    best_id = "OTHER"
    best_label = "其他｜未分类  Unclassified"
    best_slug = "other"
    best_score = 0
    for iid, kws in _KEYWORDS_BY_INDUSTRY.items():
        score = sum(len(kw) for kw in kws if kw in s)
        if score > best_score:
            best_score = score
            lookup = _INDUSTRY_LOOKUP.get(iid)
            if lookup:
                _, _, _, best_label, best_slug = lookup
                best_id = iid
    return (best_id, best_label, best_slug)


def compute_keyword_match_score(name: str, theme_id: str):
    """Score how well *name* matches *theme_id* keywords.

    Returns (total_len, n_hits, longest_kw) — higher = better.
    """
    s = str(name or "")
    kws = _KEYWORDS_BY_INDUSTRY.get(theme_id, [])
    if not kws:
        return (0, 0, 0)
    hits = [kw for kw in kws if kw in s]
    if not hits:
        return (0, 0, 0)
    return (sum(len(kw) for kw in hits), len(hits), max(len(kw) for kw in hits))


def get_theme_taxonomy(theme_id: str):
    """Return (theme_group_id, theme_group_label, industry_id, industry_label)."""
    cfg = ETF_THEMES.get(theme_id) or ETF_THEMES["OTHER"]
    return (
        cfg.get("theme_group_id", "OTHER"),
        cfg.get("theme_group_label", "其他"),
        cfg.get("industry_id", theme_id),
        cfg.get("industry_label", cfg.get("theme_label", "")),
    )

# ============================================================================
# Paths
# ============================================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMP_DATA    = os.path.join(PROJECT_ROOT, "temp_data")
OUTPUT_DIR   = os.path.join(TEMP_DATA, "analysis_output", "szse_sse_etf_margin")
STUDY_DIR    = os.path.join(OUTPUT_DIR, "study")
os.makedirs(STUDY_DIR, exist_ok=True)

TODAY_STR = datetime.now().strftime("%Y-%m-%d")

# ============================================================================
# Data loading — from database (replaces old CSV read)
# ============================================================================
_combined_cache = None

def load_combined():
    """Load (cached) combined OHLCV + margin data from the database.

    Queries stats.etf_identity JOIN etf_basic_stats LEFT JOIN etf_liquidity_margin
    to reconstruct the same column set previously exported to
    etf_margin_combined.csv.
    """
    global _combined_cache
    if _combined_cache is not None:
        return _combined_cache

    print(f"    [LOAD] querying stats.etf_identity + etf_basic_stats + "
          f"etf_liquidity_margin from database …", flush=True)

    query = """
        SELECT
            i.date, i.code, i.name,
            b.prev_close, b.open, b.high, b.low, b.close, b.pct_change,
            COALESCE(l.volume_wan, 0)     AS volume_wan,
            COALESCE(l.amount_wan, 0)     AS amount_wan,
            COALESCE(l.rz_buy, 0)         AS rz_buy,
            COALESCE(l.rz_balance, 0)     AS rz_balance,
            COALESCE(l.rq_sell_qty, 0)    AS rq_sell_qty,
            COALESCE(l.rq_balance_qty, 0) AS rq_balance_qty,
            COALESCE(l.rq_balance_amt, 0) AS rq_balance_amt,
            COALESCE(l.total_balance, 0)  AS total_balance
        FROM stats.etf_identity i
        JOIN stats.etf_basic_stats b
            ON b.date = i.date AND b.code = i.code
        LEFT JOIN stats.etf_liquidity_margin l
            ON l.date = i.date AND l.code = i.code
        ORDER BY i.code, i.date
    """

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    if not rows:
        print(f"    [FATAL] No ETF data found in database. "
              f"Run build_szse_sse_etf_and_margin.py first.", flush=True)
        sys.exit(1)

    df = pd.DataFrame(rows, columns=col_names)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values(["code", "date"]).reset_index(drop=True)
    # etf_identity.code is stored WITH exchange suffix (e.g. "159530.SZ");
    # strip it so downstream code sees bare 6-digit codes.
    df["code"] = df["code"].astype(str).apply(strip_exchange_suffix)
    _combined_cache = df
    print(f"    → {len(df):,} rows · {df['code'].nunique()} ETFs · "
          f"{df['date'].min().date()} → {df['date'].max().date()}", flush=True)
    return df


# ============================================================================
# Study: unique ETF names → themes → ordered by quantity
# ============================================================================
def study_etf_themes(combined_df=None, save=True, require_recent_data=True):
    """Study the ETF universe: classify, group by theme, count per theme.

    Args:
        combined_df:  combined DataFrame from load_combined().
        save:         whether to save CSV outputs.
        require_recent_data: if True, filter out ETFs with no data in the last month.

    Returns:
        (study_df, summary_df)
        - study_df:   per-ETF row with [code, name, theme_id, theme_label, slug,
                       theme_group_id, theme_group_label, industry_id, industry_label,
                       n_ohlcv_days, n_margin_days, has_margin, avg_volume_wan,
                       has_recent_data]
        - summary_df: per-theme row with [theme_id, theme_label, slug,
                       theme_group_id, theme_group_label, industry_id, industry_label,
                       n_etfs, n_with_margin, n_ohlcv_qualified, n_recent_qualified]
                       ordered by n_etfs DESC.
    """
    if combined_df is None:
        combined_df = load_combined()

    cutoff_date = combined_df["date"].max() - pd.Timedelta(days=30)

    rows = []
    for code, sub in combined_df.groupby("code"):
        name = str(sub["name"].dropna().iloc[0]) if sub["name"].notna().any() else ""
        tid, tlabel, tslug = classify_etf(code, name)
        tgid, tglab, iid, ilab = get_theme_taxonomy(tid)
        rz = pd.to_numeric(sub.get("rz_balance", 0), errors="coerce").fillna(0.0)
        rq = pd.to_numeric(sub.get("rq_balance_amt", 0), errors="coerce").fillna(0.0)
        vol = pd.to_numeric(sub.get("volume_wan", 0), errors="coerce").fillna(0.0)
        n_margin = int(((rz > 0) | (rq > 0)).sum())
        has_recent = (sub["date"] >= cutoff_date).any()
        rows.append({
            "code":               code,
            "name":               name,
            "theme_id":           tid,
            "theme_label":        tlabel,
            "slug":               tslug,
            "theme_group_id":     tgid,
            "theme_group_label":  tglab,
            "industry_id":        iid,
            "industry_label":     ilab,
            "n_ohlcv_days":       len(sub),
            "n_margin_days":      n_margin,
            "has_margin":         n_margin > 0,
            "avg_volume_wan":     float(vol.mean()) if len(vol) else 0.0,
            "has_recent_data":    has_recent,
        })
    study_df = pd.DataFrame(rows)

    if require_recent_data:
        before_count = len(study_df)
        study_df = study_df[study_df["has_recent_data"]].reset_index(drop=True)
        print(f"    [FILTER] Removed {before_count - len(study_df)} ETFs with no data in the last month", flush=True)

    summary_rows = []
    for tid, cfg in ETF_THEMES.items():
        sub = study_df[study_df["theme_id"] == tid]
        if len(sub) == 0:
            continue
        summary_rows.append({
            "theme_id":          tid,
            "theme_label":       cfg["theme_label"],
            "slug":              cfg["slug"],
            "theme_group_id":    cfg.get("theme_group_id", ""),
            "theme_group_label": cfg.get("theme_group_label", ""),
            "industry_id":       cfg.get("industry_id", tid),
            "industry_label":    cfg.get("industry_label", ""),
            "n_etfs":            len(sub),
            "n_with_margin":     int(sub["has_margin"].sum()),
            "n_ohlcv_qualified": int((sub["n_ohlcv_days"] >= 40).sum()),
            "n_recent_qualified": int(sub["has_recent_data"].sum()),
        })
    summary_df = pd.DataFrame(summary_rows).sort_values("n_etfs", ascending=False).reset_index(drop=True)

    if save:
        study_path = os.path.join(STUDY_DIR, "etf_theme_study.csv")
        summary_path = os.path.join(STUDY_DIR, "etf_theme_summary.csv")
        study_df.to_csv(study_path, index=False, encoding="utf-8-sig")
        summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
        print(f"    [SAVE] {study_path} ({len(study_df)} ETFs)", flush=True)
        print(f"    [SAVE] {summary_path} ({len(summary_df)} themes)", flush=True)

    return study_df, summary_df


# ============================================================================
# Selection: which ETFs to plot, quota-distributed across themes
# ============================================================================
def select_etfs_for_plotting(
    combined_df,
    target_panels=800,
    soft_max_per_theme=40,
    hard_max_per_figure=35,
    min_ohlcv_rows=40,
    max_allowed=900,
    max_per_match_tier=3,
    limit=None,
    verbose=True,
    require_recent_data=True,
):
    """Select which ETFs to plot, distributed across themes.

    Pipeline:
      1. Classify all ETFs into themes
      2. Filter to ETFs with ≥ min_ohlcv_rows OHLCV days AND data in last month
      3. Rank within each theme: margin first → keyword combination score →
         margin days → OHLCV days → volume DESC.
         Within the same "match tier" (same margin gate + same keyword score),
         keep only TOP N (default 3) highest-volume ETFs to dedup near-
         duplicates like 20 红利-only ETFs that differ only by issuer.
      4. Quota-distribute target_panels across themes (3-pass)
      5. Split oversized themes into multi-figure chunks

    Args:
        combined_df:   combined DataFrame from load_combined().
        target_panels: target total panel count across all figures.
        soft_max_per_theme: max panels per theme (before headroom redistribution).
        hard_max_per_figure: max panels per figure (split into parts if exceeded).
        min_ohlcv_rows: minimum OHLCV rows for an ETF to qualify.
        max_allowed: hard cap on total panels (trim if exceeded).
        max_per_match_tier: per (margin_gate, keyword_score) tier cap; top
                            N highest-volume ETFs kept, rest discarded as dupes.
        limit: if set, only study the top-N ETFs by OHLCV row count (dev).
        verbose: print progress.
        require_recent_data: if True, filter out ETFs with no data in the last month.

    Returns:
        figure_specs: list of (slug, theme_label, [(code, name), ...])
        Each entry describes one figure's worth of ETFs (codes + names only;
        the caller loads OHLCV & margin DataFrames).
        Also returns date_range_note string.
    """
    # --- Build per-code lookups ---
    code_name_map = {}
    code_count_map = {}
    code_margin_map = {}
    code_volume_map = {}
    code_recent_map = {}
    cutoff_date = combined_df["date"].max() - pd.Timedelta(days=30)
    for code, sub in combined_df.groupby("code"):
        name = str(sub["name"].dropna().iloc[0]) if sub["name"].notna().any() else ""
        code_name_map[code] = name
        code_count_map[code] = len(sub)
        rz = pd.to_numeric(sub.get("rz_balance", 0), errors="coerce").fillna(0.0)
        rq = pd.to_numeric(sub.get("rq_balance_amt", 0), errors="coerce").fillna(0.0)
        code_margin_map[code] = int(((rz > 0) | (rq > 0)).sum())
        vol = pd.to_numeric(sub.get("volume_wan", 0), errors="coerce").fillna(0.0)
        code_volume_map[code] = float(vol.mean()) if len(vol) else 0.0
        code_recent_map[code] = (sub["date"] >= cutoff_date).any()

    # Optional dev limit
    if limit:
        top_codes = sorted(code_count_map.keys(), key=lambda c: -code_count_map[c])[:limit]
        code_name_map = {c: code_name_map[c] for c in top_codes if c in code_name_map}

    # --- (1) Classify ---
    code_theme = {}
    for code, name in code_name_map.items():
        tid, tlabel, tslug = classify_etf(code, name)
        code_theme[code] = (tid, tlabel, tslug)

    if verbose:
        print(f"\n  [STUDY] Classifying {len(code_name_map)} ETFs into themes …", flush=True)
        counts = {}
        for code, (tid, _, _) in code_theme.items():
            counts[tid] = counts.get(tid, 0) + 1
        for tid in ETF_THEMES.keys():
            cnt = counts.get(tid, 0)
            if cnt > 0:
                print(f"    · {tid:<20s} {cnt:>4d}  → {ETF_THEMES[tid]['theme_label']}", flush=True)

    # --- (2) Filter & rank within each theme ---
    # Rank key (highest priority first):
    #   1. Has margin data (boolean) — ETFs with ANY two-financing data MUST
    #      be selected first (primary eligibility gate).  Margin-capable ETFs
    #      always beat no-margin ETFs regardless of keyword quality.
    #   2. Keyword match score — within each margin-tier, ETFs matching MORE /
    #      LONGER theme keywords rank higher.  E.g. 红利低波 (both keywords,
    #      combined score) beats 红利 alone (single keyword).  This ensures
    #      "combination effect" drives selection priority after eligibility.
    #   3. n_margin_days DESC — within same keyword tier, more margin days
    #      act as a reliability tie-breaker.
    #   4. n_ohlcv_days DESC — longer price-history better.
    #   5. avg_volume_wan DESC — higher trading liquidity better.
    theme_all_codes = OrderedDict()
    total_qualified = 0
    for tid in ETF_THEMES.keys():
        codes_in_theme = [c for c, (t, _, _) in code_theme.items() if t == tid]
        if not codes_in_theme:
            continue
        # Sort: margin gate → kw combination → margin days → OHLCV → volume
        codes_sorted = sorted(
            codes_in_theme,
            key=lambda c: (
                -(1 if code_margin_map.get(c, 0) > 0 else 0),
                tuple(-x for x in compute_keyword_match_score(code_name_map[c], tid)),
                -code_margin_map.get(c, 0),
                -code_count_map.get(c, 0),
                -code_volume_map.get(c, 0.0),
            ),
        )
        # Filter to qualified (≥ min_ohlcv_rows AND has data in last month)
        qualified = [c for c in codes_sorted
                     if code_count_map.get(c, 0) >= min_ohlcv_rows
                     and (not require_recent_data or code_recent_map.get(c, False))]

        # Dedup near-duplicates: for the same "match tier"
        #   tier_key = kw_score_tuple ONLY  (mixes margin + no-margin in one tier)
        # Keep top-N (default 3) highest VOLUME within tier, then enforce
        # the "at least one margin ETF per tier" rule:
        #   If top-N-by-volume has 0 margin but the full tier contains margin
        #   ETFs at positions N+1 and beyond, promote the HIGHEST-VOLUME margin
        #   ETF from the remainder into the kept list (replacing position N-1
        #   a.k.a. "the 3rd ← 4th swap" if margin exists at 4th+).
        dedup_count = 0
        swap_count = 0
        if max_per_match_tier and max_per_match_tier > 0:
            # (1) Group qualified codes by kw_score tier
            tiers = OrderedDict()
            for c in qualified:
                kw = compute_keyword_match_score(code_name_map[c], tid)
                tiers.setdefault(kw, []).append(c)

            kept_set = set()
            for kw, tier_codes in tiers.items():
                # (2) Within each tier, re-sort by VOLUME DESC to find
                #     top-volume names first (top-3 = most traded).
                tier_by_vol = sorted(tier_codes, key=lambda c: -code_volume_map.get(c, 0.0))

                kept_tier = list(tier_by_vol[:max_per_match_tier])
                dedup_count += max(0, len(tier_by_vol) - len(kept_tier))

                has_margin_in_kept = any(code_margin_map.get(c, 0) > 0 for c in kept_tier)
                if not has_margin_in_kept and len(tier_by_vol) > max_per_match_tier:
                    remainder = tier_by_vol[max_per_match_tier:]
                    # Find first margin-capable ETF in remainder (remainder is
                    # already volume DESC, so first match = highest-vol one).
                    margin_elev = next((c for c in remainder
                                        if code_margin_map.get(c, 0) > 0), None)
                    if margin_elev is not None:
                        # Replace the last kept slot (the 3rd) with the
                        # promoted margin ETF; if kept list shorter than max
                        # (rare), just append.
                        if len(kept_tier) >= max_per_match_tier:
                            kept_tier.pop()
                        kept_tier.append(margin_elev)
                        swap_count += 1
                        # Don't double-count the promoted code as a dedup:
                        # the removed 3rd is still counted in dedup_count,
                        # but the elevated 4th+ now counts as kept, not dedup.
                kept_set.update(kept_tier)

            # (3) Re-order kept codes to match the global priority ranking
            #     (margin gate → kw score → margin days → OHLCV → volume) so
            #     the 3-pass quota distribution still keeps highest-priority
            #     names first.
            code_rank_in_qualified = {c: i for i, c in enumerate(qualified)}
            qualified = sorted(kept_set, key=lambda c: code_rank_in_qualified.get(c, 99999))

        if qualified:
            theme_all_codes[tid] = qualified
            total_qualified += len(qualified)
            if verbose and (dedup_count > 0 or swap_count > 0):
                msg = f"    !! {tid:<20s} deduped {dedup_count} same-match ETFs (kept {len(qualified)})"
                if swap_count > 0:
                    msg += f"  [margin-promoted {swap_count} tiers]"
                print(msg, flush=True)

    if verbose:
        print(f"\n  [STUDY] Qualified ETFs (≥{min_ohlcv_rows} OHLCV days): {total_qualified}", flush=True)
        for tid, codes in theme_all_codes.items():
            n_marg = sum(1 for c in codes if code_margin_map.get(c, 0) > 0)
            print(f"    · {tid:<20s} {len(codes):>3d} qualified ({n_marg} with margin)", flush=True)

    if total_qualified == 0:
        print("    [FATAL] No ETFs have qualified OHLCV data", flush=True)
        return [], ""

    # --- (3) Quota-distribute (3-pass) ---
    n_themes = len(theme_all_codes)
    base_quota = max(6, target_panels // n_themes)
    theme_final = OrderedDict()
    total_selected = 0

    # Pass 1: each theme takes up to quota or its count
    for tid, codes in theme_all_codes.items():
        take = min(len(codes), base_quota, soft_max_per_theme)
        theme_final[tid] = list(codes[:take])
        total_selected += take

    # Pass 2: redistribute headroom to biggest themes
    headroom = target_panels - total_selected
    if headroom > 0:
        sorted_tids = sorted(theme_all_codes.keys(),
                             key=lambda t: len(theme_all_codes[t]), reverse=True)
        for tid in sorted_tids:
            if headroom <= 0:
                break
            extra = min(headroom,
                        len(theme_all_codes[tid]) - len(theme_final[tid]),
                        soft_max_per_theme - len(theme_final[tid]))
            if extra > 0:
                extra_slice = theme_all_codes[tid][len(theme_final[tid]):len(theme_final[tid])+extra]
                theme_final[tid].extend(extra_slice)
                total_selected += extra
                headroom -= extra

    # Pass 3: trim if over max_allowed
    if total_selected > max_allowed:
        over = total_selected - max_allowed
        sorted_tids = sorted(theme_final.keys(),
                             key=lambda t: len(theme_final[t]), reverse=True)
        for tid in sorted_tids:
            while over > 0 and len(theme_final[tid]) > 3:
                theme_final[tid].pop()
                over -= 1
                total_selected -= 1
            if over <= 0:
                break

    if verbose:
        print(f"\n  [STUDY] Final selection: {total_selected} panels across {len(theme_final)} themes",
              flush=True)

    # --- (4) Split oversized themes into multi-figure chunks ---
    figure_specs = []
    for tid, codes in theme_final.items():
        cfg = ETF_THEMES[tid]
        slug_base = cfg["slug"]
        label_base = cfg["theme_label"]
        etf_list = [(c, code_name_map.get(c, c)) for c in codes]
        if len(etf_list) <= hard_max_per_figure:
            figure_specs.append((slug_base, label_base, etf_list))
        else:
            n = len(etf_list)
            n_parts = int(np.ceil(n / float(hard_max_per_figure)))
            part_size = int(np.ceil(n / float(n_parts)))
            for i in range(n_parts):
                start = i * part_size
                end = min(n, start + part_size)
                chunk = etf_list[start:end]
                if not chunk:
                    continue
                part_num = i + 1
                sfx = f"_part{part_num:02d}"
                lbl_full = f"{label_base}  [{part_num}/{n_parts}]"
                figure_specs.append((slug_base + sfx, lbl_full, chunk))

    # --- Date-range note ---
    all_dates = combined_df["date"].dropna()
    if len(all_dates):
        dmin = all_dates.min().strftime("%Y-%m-%d")
        dmax = all_dates.max().strftime("%Y-%m-%d")
        date_note = f"{dmin} → {dmax}"
    else:
        date_note = TODAY_STR

    return figure_specs, date_note


# ============================================================================
# Standalone: study ETF themes & export report
# ============================================================================
def main():
    ap = argparse.ArgumentParser(description="Study SZSE ETF themes and export selection report.")
    ap.add_argument("--limit", type=int, default=None, help="Limit to top-N ETFs by OHLCV rows (dev)")
    ap.add_argument("--target-panels", type=int, default=800, help="Target total panels")
    ap.add_argument("--soft-max", type=int, default=40, help="Max panels per theme")
    ap.add_argument("--hard-max", type=int, default=35, help="Max panels per figure")
    ap.add_argument("--min-ohlcv", type=int, default=40, help="Min OHLCV rows to qualify")
    ap.add_argument("--max-per-tier", type=int, default=3, help="Top-N per (margin_gate + keyword_score) tier; ≤3 default. 0 disables dedup.")
    ap.add_argument("--no-recent-filter", action="store_true", help="Disable filtering ETFs with no data in the last month")
    args = ap.parse_args()

    print("=" * 78, flush=True)
    print("  SZSE ETF THEME STUDY  ·  classify + select for plotting", flush=True)
    print("=" * 78, flush=True)
    print(f"  Study dir    : {STUDY_DIR}", flush=True)
    print(f"  Today        : {TODAY_STR}", flush=True)

    # --- (1) Load ---
    print("\n[1/3] Loading ETF data from database …", flush=True)
    combined = load_combined()

    # Optional dev limit
    if args.limit:
        code_counts = combined.groupby("code").size().sort_values(ascending=False)
        top_codes = code_counts.head(args.limit).index.tolist()
        combined = combined[combined["code"].isin(top_codes)].copy()
        print(f"    → --limit applied → {combined['code'].nunique()} ETFs", flush=True)

    # --- (2) Study themes ---
    print("\n[2/3] Studying ETF themes (unique names → theme/industry) …", flush=True)
    study_df, summary_df = study_etf_themes(combined, save=True, require_recent_data=not args.no_recent_filter)

    print(f"\n  Theme summary (ordered by quantity DESC):", flush=True)
    print(f"  {'Theme ID':<20s} {'Grp':<6s} {'Ind':<6s} {'#ETFs':>6s} {'#Margin':>8s} {'#Qual':>6s}  Label", flush=True)
    print(f"  {'-'*20} {'-'*6} {'-'*6} {'-'*6} {'-'*8} {'-'*6}  {'-'*40}", flush=True)
    for _, r in summary_df.iterrows():
        print(f"  {r['theme_id']:<20s} {str(r.get('theme_group_id','')):<6s} "
              f"{str(r.get('industry_id','')):<6s} {r['n_etfs']:>6d} {r['n_with_margin']:>8d} "
              f"{r['n_ohlcv_qualified']:>6d}  {r['theme_label']}", flush=True)
    print(f"\n  Total: {study_df['code'].nunique()} unique ETFs across "
          f"{summary_df['theme_id'].nunique()} industries / "
          f"{summary_df['theme_group_id'].nunique()} theme groups", flush=True)

    if "theme_group_id" in summary_df.columns:
        grp = (summary_df.groupby(["theme_group_id", "theme_group_label"], dropna=False)
                      .agg(n_etfs=("n_etfs", "sum"),
                           n_with_margin=("n_with_margin", "sum"),
                           n_ohlcv_qualified=("n_ohlcv_qualified", "sum"),
                           n_industries=("theme_id", "nunique"))
                      .sort_values("n_etfs", ascending=False).reset_index())
        print(f"\n  Theme GROUP summary (ordered by #ETFs DESC, {len(grp)} groups):", flush=True)
        print(f"  {'GrpID':<12s} {'Group Label':<12s} {'#Ind':>5s} {'#ETFs':>6s} {'#Margin':>8s} {'#Qual':>6s}", flush=True)
        print(f"  {'-'*12} {'-'*12} {'-'*5} {'-'*6} {'-'*8} {'-'*6}", flush=True)
        for _, r in grp.iterrows():
            print(f"  {str(r['theme_group_id']):<12s} {str(r['theme_group_label']):<12s} "
                  f"{int(r['n_industries']):>5d} {int(r['n_etfs']):>6d} "
                  f"{int(r['n_with_margin']):>8d} {int(r['n_ohlcv_qualified']):>6d}", flush=True)

    # --- (3) Selection plan ---
    print("\n[3/3] Building selection plan for plotting …", flush=True)
    figure_specs, date_note = select_etfs_for_plotting(
        combined,
        target_panels=args.target_panels,
        soft_max_per_theme=args.soft_max,
        hard_max_per_figure=args.hard_max,
        min_ohlcv_rows=args.min_ohlcv,
        max_per_match_tier=args.max_per_tier,
        verbose=True,
        require_recent_data=not args.no_recent_filter,
    )

    print(f"\n  Figure specs ({len(figure_specs)} figures):", flush=True)
    for i, (slug, label, etf_list) in enumerate(figure_specs):
        print(f"    [{i:03d}] {slug:<40s} {len(etf_list):>3d} ETFs  → {label[:50]}", flush=True)

    # Save selection plan
    plan_path = os.path.join(STUDY_DIR, "etf_selection_plan.csv")
    plan_rows = []
    for fig_idx, (slug, label, etf_list) in enumerate(figure_specs):
        for panel_idx, (code, name) in enumerate(etf_list):
            tid, _, _ = classify_etf(code, name)
            tgid, tglab, iid, ilab = get_theme_taxonomy(tid)
            plan_rows.append({
                "fig_idx":          fig_idx,
                "fig_slug":         slug,
                "fig_label":        label,
                "panel_idx":        panel_idx,
                "code":             code,
                "name":             name,
                "theme_id":         tid,
                "theme_group_id":   tgid,
                "theme_group_label": tglab,
                "industry_id":      iid,
                "industry_label":   ilab,
            })
    plan_df = pd.DataFrame(plan_rows)
    plan_df.to_csv(plan_path, index=False, encoding="utf-8-sig")
    print(f"\n  [SAVE] {plan_path} ({len(plan_df)} rows)", flush=True)

    print(f"\n  Done. Date range: {date_note}", flush=True)
    print("=" * 78, flush=True)


if __name__ == "__main__":
    main()

"""builds.bond.pboc_omo — PBoC OMO / outright-repo / MLF daily builders.

All three builders derive from the combined instruments CSV:
  (1) build_pboc_omo_df            → stats.debt_omo
  (2) build_pboc_outright_repo_df  → stats.debt_outright_repo
      build_pboc_mlf_df            → stats.debt_mlf
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from builds._commons.row_emission import dates_as_date_list
from builds.bond.instruments import (
    load_pboc_instruments_df,
    parse_duration_to_days,
)
from builds.bond.paths import PBOC_INSTRUMENTS_CSV


def _tenor_days_vec(tenor_col: pd.Series) -> pd.Series:
    """Vectorized parse_duration_to_days over a str column ('7D','6M','1Y').

    Returns float64 days (NaN when the token is empty/unparsable) — the
    debt_* tenor_days columns are NUMERIC(6,1). Series.map(callable) is
    NOT used: under cudf.pandas it attempts a Numba JIT per element and
    falls back per element.
    """
    st = tenor_col.astype(str).str.strip().str.upper()
    ext = st.str.extract(r"^(\d+)\s*([DMY])$", expand=True)
    n = pd.to_numeric(ext[0], errors="coerce")
    mult = ext[1].map({"D": 1.0, "M": 30.0, "Y": 365.0})
    return n * mult


# ============================================================================
# (1) PBoC OMO transaction announcements  → daily OMO rate / qty / tenor
# ============================================================================
def build_pboc_omo_df(start_date=None, end_date=None, verbose=True):
    """Build a daily OMO operations frame from the combined instruments CSV.

    Per-date assembly runs as PURE PYTHON over ONE host transfer per column
    (the former groupby + .iloc + .tolist + pd.notna loop cost ~11k cudf
    fallback lines per run). Group order = date order; within a date the
    CSV row order is preserved — identical semantics to the groupby loop.

    Returns DataFrame columns:
        date, omo_rate, omo_quantity, omo_tenor_days, omo_tenor_label,
        omo_all_rates, omo_all_tenors, omo_all_quantities, omo_dur_qty_pairs
    """
    if verbose:
        print(f"    [PBOC-OMO] reading {PBOC_INSTRUMENTS_CSV}", flush=True)

    inst = load_pboc_instruments_df(start_date=start_date, end_date=end_date, verbose=verbose)
    if inst is None or len(inst) == 0:
        if verbose:
            print(f"    [PBOC-OMO] no records in range", flush=True)
        return pd.DataFrame()

    inst = inst[inst["category"] == "omo_transaction"]
    if len(inst) == 0:
        if verbose:
            print(f"    [PBOC-OMO] no omo_transaction records in range", flush=True)
        return pd.DataFrame()

    host = inst.to_pandas() if hasattr(inst, "to_pandas") else inst
    dates_l = dates_as_date_list(host["pub_date"])
    instrs_l = np.asarray(host["instrument"]).astype(object).tolist()
    rates_l = np.asarray(host["rate"]).tolist()   # float64, NaN for missing
    qtys_l = np.asarray(host["quantity"]).tolist()
    tenors_l = np.asarray(host["tenor"]).astype(object).tolist()

    acc: dict = {}
    for d, ins, r, q, t in zip(dates_l, instrs_l, rates_l, qtys_l, tenors_l):
        st = acc.setdefault(d, {"has_rr": False, "rate": np.nan,
                                 "qty": np.nan, "tenor": "", "rows": []})
        if ins == "reverse_repo" and not st["has_rr"]:
            st["has_rr"] = True
            st["rate"] = r
            st["qty"] = q
            st["tenor"] = t or ""
        if ins == "reverse_repo" or ins == "MLF":
            st["rows"].append((t, r, q))

    rows = []
    for d in sorted(acc):
        st = acc[d]
        # only dates with a reverse-repo record feed the OMO table (the
        # former omo_has_reverse_repo post-filter dropped the rest)
        if not st["has_rr"]:
            continue
        all_rates = [f"{v:g}" for v in (x[1] for x in st["rows"]) if v == v]
        all_tenors = [str(x[0]) for x in st["rows"] if x[0]]
        all_qtys = [f"{v:g}" for v in (x[2] for x in st["rows"]) if v == v]
        dur_pairs = [f"{x[0]}:{x[2]:g}" for x in st["rows"] if x[0] and x[2] == x[2]]
        rows.append({
            "date": d,
            "omo_rate": st["rate"],
            "omo_quantity": st["qty"],
            "omo_tenor_days": parse_duration_to_days(st["tenor"]),
            "omo_tenor_label": st["tenor"],
            "omo_all_rates": "|".join(all_rates),
            "omo_all_tenors": "|".join(all_tenors),
            "omo_all_quantities": "|".join(all_qtys),
            "omo_dur_qty_pairs": "|".join(dur_pairs),
        })

    if not rows:
        return pd.DataFrame()

    # Column-wise ctor with explicit dtypes (dates stay datetime64 — a
    # dict-rows ctor would infer object date columns)
    df = pd.DataFrame({
        "date": np.array([r["date"] for r in rows], dtype="datetime64[ns]"),
        "omo_rate": np.array([r["omo_rate"] for r in rows], dtype="float64"),
        "omo_quantity": np.array([r["omo_quantity"] for r in rows], dtype="float64"),
        "omo_tenor_days": np.array(
            [np.nan if r["omo_tenor_days"] is None else r["omo_tenor_days"]
             for r in rows], dtype="float64"),
        "omo_tenor_label": [r["omo_tenor_label"] for r in rows],
        "omo_all_rates": [r["omo_all_rates"] for r in rows],
        "omo_all_tenors": [r["omo_all_tenors"] for r in rows],
        "omo_all_quantities": [r["omo_all_quantities"] for r in rows],
        "omo_dur_qty_pairs": [r["omo_dur_qty_pairs"] for r in rows],
    })

    if verbose:
        if len(df):
            print(f"    [PBOC-OMO] parsed {len(df)} daily OMO records, "
                  f"{rows[0]['date']} → {rows[-1]['date']}", flush=True)
            if df["omo_rate"].notna().any():
                print(f"    [PBOC-OMO] omo_rate range: "
                      f"{df['omo_rate'].min():.4f}% → {df['omo_rate'].max():.4f}%", flush=True)
            if df["omo_quantity"].notna().any():
                print(f"    [PBOC-OMO] omo_quantity range: "
                      f"{df['omo_quantity'].min():g} → {df['omo_quantity'].max():g} 亿元", flush=True)
        else:
            print(f"    [PBOC-OMO] no records in range", flush=True)
    return df


# ============================================================================
# (2) PBoC outright-repo tender announcements  → daily marker
# ============================================================================
def build_pboc_outright_repo_df(start_date=None, end_date=None, verbose=True):
    """Build a daily outright-repo marker frame from the combined instruments CSV."""
    if verbose:
        print(f"    [PBOC-OUTRIGHT] reading {PBOC_INSTRUMENTS_CSV}", flush=True)

    inst = load_pboc_instruments_df(start_date=start_date, end_date=end_date, verbose=verbose)
    if inst is None or len(inst) == 0:
        if verbose:
            print(f"    [PBOC-OUTRIGHT] no records in range", flush=True)
        return pd.DataFrame()

    inst = inst[inst["instrument"] == "outright_repo"].copy()
    if len(inst) == 0:
        if verbose:
            print(f"    [PBOC-OUTRIGHT] no outright_repo instruments in range", flush=True)
        return pd.DataFrame()

    inst["outright_repo_marker"] = 1
    inst["outright_repo_tenor_days"] = _tenor_days_vec(inst["tenor"])
    inst["outright_repo_tenor_label"] = inst["tenor"].fillna("")
    # vectorized f"{serial_year}#{serial_no}" — row-wise .apply is a
    # per-element cudf fallback storm
    no = inst["serial_no"].fillna("")
    ok = no.astype(str).str.len() > 0
    inst["outright_repo_serial"] = (
        inst["serial_year"].fillna("").astype(str) + "#" + no.astype(str)
    ).where(ok, "")
    inst = inst.rename(columns={
        "pub_date":  "date",
        "quantity":  "outright_repo_quantity",
    })
    keep = ["date", "outright_repo_marker", "outright_repo_quantity",
            "outright_repo_tenor_days", "outright_repo_tenor_label",
            "outright_repo_serial"]
    df = inst[keep].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").astype("datetime64[ns]")
    # no sort/reset here — groupby(as_index=False) re-sorts by date and
    # returns a fresh index
    df = df.dropna(subset=["date"])

    df = df.groupby("date", as_index=False).agg({
        "outright_repo_marker":          "max",
        "outright_repo_quantity":        "sum",
        "outright_repo_tenor_days":      "min",
        "outright_repo_tenor_label":     lambda s: "|".join(s.astype(str)),
        "outright_repo_serial":          lambda s: "|".join(s.astype(str)),
    })

    if verbose:
        if len(df):
            print(f"    [PBOC-OUTRIGHT] {len(df)} outright-repo announcements, "
                  f"{df['date'].min().date()} → {df['date'].max().date()}", flush=True)
            if df["outright_repo_quantity"].notna().any():
                print(f"    [PBOC-OUTRIGHT] quantity range: "
                      f"{df['outright_repo_quantity'].min():g} → "
                      f"{df['outright_repo_quantity'].max():g} 亿元", flush=True)
        else:
            print(f"    [PBOC-OUTRIGHT] no records in range", flush=True)
    return df


def build_pboc_mlf_df(start_date=None, end_date=None, verbose=True):
    """Build a daily MLF marker frame from the combined instruments CSV."""
    if verbose:
        print(f"    [PBOC-MLF] reading {PBOC_INSTRUMENTS_CSV}", flush=True)

    inst = load_pboc_instruments_df(start_date=start_date, end_date=end_date, verbose=verbose)
    if inst is None or len(inst) == 0:
        if verbose:
            print(f"    [PBOC-MLF] no records in range", flush=True)
        return pd.DataFrame()

    inst = inst[inst["instrument"] == "MLF"].copy()
    if len(inst) == 0:
        if verbose:
            print(f"    [PBOC-MLF] no MLF instruments in range", flush=True)
        return pd.DataFrame()

    inst["mlf_marker"] = 1
    inst["mlf_tenor_days"] = _tenor_days_vec(inst["tenor"])
    inst["mlf_tenor_label"] = inst["tenor"].fillna("")
    no = inst["serial_no"].fillna("")
    ok = no.astype(str).str.len() > 0
    inst["mlf_serial"] = (
        inst["serial_year"].fillna("").astype(str) + "#" + no.astype(str)
    ).where(ok, "")
    inst = inst.rename(columns={
        "pub_date":  "date",
        "quantity":  "mlf_quantity",
    })
    keep = ["date", "mlf_marker", "mlf_quantity",
            "mlf_tenor_days", "mlf_tenor_label", "mlf_serial"]
    df = inst[keep].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").astype("datetime64[ns]")
    # no sort/reset here — groupby(as_index=False) re-sorts by date and
    # returns a fresh index
    df = df.dropna(subset=["date"])

    df = df.groupby("date", as_index=False).agg({
        "mlf_marker":        "max",
        "mlf_quantity":      "sum",
        "mlf_tenor_days":    "min",
        "mlf_tenor_label":   lambda s: "|".join(s.astype(str)),
        "mlf_serial":        lambda s: "|".join(s.astype(str)),
    })

    if verbose:
        if len(df):
            print(f"    [PBOC-MLF] {len(df)} MLF announcements, "
                  f"{df['date'].min().date()} → {df['date'].max().date()}", flush=True)
            if df["mlf_quantity"].notna().any():
                print(f"    [PBOC-MLF] quantity range: "
                      f"{df['mlf_quantity'].min():g} → "
                      f"{df['mlf_quantity'].max():g} 亿元", flush=True)
        else:
            print(f"    [PBOC-MLF] no records in range", flush=True)
    return df

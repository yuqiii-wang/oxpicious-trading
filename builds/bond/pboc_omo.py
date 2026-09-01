"""builds.bond.pboc_omo — PBoC OMO / outright-repo / MLF daily builders.

All three builders derive from the combined instruments CSV:
  (1) build_pboc_omo_df            → stats.debt_omo
  (2) build_pboc_outright_repo_df  → stats.debt_outright_repo
      build_pboc_mlf_df            → stats.debt_mlf
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from builds.bond.instruments import (
    load_pboc_instruments_df,
    parse_duration_to_days,
)
from builds.bond.paths import PBOC_INSTRUMENTS_CSV


# ============================================================================
# (1) PBoC OMO transaction announcements  → daily OMO rate / qty / tenor
# ============================================================================
def build_pboc_omo_df(start_date=None, end_date=None, verbose=True):
    """Build a daily OMO operations frame from the combined instruments CSV.

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

    inst = inst[inst["category"] == "omo_transaction"].copy()
    if len(inst) == 0:
        if verbose:
            print(f"    [PBOC-OMO] no omo_transaction records in range", flush=True)
        return pd.DataFrame()

    rows = []
    for pub_date, sub in inst.groupby(inst["pub_date"].dt.normalize()):
        # no reset_index: .iloc[0] below is positional — labels are irrelevant
        repo_entries = sub[sub["instrument"] == "reverse_repo"]
        has_reverse_repo: bool = len(repo_entries) > 0

        if has_reverse_repo:
            r = repo_entries.iloc[0]
            primary_rate = r["rate"]
            primary_qty = r["quantity"]
            primary_tenor = r["tenor"] or ""
        else:
            primary_rate = np.nan
            primary_qty = np.nan
            primary_tenor = ""

        rr_mlf = sub[sub["instrument"].isin(["reverse_repo", "MLF"])]
        all_rates = [f"{v:g}" for v in rr_mlf["rate"].dropna().tolist()]
        all_tenors = [str(t) for t in rr_mlf["tenor"].tolist() if t]
        all_qtys = [f"{v:g}" for v in rr_mlf["quantity"].dropna().tolist()]
        dur_qty_pairs = [
            f"{t}:{v:g}"
            for t, v in zip(rr_mlf["tenor"], rr_mlf["quantity"])
            if t and pd.notna(v)
        ]

        rows.append({
            "date":                  pub_date,
            "omo_rate":              primary_rate,
            "omo_quantity":          primary_qty,
            "omo_tenor_days":        parse_duration_to_days(primary_tenor),
            "omo_tenor_label":       primary_tenor,
            "omo_all_rates":         "|".join(all_rates),
            "omo_all_tenors":       "|".join(all_tenors),
            "omo_all_quantities":    "|".join(all_qtys),
            "omo_dur_qty_pairs":     "|".join(dur_qty_pairs),
            "omo_has_reverse_repo":  has_reverse_repo,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    # Only reverse-repo records feed the OMO table — apply the flag filter
    # FIRST so the per-date dedup needs neither a flag sort nor a second
    # re-filter (stable sort keeps first-occurrence order within a date).
    df = df[df["omo_has_reverse_repo"] == True]
    df = df.sort_values("date", kind="stable") \
           .drop_duplicates(subset=["date"], keep="first") \
           .reset_index(drop=True)
    df = df.drop(columns=["omo_has_reverse_repo"])

    if verbose:
        if len(df):
            print(f"    [PBOC-OMO] parsed {len(df)} daily OMO records, "
                  f"{df['date'].min().date()} → {df['date'].max().date()}", flush=True)
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
    inst["outright_repo_tenor_days"] = inst["tenor"].map(parse_duration_to_days)
    inst["outright_repo_tenor_label"] = inst["tenor"].fillna("")
    inst["outright_repo_serial"] = inst.apply(
        lambda r: f"{r['serial_year']}#{r['serial_no']}" if r.get("serial_no") else "",
        axis=1,
    )
    inst = inst.rename(columns={
        "pub_date":  "date",
        "quantity":  "outright_repo_quantity",
    })
    keep = ["date", "outright_repo_marker", "outright_repo_quantity",
            "outright_repo_tenor_days", "outright_repo_tenor_label",
            "outright_repo_serial"]
    df = inst[keep].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
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
    inst["mlf_tenor_days"] = inst["tenor"].map(parse_duration_to_days)
    inst["mlf_tenor_label"] = inst["tenor"].fillna("")
    inst["mlf_serial"] = inst.apply(
        lambda r: f"{r['serial_year']}#{r['serial_no']}" if r.get("serial_no") else "",
        axis=1,
    )
    inst = inst.rename(columns={
        "pub_date":  "date",
        "quantity":  "mlf_quantity",
    })
    keep = ["date", "mlf_marker", "mlf_quantity",
            "mlf_tenor_days", "mlf_tenor_label", "mlf_serial"]
    df = inst[keep].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
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

"""builds.bond.repo_lifecycle — Repo lifecycle tracking builder.

Builds stats.debt_repo (running cumulative repo balance) from OMO records:
  - repo_start_quantity: amount injected on repo start date
  - repo_end_quantity:   amount withdrawn on repo end date (negative)
  - repo_net_injection:  daily net money injection (start - end)
  - repo_cumulative:     cumulative outstanding repo balance
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd


def build_repo_lifecycle_df(omo_df):
    """Build repo lifecycle data from OMO records.

    NOTE: repo_cumulative depends on the FULL chronological OMO history.
    Callers must pass the full omo_df (not a missing-dates-only subset) so
    the cumulative sum is correct. The INSERT step then filters to missing
    dates.
    """
    if omo_df is None or len(omo_df) == 0:
        return pd.DataFrame()

    repo_legs = []
    for _, row in omo_df.iterrows():
        start_date = row['date']
        qty = row['omo_quantity']
        tenor_days = row['omo_tenor_days']
        if pd.isna(qty) or pd.isna(tenor_days):
            continue

        try:
            tenor_days = int(tenor_days)
            qty = float(qty)
        except (ValueError, TypeError):
            continue

        end_date = start_date + timedelta(days=tenor_days)

        repo_legs.append({
            'date': start_date,
            'repo_start_quantity': qty,
            'repo_end_quantity': 0,
        })
        repo_legs.append({
            'date': end_date,
            'repo_start_quantity': 0,
            'repo_end_quantity': -qty,
        })

    if not repo_legs:
        return pd.DataFrame()

    legs_df = pd.DataFrame(repo_legs)
    daily = legs_df.groupby('date').agg({
        'repo_start_quantity': 'sum',
        'repo_end_quantity': 'sum',
    }).reset_index()

    daily['repo_net_injection'] = daily['repo_start_quantity'] + daily['repo_end_quantity']
    daily['repo_cumulative'] = daily['repo_net_injection'].cumsum()

    return daily

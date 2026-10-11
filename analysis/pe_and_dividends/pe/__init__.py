"""PE-side logic for analysis.pe_and_dividends.

  - pe.compute     — clean_pe: raw PE with the invalid-value rule
                     applied (PE <= 0 / NULL → NaN)
  - pe.config      — analysis.pe identity + the pe_pct[_streaks]
                     bands-and-streaks settings
  - pe.pct_bands   — monthly trailing percentile BANDS of pe
                     (analysis.pe_pct)
  - pe.pct_streaks — band-break excursion STREAKS audited against those
                     bands (analysis.pe_pct_streaks — the
                     mov_ave_high_low_pct[_streaks] pattern)
"""
from analysis.pe_and_dividends.pe.compute import clean_pe

__all__ = [
    "clean_pe",
]

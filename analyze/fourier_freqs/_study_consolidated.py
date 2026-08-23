"""Study round 2: consolidated periodic-pattern score with NULL-CORRECTED
recurrence.

Round-1 findings:
  - extrema-evidence recurrence works on trend+noise (sine+trend case
    peaks at 10d, where raw ACF is trend-diluted to 0.375),
  - BUT pure noise also shows spurious recurrence ~0.15-0.3 at the
    prominence-filtered extrema characteristic scale (7-16d) — needs a
    null model,
  - 000300 shows a real ~28-58d recurring family (way beyond its own
    noise characteristic scale ~13d).

Round-2 variants:
  recV3 — excess over an Erlang(2, lambda=M/N) null: expected evidence
           count if the M extrema were a Poisson process (analytic).
  recV4 — excess over a rolling-median baseline of the evidence
           histogram itself (spike-over-smooth-baseline; kills broad
           noise families, keeps clustered-gap spikes).
  recACFb — ACF recurrence of the FFT band-passed residual (bins with
           period <= N/4 kept, trend removed): coherence without
           trend-dilution. Candidate gate/weight vs noise.

Score candidates (noticeability x recurrence):
  S_V4  = amp/sBand * recV4
  S_V3  = amp/sBand * recV3
  S_gB  = amp/sBand * sqrt(recV4 * recACFb)   (soft AND with band ACF)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import numpy as np
import pandas as pd
import psycopg2
from scipy.signal import find_peaks

pd.set_option("display.width", 220)

rng = np.random.default_rng(42)

TOL = 0.15
K_PROM = 1.5


# ---------------------------------------------------------------------------
#  Extrema evidence
# ---------------------------------------------------------------------------
def extrema_evidence(w: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Return (gaps, period-estimate pool, n_extrema).

    Pool = consecutive-gap sums (full cycles) + doubled single gaps
    (half-cycle corroboration). Both are Gamma(2,.)-distributed under a
    Poisson extrema null.
    """
    N = len(w)
    sd = float(np.std(np.diff(w)))
    hi, _ = find_peaks(w, prominence=K_PROM * sd)
    lo, _ = find_peaks(-w, prominence=K_PROM * sd)
    idx = np.concatenate([hi, lo])
    typ = np.concatenate([np.ones(len(hi)), -np.ones(len(lo))])
    order = np.argsort(idx)
    idx, typ = idx[order], typ[order]
    if len(idx) > 1:
        keep = np.concatenate([[True], np.diff(typ) != 0])
        idx = idx[keep]
    gaps = np.diff(idx).astype(float) if len(idx) > 1 else np.array([])
    pool = (
        np.concatenate([gaps[:-1] + gaps[1:], 2 * gaps])
        if len(gaps) > 1
        else np.array([])
    )
    return gaps, pool, int(len(idx))


def evidence_counts(pool: np.ndarray, N: int) -> pd.Series:
    days = np.arange(2, N // 2 + 1)
    if len(pool) == 0:
        return pd.Series(0.0, index=days)
    hit = np.abs(pool[:, None] - days[None, :]) <= TOL * days[None, :]
    return pd.Series(hit.sum(axis=0), index=days)


def erlang_null(pool_size: int, M: int, N: int) -> pd.Series:
    """Expected evidence count under a Poisson-extrema null.

    Extrema rate lambda = M/N per day; each pool entry (sum of two
    exponential gaps) ~ Erlang(2, lambda) with CDF F(x) = 1 - e^-lx(1+lx).
    """
    days = np.arange(2, N // 2 + 1)
    if M == 0:
        return pd.Series(0.0, index=days)
    lam = M / N
    a, b = days * (1 - TOL), days * (1 + TOL)
    fa = 1.0 - np.exp(-lam * a) * (1.0 + lam * a)
    fb = 1.0 - np.exp(-lam * b) * (1.0 + lam * b)
    return pd.Series(pool_size * (fb - fa), index=days)


def rolling_median_baseline(ev: pd.Series, half: int = 5) -> pd.Series:
    return ev.rolling(2 * half + 1, center=True, min_periods=1).median()


# ---------------------------------------------------------------------------
#  Amplitude + ACF factors
# ---------------------------------------------------------------------------
def merged_amp(w: np.ndarray) -> pd.Series:
    N = len(w)
    x = w - w.mean()
    X = np.abs(np.fft.rfft(x))
    amp = X[1:] * 2.0 / N
    ks = np.arange(1, N // 2 + 1)
    days = np.round(N / ks).astype(int)
    return np.sqrt(pd.Series(amp**2, index=days).groupby(level=0).sum())


def band_residual(w: np.ndarray) -> np.ndarray:
    """FFT band-pass residual: keep bins with period <= ~N/4 (remove trend).

    Remove bins k = 0..3 (periods >= N/4); keep k >= 4. For N=255 that
    keeps periods <= 51d — the swing band the extrema audit lives in.
    """
    N = len(w)
    x = w - w.mean()
    X = np.fft.rfft(x)
    X[:4] = 0.0
    return np.fft.irfft(X, N)


def ma_residual(w: np.ndarray) -> np.ndarray:
    """Centered-MA detrended residual (frontend-portable band-pass proxy).

    residual = w - centeredMA(w, L) with L = odd(round(N/4)) — removes
    periods >= ~N/4 like the FFT band-pass, but computable in O(N*L)
    without an FFT. Edge windows shrink.
    """
    N = len(w)
    L = max(3, (N // 4) | 1)
    half = L // 2
    ma = np.empty(N)
    cs = np.concatenate([[0.0], np.cumsum(w)])
    for i in range(N):
        lo = max(0, i - half)
        hi = min(N, i + half + 1)
        ma[i] = (cs[hi] - cs[lo]) / (hi - lo)
    return w - ma


def acf_recurrence(x: np.ndarray) -> pd.Series:
    N = len(x)
    xc = x - x.mean()
    denom = float(np.dot(xc, xc))
    if denom <= 0:
        return pd.Series(0.0, index=np.arange(2, N // 2 + 1))
    acf = np.correlate(xc, xc, mode="full")[N - 1:] / denom
    tau = 1.96 / np.sqrt(N)
    days = np.arange(2, N // 2 + 1)
    vals = np.zeros(len(days))
    for i, d in enumerate(days):
        m = np.arange(1, (N - d) // d + 1)
        if len(m):
            vals[i] = float((acf[m * d] >= tau).mean())
    return pd.Series(vals, index=days)


def acf_band_spectral(w: np.ndarray) -> np.ndarray:
    """Band ACF via Wiener-Khinchin from the amplitude spectrum.

    acf_band(tau) = (1 - tau/N) * sum_{k>=4} (amp_k^2/2) cos(2 pi k tau / N)
                    / sum_{k>=4} amp_k^2/2
    Same quantity the frontend computes from the STORED spectrum (no FFT
    needed client-side). Triangle factor approximates the biased
    time-domain estimator.
    """
    N = len(w)
    x = w - w.mean()
    X = np.abs(np.fft.rfft(x))
    amp = X[1:] * 2.0 / N  # k = 1..N//2
    pw = amp**2 / 2.0
    band = pw[3:]  # k >= 4 (period <= N/4)
    denom = band.sum()
    if denom <= 0:
        return np.zeros(N)
    ks = np.arange(4, len(amp) + 1)
    out = np.zeros(N)
    for tau in range(N):
        out[tau] = (1.0 - tau / N) * float(
            np.dot(band, np.cos(2 * np.pi * ks * tau / N))
        ) / denom
    return out


def acf_recurrence_from_acf(acf: np.ndarray, N: int) -> pd.Series:
    tau = 1.96 / np.sqrt(N)
    days = np.arange(2, N // 2 + 1)
    vals = np.zeros(len(days))
    for i, d in enumerate(days):
        m = np.arange(1, (N - d) // d + 1)
        if len(m):
            vals[i] = float((acf[m * d] >= tau).mean())
    return pd.Series(vals, index=days)


# ---------------------------------------------------------------------------
#  Report
# ---------------------------------------------------------------------------
def report(name: str, w: np.ndarray) -> None:
    N = len(w)
    amp = merged_amp(w)
    sig_band = float(np.sqrt((amp[amp.index <= N // 4] ** 2).sum() / 2.0))
    sig_tot = float((w - w.mean()).std())

    gaps, pool, M = extrema_evidence(w)
    ev = evidence_counts(pool, N)
    null3 = erlang_null(len(pool), M, N)
    maxrep = pd.Series(np.maximum((N - ev.index.values) // ev.index.values, 1),
                       index=ev.index)

    recEXT = (ev / maxrep).clip(upper=1.0)
    recV3 = ((ev - null3).clip(lower=0) / maxrep).clip(upper=1.0)
    recACFb = acf_recurrence(band_residual(w))
    # MA-detrend replica (frontend-portable) — must match recACFb
    recACFma = acf_recurrence(ma_residual(w))
    # spectral replica (circular ACF) — known too harsh on short windows
    recACFb_spec = acf_recurrence_from_acf(acf_band_spectral(w), N)

    df = pd.DataFrame({
        "ev": ev, "null": null3.round(1),
        "recEXT": recEXT, "recV3": recV3, "recACFb": recACFb,
        "recACFma": recACFma, "recACFspec": recACFb_spec,
        "amp/sB": amp / sig_band, "amp/sT": amp / sig_tot,
    })
    auditable = df.index <= N // 3
    df.loc[~auditable, ["recEXT", "recV3", "recACFb", "recACFma", "recACFspec"]] = np.nan
    df["S_EXT"] = df["amp/sB"] * df["recEXT"]
    df["S_V3"] = df["amp/sB"] * df["recV3"]
    df["S_gE"] = df["amp/sB"] * df["recEXT"] * df["recACFb"]
    df["S_gEma"] = df["amp/sB"] * df["recEXT"] * df["recACFma"]
    maxdiff = float((df["recACFb"] - df["recACFma"]).abs().max())

    print(f"\n=== {name} · N={N} · sTot={sig_tot:.0f} sBand={sig_band:.0f} "
          f"· extrema={M} pool={len(pool)} · max|ACFb-ACFma|={maxdiff:.4f} ===")
    show = df.dropna(subset=["recEXT"])
    print("-- top by S_gE (amp x evidence x band-ACF gate) --")
    print(show.sort_values("S_gE", ascending=False).head(8)
          .to_string(float_format=lambda v: f"{v:6.3f}"))
    probe = df.loc[[d for d in (5, 10, 14, 20, 28, 36, 40, 49, 58) if d in df.index]]
    print("-- probe days (FFT-band ACF vs MA-detrend ACF) --")
    print(probe[["ev", "recEXT", "recACFb", "recACFma", "amp/sB",
                 "S_gE", "S_gEma"]]
          .to_string(float_format=lambda v: f"{v:6.3f}"))


# ---------------------------------------------------------------------------
#  Cases
# ---------------------------------------------------------------------------
N = 255
t = np.arange(N)
sine = 100.0 * np.sin(2 * np.pi * t / 10.0)
noise = rng.normal(0, 20.0, N)

report("synthetic: pure 10d sine", sine.copy())
report("synthetic: 10d sine + strong trend + noise", (sine + 2.0 * t + noise).copy())
report("synthetic: pure noise", noise.copy())

conn = psycopg2.connect(host="127.0.0.1", port=9876, dbname="oxpicious-stats",
                        user="postgres", password="postgres")
cur = conn.cursor()
cur.execute("SELECT date, close FROM stats.index_basic_stats "
            "WHERE code = '000300' ORDER BY date")
close = np.array([r[1] for r in cur.fetchall()], dtype=np.float64)
cur.close()
conn.close()

for Nw in (255, 750, 1275):
    report(f"000300 latest {Nw}d", close[-Nw:].copy())
# non-latest window (guard against latest-window cherry-picking)
report("000300 750d ending 500d ago", close[-500 - 750:-500].copy())

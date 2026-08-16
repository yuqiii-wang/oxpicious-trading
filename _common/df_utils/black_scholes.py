"""Vectorized Black-76 implied volatility + Greeks (CPU numpy / GPU cupy).

Shared by builds.options.szse and builds.options.cffex. Replaces the
row-by-row QuantLib BlackCalculator + scipy brentq loop (~30+ min for
1.6M rows) with fully vectorized array math (~seconds).

The Black-76 model matches QuantLib's BlackCalculator exactly:
  F = S * exp(rT), D = exp(-rT)
  Call = D * (F*N(d1) - K*N(d2))
  Put  = D * (K*N(-d2) - F*N(-d1))
  d1 = [ln(F/K) + (sigma^2/2)T] / (sigma*sqrt(T)),  d2 = d1 - sigma*sqrt(T)

IV is solved with a vectorized safeguarded Newton method: a Newton step
when it stays inside the current bracket, a bisection step otherwise
(rows whose Newton step diverges — deep OTM options with tiny vega —
still converge via bisection). Rows whose market price violates the
no-arbitrage bounds (below intrinsic / above the sigma_max price) return
NaN, matching the old brentq failure semantics.

Greeks are analytical and match QuantLib's BlackCalculator
delta/gamma/thetaPerDay/vega/rho exactly (calibrated against
ql.BlackCalculator on a random grid to ~1e-15):

  delta = N(d1) (call) / N(d1)-1 (put)      — the discount factor
            cancels because forward = spot*exp(rT)
  gamma = phi(d1) / (spot*sigma*sqrt(T))    — same cancellation
  theta = r*V - r*S*delta - 0.5*sigma^2*S^2*gamma, per day — this is
            QL's theta(spot,T)/365 = -(ln(D)*V + ln(F/S)*S*delta
            + 0.5*var*S^2*gamma)/T/365 with F = S*exp(rT) substituted
  vega  = D*F*phi(d1)*sqrt(T), scaled by 0.01
  rho   = ±K*T*D*N(±d2), scaled by 0.01

GPU path is selected via the cuDF router (``should_use_gpu``) using the
"elementwise" op profile; cupyx.scipy.special.ndtr provides the normal
CDF on device.

USAGE
=====

    from _common.df_utils import compute_iv_and_greeks

    # SZSE convention (raw prices are x1000 / x10000 scaled):
    iv, delta, theta, gamma, vega, rho = compute_iv_and_greeks(df)

    # CFFEX convention (no scaling, CSV delta fallback):
    iv, delta, theta, gamma, vega, rho = compute_iv_and_greeks(
        df, price_scale=1.0, opt_scale=1.0, csv_delta_col="csv_delta",
    )
"""
from __future__ import annotations

import numpy as np
from scipy.special import ndtr as _scipy_ndtr

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_RISK_FREE_RATE = 0.02
DEFAULT_PRICE_SCALE = 1000.0   # SZSE: underlying_close / strike_price scale
DEFAULT_OPT_SCALE = 10000.0    # SZSE: option settle price scale

# IV solver bracket and precision (mirrors the old brentq call:
# brentq(objective, 1e-6, 5.0, xtol=1e-8)).
IV_SIGMA_MIN = 1e-6
IV_SIGMA_MAX = 5.0
IV_MAX_ITER = 60               # bisection fallback needs ~53 to exhaust
                               # [1e-6, 5.0]; 60 gives headroom.

_SQRT_2PI = float(np.sqrt(2.0 * np.pi))

# Lazy cache for cupy's ndtr: None = not probed, False = unavailable.
_cupy_ndtr = None


def _get_cupy_ndtr():
    """Lazy-import cupyx.scipy.special.ndtr. Returns callable or None."""
    global _cupy_ndtr
    if _cupy_ndtr is not None:
        return _cupy_ndtr if _cupy_ndtr is not False else None
    try:
        from cupyx.scipy.special import ndtr  # type: ignore[import-untyped]
        _cupy_ndtr = ndtr
    except Exception:
        _cupy_ndtr = False
        return None
    return _cupy_ndtr


# ---------------------------------------------------------------------------
# Core vectorized Black-76 price + Greeks
# ---------------------------------------------------------------------------
def _ndtr_all(d1, d2, use_gpu):
    """Compute N(d1), N(d2), N(-d1), N(-d2) on CPU or GPU.

    Returns cupy arrays when use_gpu, numpy arrays otherwise.
    """
    if not use_gpu:
        return _scipy_ndtr(d1), _scipy_ndtr(d2), _scipy_ndtr(-d1), _scipy_ndtr(-d2)

    import cupy as cp
    ndtr = _get_cupy_ndtr()
    if ndtr is not None:
        return ndtr(d1), ndtr(d2), ndtr(-d1), ndtr(-d2)
    # Fallback (cupyx ndtr unavailable): round-trip via scipy on CPU.
    d1h, d2h = cp.asnumpy(d1), cp.asnumpy(d2)
    return (cp.asarray(_scipy_ndtr(d1h)),
            cp.asarray(_scipy_ndtr(d2h)),
            cp.asarray(_scipy_ndtr(-d1h)),
            cp.asarray(_scipy_ndtr(-d2h)))


def bs_price_greeks(S, K, T, r, sigma, is_call, use_gpu=False):
    """Vectorized Black-76 price + Greeks (numpy or cupy arrays).

    Args:
        S:       spot prices array (n,)
        K:       strike prices array (n,)
        T:       times to expiry in years (n,)
        r:       risk-free rate (scalar)
        sigma:   volatility array (n,)
        is_call: bool array (n,)
        use_gpu: True -> cupy arrays, False -> numpy arrays

    Returns:
        (price, delta, gamma, vega, theta_perday, rho) — all (n,) arrays.
        vega and rho are scaled by 0.01 (1% shock), theta is per day —
        matching the old QuantLib call convention
        (calc.vega(T)*0.01, calc.rho(T)*0.01, calc.thetaPerDay).
    """
    if use_gpu:
        import cupy as xp
    else:
        xp = np

    sqrt_T = xp.sqrt(xp.maximum(T, 0.0))
    sigma_sqrt_T = sigma * sqrt_T
    sigma_sqrt_T = xp.where(sigma_sqrt_T > 0, sigma_sqrt_T, 1e-12)

    F = S * xp.exp(r * T)
    D = xp.exp(-r * T)

    d1 = (xp.log(xp.maximum(F / K, 1e-300)) + 0.5 * sigma**2 * T) / sigma_sqrt_T
    d2 = d1 - sigma_sqrt_T

    Nd1, Nd2, Nmd1, Nmd2 = _ndtr_all(d1, d2, use_gpu)
    n_d1 = xp.exp(-0.5 * d1**2) / _SQRT_2PI

    call = xp.asarray(is_call, dtype=bool)

    price = xp.where(call,
                     D * (F * Nd1 - K * Nd2),
                     D * (K * Nmd2 - F * Nmd1))

    # Delta w.r.t. spot (QuantLib BlackCalculator::delta with
    # forward = spot*exp(rT)): the discount cancels, leaving
    # N(d1) for calls / N(d1)-1 for puts.
    delta = xp.where(call, Nd1, Nd1 - 1.0)

    # Gamma (BlackCalculator::gamma): phi(d1) / (spot*sigma*sqrt(T));
    # the discount cancels identically (F*phi(d1) = K*phi(d2)).
    gamma = n_d1 / (S * sigma_sqrt_T)

    # Vega x 0.01 (BlackCalculator::vega * 0.01).
    vega = D * F * n_d1 * sqrt_T * 0.01

    # Theta per day (BlackCalculator::theta(spot,T)/365):
    #   -(ln(D)*V + ln(F/S)*S*delta + 0.5*sigma^2*T*S^2*gamma)/T
    # with ln(D) = -r*T and ln(F/S) = r*T by construction, i.e.
    #   r*V - r*S*delta - 0.5*sigma^2*S^2*gamma
    # (NOT the textbook Black-76 theta).
    theta = (r * price - r * S * delta
             - 0.5 * sigma**2 * S * S * gamma) / 365.0

    # Rho x 0.01 (BlackCalculator::rho * 0.01), Black-76 discount-rate rho:
    #   call:  K*T*D*N(d2)
    #   put:  -K*T*D*N(-d2)
    rho = xp.where(call,
                   K * T * D * Nd2,
                   -K * T * D * Nmd2) * 0.01

    return price, delta, gamma, vega, theta, rho


# ---------------------------------------------------------------------------
# Safeguarded Newton IV solver
# ---------------------------------------------------------------------------
def solve_iv_newton(S, K, T, r, market_price, is_call,
                    max_iter=IV_MAX_ITER, use_gpu=False):
    """Solve implied volatility via safeguarded Newton + bisection.

    Newton step when it lands inside the current bracket, bisection
    otherwise, so every solvable row converges (pure Newton diverges on
    deep OTM rows where vega ~ 0).

    Rows are NaN when inputs are invalid (T/price/S/K <= 0) or the
    market price is outside the no-arbitrage bracket
    [intrinsic, price(sigma_max)] — the same rows for which the old
    brentq(objective, 1e-6, 5.0) raised and produced NaN.

    Returns:
        (iv, delta, gamma, vega, theta, rho) — arrays (n,).
    """
    if use_gpu:
        import cupy as xp
    else:
        xp = np

    n = len(S)

    # Price at the bracket ends (needed for solvability).
    p_min, *_ = bs_price_greeks(
        S, K, T, r, xp.full(n, IV_SIGMA_MIN, dtype=xp.float64), is_call, use_gpu)
    p_max, *_ = bs_price_greeks(
        S, K, T, r, xp.full(n, IV_SIGMA_MAX, dtype=xp.float64), is_call, use_gpu)

    valid = ((T > 0) & (market_price > 0) & (S > 0) & (K > 0)
             & (market_price > p_min) & (market_price < p_max))

    S_s = xp.where(valid, S, 1.0)
    K_s = xp.where(valid, K, 1.0)
    T_s = xp.where(valid, T, 1.0)
    P_s = xp.where(valid, market_price, 0.5)

    lo = xp.full(n, IV_SIGMA_MIN, dtype=xp.float64)
    hi = xp.full(n, IV_SIGMA_MAX, dtype=xp.float64)
    sigma = xp.full(n, 0.2, dtype=xp.float64)
    prev_abs_diff = None

    with np.errstate(all="ignore"):
        for _ in range(max_iter):
            price, _, _, vega, _, _ = bs_price_greeks(
                S_s, K_s, T_s, r, sigma, is_call, use_gpu)

            diff = price - P_s
            abs_diff = xp.abs(diff)

            # Maintain bracket: model price is increasing in sigma.
            # >= / <= (not > / <) so an exact root (diff == +-0.0)
            # collapses the bracket onto sigma and the row freezes
            # there (otherwise bisection would wander off a perfect
            # root and stall at ulp-level residual).
            hi = xp.where(diff >= 0, sigma, hi)
            lo = xp.where(diff <= 0, sigma, lo)

            # Newton step (vega returned = dV/dsigma * 0.01).
            vega_raw = xp.where(vega > 0, vega / 0.01, 1e-12)
            newton = sigma - diff / vega_raw

            # Safeguard 1: Newton only when it stays inside the bracket.
            ok = (newton > lo) & (newton < hi)
            # Safeguard 2: Newton only when it improves on the previous
            # iteration (a pure Newton step can enter a 2-cycle inside
            # the bracket without ever tightening it).
            if prev_abs_diff is not None:
                ok = ok & (abs_diff < prev_abs_diff)
            sigma = xp.where(ok, newton, 0.5 * (lo + hi))
            prev_abs_diff = abs_diff

            if use_gpu:
                max_diff = float(xp.max(abs_diff).get())
            else:
                max_diff = float(xp.max(abs_diff))
            if max_diff < 1e-10:
                break

    _, delta, gamma, vega, theta, rho = bs_price_greeks(
        S_s, K_s, T_s, r, sigma, is_call, use_gpu)

    nan = xp.full(n, xp.nan)
    iv = xp.where(valid, sigma, nan)
    delta = xp.where(valid, delta, nan)
    gamma = xp.where(valid, gamma, nan)
    vega = xp.where(valid, vega, nan)
    theta = xp.where(valid, theta, nan)
    rho = xp.where(valid, rho, nan)

    return iv, delta, gamma, vega, theta, rho


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def compute_iv_and_greeks(
    df,
    use_gpu=None,
    *,
    price_scale=DEFAULT_PRICE_SCALE,
    opt_scale=DEFAULT_OPT_SCALE,
    risk_free_rate=DEFAULT_RISK_FREE_RATE,
    csv_delta_col=None,
    verbose=True,
):
    """Compute implied volatility and Greeks for an options DataFrame.

    Vectorized replacement for the row-by-row QuantLib loop in
    builds.options.szse / builds.options.cffex. CPU/GPU is auto-routed
    via the project's ``should_use_gpu`` when ``use_gpu`` is None.

    Args:
        df: DataFrame with columns:
            - underlying_close: spot price of the underlying
            - strike_price: strike price
            - settle: option settlement price
            - days_to_expiry: calendar days to expiry
            - option_type: 'CALL' or 'PUT'
        use_gpu: True -> force GPU, False -> force CPU, None -> auto.
        price_scale: divisor for underlying_close / strike_price
            (SZSE = 1000.0, CFFEX = 1.0).
        opt_scale: divisor for settle (SZSE = 10000.0, CFFEX = 1.0).
        risk_free_rate: annualized continuously-compounded rate.
        csv_delta_col: optional column with exchange-provided delta
            (CFFEX); fills NaN deltas (rows where IV is unsolvable).
        verbose: print the CPU/GPU router decision.

    Returns:
        (iv, delta, theta, gamma, vega, rho) — six numpy arrays.
    """
    from _common.df_utils import should_use_gpu

    S = df["underlying_close"].values.astype(np.float64) / price_scale
    K = df["strike_price"].values.astype(np.float64) / price_scale
    P = df["settle"].values.astype(np.float64) / opt_scale
    T = df["days_to_expiry"].values.astype(np.float64) / 365.0
    is_call = (df["option_type"].values == "CALL")

    if use_gpu is None:
        use_gpu = should_use_gpu(df, op_type="elementwise", verbose=verbose)

    if use_gpu:
        import cupy as cp
        args = [cp.asarray(a) for a in (S, K, T, P, is_call)]
        iv, delta, gamma, vega, theta, rho = solve_iv_newton(
            *args[:3], risk_free_rate, args[3], args[4], use_gpu=True)
        iv, delta = cp.asnumpy(iv), cp.asnumpy(delta)
        gamma, vega = cp.asnumpy(gamma), cp.asnumpy(vega)
        theta, rho = cp.asnumpy(theta), cp.asnumpy(rho)
    else:
        iv, delta, gamma, vega, theta, rho = solve_iv_newton(
            S, K, T, risk_free_rate, P, is_call, use_gpu=False)

    # Exchange-provided delta fallback (CFFEX): fill unsolvable rows.
    if csv_delta_col is not None and csv_delta_col in df.columns:
        csv_delta = df[csv_delta_col].values.astype(np.float64)
        nan_mask = ~np.isfinite(delta)
        delta[nan_mask] = csv_delta[nan_mask]

    return iv, delta, theta, gamma, vega, rho

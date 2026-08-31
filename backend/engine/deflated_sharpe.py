"""Deflated Sharpe Ratio — Bailey & López de Prado (2014) (PLATFORM-SPEC.md §4.5 item 6).

Given a series of periodic returns (daily here), the number of trials that
produced the best-looking strategy, and the returns' skew/kurtosis, DSR is
the probability that the observed Sharpe exceeds the expected maximum
Sharpe of `trials` random strategies:

    SR0  = sqrt(V[SR]) * ((1-γ) Φ⁻¹(1-1/N) + γ Φ⁻¹(1-1/(N e)))      expected max SR under H0
    DSR  = Φ( (SR - SR0) * sqrt(T-1) / sqrt(1 - γ3 SR + (γ4-1)/4 SR²) )

with γ = Euler–Mascheroni, γ3 skew, γ4 kurtosis (non-excess), T observations.
V[SR] across trials is approximated by the variance of the SR estimator
itself when only one series is available (the conservative choice).
"""

from __future__ import annotations

import math

import numpy as np

EULER_GAMMA = 0.5772156649015329


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation, |err| < 1.2e-9)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - plow:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def moments(returns) -> dict:
    r = np.asarray(returns, dtype=float)
    n = r.size
    if n < 2:
        return {"n": int(n), "mean": 0.0, "std": 0.0, "skew": 0.0, "kurtosis": 3.0, "sharpe": 0.0}
    mean = float(r.mean())
    std = float(r.std(ddof=1))
    if std == 0:
        return {"n": int(n), "mean": mean, "std": 0.0, "skew": 0.0, "kurtosis": 3.0, "sharpe": 0.0}
    z = (r - mean) / std
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4))
    return {"n": int(n), "mean": mean, "std": std, "skew": skew, "kurtosis": kurt, "sharpe": mean / std}


def expected_max_sharpe(var_sr: float, trials: int) -> float:
    if trials <= 1 or var_sr <= 0:
        return 0.0
    n = float(trials)
    return math.sqrt(var_sr) * ((1 - EULER_GAMMA) * _norm_ppf(1 - 1 / n) + EULER_GAMMA * _norm_ppf(1 - 1 / (n * math.e)))


def probabilistic_sharpe(sr: float, sr_benchmark: float, n: int, skew: float, kurt: float) -> float:
    if n < 2:
        return 0.0
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr * sr))
    return _norm_cdf((sr - sr_benchmark) * math.sqrt(n - 1) / denom)


def deflated_sharpe(returns, trials: int, periods_per_year: int = 252) -> dict:
    """Per-period Sharpe deflated for `trials` strategies tried. Returns
    the per-period and annualised SR, the expected max SR under the null,
    and DSR = P(SR > SR0)."""
    m = moments(returns)
    n, sr = m["n"], m["sharpe"]
    # Variance of the SR estimator for one series (Lo 2002), used as V[SR].
    var_sr = (1 + 0.5 * sr * sr) / max(n - 1, 1) if n > 1 else 0.0
    sr0 = expected_max_sharpe(var_sr, max(1, int(trials)))
    dsr = probabilistic_sharpe(sr, sr0, n, m["skew"], m["kurtosis"]) if n > 1 else 0.0
    return {
        "observations": n, "trials": int(trials),
        "sharpe": sr, "sharpeAnnualized": sr * math.sqrt(periods_per_year),
        "expectedMaxSharpe": sr0, "skew": m["skew"], "kurtosis": m["kurtosis"],
        "dsr": dsr,
    }

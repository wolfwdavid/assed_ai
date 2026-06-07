"""
Bayesian sensor-anomaly analysis (PyMC) — optional enrichment layer.

Scout always detects out-of-range readings deterministically (3-sigma), so
DETECTION never depends on this module. When PyMC is available, this fits a
ROBUST Student-T model per sensor column and upgrades each finding with a
calibrated anomaly probability and the sensor's 95% credible normal range.

Why robust/Bayesian beats naive 3-sigma here:
- The outliers we are hunting INFLATE the naive standard deviation, which widens
  the very threshold meant to catch them (a 99 C over-temp reading can hide
  inside a corrupted "normal" band). A heavy-tailed Student-T likelihood
  down-weights extreme points, so the estimated centre/scale reflect the TRUE
  in-spec behaviour of the sensor — not the faults.
- The posterior yields a per-reading probability ("97% likely anomalous") and an
  uncertainty-aware normal range, both of which are explainable to an auditor.

Every public call is best-effort: on ANY problem it returns None / False and
Scout falls back to the deterministic result. No decision depends on the model
being installed, and the demo never breaks. A fixed random seed makes the
reported numbers reproducible across runs.
"""
from __future__ import annotations

import numpy as np

SEED = 42
DRAWS = 400
TUNE = 400
CHAINS = 2
MIN_POINTS = 8          # below this, defer to deterministic detection
MAX_POINTS = 2000       # subsample very large columns to keep sampling fast
_POSTERIOR_STRIDE = 4   # thin the posterior when scoring values (speed)


def available() -> bool:
    """True only if PyMC (and SciPy, which we use for tail probabilities) import."""
    try:
        import pymc  # noqa: F401
        import scipy.stats  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _fit(values: np.ndarray):
    """Fit a robust Student-T model; return (mu_samples, sigma_samples, nu_samples) or None."""
    import pymc as pm

    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if x.size > MAX_POINTS:
        rng = np.random.default_rng(SEED)
        x = rng.choice(x, size=MAX_POINTS, replace=False)
    if x.size < MIN_POINTS:
        return None
    spread = float(np.std(x))
    if spread == 0:
        return None
    centre = float(np.median(x))

    with pm.Model():
        mu = pm.Normal("mu", mu=centre, sigma=2 * spread + 1)
        sigma = pm.HalfNormal("sigma", sigma=spread + 1)
        nu = pm.Gamma("nu", alpha=2, beta=0.1)        # heavy-tailed -> robust to outliers
        pm.StudentT("obs", nu=nu, mu=mu, sigma=sigma, observed=x)
        idata = pm.sample(draws=DRAWS, tune=TUNE, chains=CHAINS, cores=1,
                          progressbar=False, compute_convergence_checks=False,
                          random_seed=SEED)
    post = idata.posterior
    mu_s = post["mu"].values.reshape(-1)
    sig_s = post["sigma"].values.reshape(-1)
    nu_s = post["nu"].values.reshape(-1)
    return mu_s, sig_s, nu_s


def warmup() -> bool:
    """Prime PyTensor's compile cache so the first real audit run is fast.

    Safe to call in a background thread at startup. Returns True on success.
    """
    if not available():
        return False
    try:
        rng = np.random.default_rng(SEED)
        dummy = rng.normal(70, 4, size=200)
        return _fit(dummy) is not None
    except Exception:  # noqa: BLE001
        return False


def analyze_column(all_values, query_values, threshold: float = 3.0) -> dict | None:
    """Robust Bayesian analysis of one sensor column.

    Args:
        all_values:   every reading in the column (the model is fit on these).
        query_values: the specific readings to score (the deterministic outliers).
    Returns a dict with robust vs naive parameters, the 95% credible normal range,
    and an anomaly probability per query value — or None on any failure.
    """
    if not available():
        return None
    try:
        from scipy import stats

        fit = _fit(all_values)
        if fit is None:
            return None
        mu_s, sig_s, nu_s = fit

        # Thin the posterior for fast tail-probability integration.
        i = slice(None, None, _POSTERIOR_STRIDE)
        mu_t, sig_t, nu_t = mu_s[i], sig_s[i], nu_s[i]

        robust_mu = float(np.mean(mu_s))
        robust_sigma = float(np.mean(sig_s))
        x = np.asarray(all_values, dtype=float)
        x = x[np.isfinite(x)]
        naive_mu = float(np.mean(x))
        naive_sigma = float(np.std(x))

        # 95% credible normal range = posterior-averaged 2.5/97.5 predictive quantiles.
        lo = float(np.mean(stats.t.ppf(0.025, df=nu_t, loc=mu_t, scale=sig_t)))
        hi = float(np.mean(stats.t.ppf(0.975, df=nu_t, loc=mu_t, scale=sig_t)))

        # Per-value anomaly probability: 1 - posterior-averaged two-sided tail prob.
        probs = []
        for v in query_values:
            v = float(v)
            cdf = stats.t.cdf(v, df=nu_t, loc=mu_t, scale=sig_t)
            tail = float(np.mean(2.0 * np.minimum(cdf, 1.0 - cdf)))
            probs.append(round(max(0.0, min(1.0, 1.0 - tail)), 3))

        return {
            "method": "bayesian",
            "model": "PyMC robust Student-T",
            "robust_mu": round(robust_mu, 2),
            "robust_sigma": round(robust_sigma, 2),
            "naive_mu": round(naive_mu, 2),
            "naive_sigma": round(naive_sigma, 2),
            "credible_low": round(lo, 1),
            "credible_high": round(hi, 1),
            "anomaly_probs": probs,
        }
    except Exception:  # noqa: BLE001
        return None

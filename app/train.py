"""
Train and evaluate the thermal-prediction models.

Targets
  t_max        peak cell temperature over the operating window  [C]   (regression)
  delta_t_max  worst cell-to-cell temperature spread             [C]   (regression)
  hotspot_cell cell that runs hottest on average (C1..C10)             (classification)

Evaluation protocol
  1. Out-of-distribution (OOD) hold-out: every scenario with ambient > 42 C is removed
     before training and used only to test generalisation to hotter weather.
  2. Remaining rows: random 80/20 train/test split (seed 42).
  3. T_max candidates
       physics_closed_form_baseline  rule-based, no training (app.physics.quick_estimate)
       linear_regression             pure ML on the 10 raw inputs
       random_forest                 pure ML on the 10 raw inputs
       hgb_pure_ml                   pure ML on the 10 raw inputs
       greybox_hgb (deployed)        physics prior + HGB residual, monotonic constraints
  4. A split-conformal residual quantile gives a 90 % prediction interval.
  5. The deployed bundle is refit on ALL rows with the selected settings.

Usage:  python -m app.train [--data data/btms_synthetic.csv] [--deploy-only]
"""
from __future__ import annotations

import argparse
import io
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import (HistGradientBoostingClassifier,
                              HistGradientBoostingRegressor,
                              RandomForestRegressor)
from sklearn.linear_model import LinearRegression
from sklearn.metrics import (accuracy_score, mean_absolute_error,
                             mean_squared_error, r2_score, top_k_accuracy_score)
from sklearn.model_selection import cross_val_predict, train_test_split

from app.greybox import PRIOR_COL, GreyBoxRegressor
from app.physics import quick_estimate

MODEL_VERSION = "1.1.0"
RAW = ["is_charging", "c_rate", "soc_start", "soh", "ambient_temp", "inlet_temp",
       "start_temp", "reynolds", "phi_pct", "duration_min"]
FEATURES = RAW + [PRIOR_COL]
CELLS = [f"C{i}" for i in range(1, 11)]
OOD_AMBIENT = 42.0
SEED = 42

HGB_REG = dict(max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
               min_samples_leaf=20, l2_regularization=0.1, random_state=SEED)
HGB_CLF = dict(max_iter=60, learning_rate=0.1, max_leaf_nodes=15,
               min_samples_leaf=30, random_state=SEED)   # 250 iterations tested: same accuracy, 3.5x slower
# Monotonic constraints (+1 increasing, -1 decreasing) on the variables the optimiser
# searches over. For T_max they act on the residual model with the physics prior held
# fixed; the optimiser additionally applies a conservative envelope along Reynolds
# number (ThermalModel.monotone_in_re), so it cannot exploit "less flow -> cooler pack".
MONO_TMAX = {"c_rate": 1, "inlet_temp": 1, "reynolds": -1}
MONO_DT = {"c_rate": 1, "reynolds": -1}


def mono(spec: dict, cols=FEATURES) -> list[int]:
    return [spec.get(f, 0) for f in cols]


def featurise(df: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame({
        "is_charging": (df["mode"] == "charge").astype(float).to_numpy(),
        **{c: df[c].astype(float).to_numpy() for c in RAW[1:]},
    })
    x[PRIOR_COL] = quick_estimate({**{c: df[c].to_numpy() for c in RAW[1:]}, "mode": df["mode"].to_numpy()})
    return x


def tmax_model() -> GreyBoxRegressor:
    return GreyBoxRegressor(HistGradientBoostingRegressor(monotonic_cst=mono(MONO_TMAX), **HGB_REG))


def dt_model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(monotonic_cst=mono(MONO_DT), **HGB_REG)


def reg_metrics(y, p) -> dict:
    return {"mae": round(float(mean_absolute_error(y, p)), 4),
            "rmse": round(float(np.sqrt(mean_squared_error(y, p))), 4),
            "r2": round(float(r2_score(y, p)), 4)}


def conformal_q(resid_abs: np.ndarray, coverage: float = 0.90) -> float:
    n = len(resid_abs)
    level = min(1.0, np.ceil((n + 1) * coverage) / n)
    return float(np.quantile(resid_abs, level, method="higher"))


def _size_kb(m) -> float:
    buf = io.BytesIO()
    joblib.dump(m, buf)
    return round(len(buf.getvalue()) / 1024, 1)


class _Prior:
    """Wrap the closed-form estimate so it can be scored like a model."""

    def fit(self, X, y):
        return self

    def predict(self, X):
        return X[PRIOR_COL].to_numpy()


class _Raw:
    """Restrict a model to the 10 raw inputs (pure-ML candidates)."""

    def __init__(self, m):
        self.m = m

    def fit(self, X, y):
        self.m.fit(X[RAW], y)
        return self

    def predict(self, X):
        return self.m.predict(X[RAW])


def fit_bundle(x: pd.DataFrame, df: pd.DataFrame) -> tuple[dict, dict]:
    y_cls = df["hotspot_cell"].map(CELLS.index).values
    models = {
        "t_max": tmax_model().fit(x, df["t_max"]),
        "delta_t": dt_model().fit(x, df["delta_t_max"]),
        "hotspot": HistGradientBoostingClassifier(**HGB_CLF).fit(x, y_cls),
    }
    oof_t = cross_val_predict(tmax_model(), x, df["t_max"], cv=5)
    oof_d = cross_val_predict(dt_model(), x, df["delta_t_max"], cv=5)
    intervals = {"t_max_q90": conformal_q(np.abs(df["t_max"].to_numpy() - oof_t)),
                 "delta_t_q90": conformal_q(np.abs(df["delta_t_max"].to_numpy() - oof_d))}
    return models, intervals


def evaluate(df: pd.DataFrame, fig_dir: Path) -> dict:
    ood = df[df["ambient_temp"] > OOD_AMBIENT]
    ind = df[df["ambient_temp"] <= OOD_AMBIENT]
    tr, te = train_test_split(ind, test_size=0.2, random_state=SEED)
    xtr, xte, xood = featurise(tr), featurise(te), featurise(ood)
    res = {"rows": {"total": len(df), "train": len(tr), "test": len(te), "ood_test": len(ood)}}
    preds = {}

    cands = {
        "t_max": {
            "physics_closed_form_baseline": _Prior(),
            "linear_regression": _Raw(LinearRegression()),
            "random_forest": _Raw(RandomForestRegressor(n_estimators=100, min_samples_leaf=3,
                                                        n_jobs=1, random_state=SEED)),
            "hgb_pure_ml": _Raw(HistGradientBoostingRegressor(**HGB_REG)),
            "greybox_hgb (deployed)": tmax_model(),
        },
        "delta_t_max": {
            "linear_regression": LinearRegression(),
            "random_forest": RandomForestRegressor(n_estimators=100, min_samples_leaf=3,
                                                   n_jobs=1, random_state=SEED),
            "hgb_monotonic (deployed)": dt_model(),
        },
    }
    for target, models in cands.items():
        res[target] = {}
        for name, m in models.items():
            t0 = time.perf_counter()
            m.fit(xtr, tr[target])
            fit_s = time.perf_counter() - t0
            one = xte.iloc[[0]]
            t0 = time.perf_counter()
            for _ in range(100):
                m.predict(one)
            lat_ms = (time.perf_counter() - t0) / 100 * 1000
            p_te, p_ood = m.predict(xte), m.predict(xood)
            preds[(target, name)] = (p_te, p_ood)
            res[target][name] = {
                "test": reg_metrics(te[target], p_te),
                "ood": reg_metrics(ood[target], p_ood),
                "fit_seconds": round(fit_s, 2),
                "single_row_predict_ms": round(lat_ms, 3),
                "model_size_kb": 0.0 if isinstance(m, _Prior) else _size_kb(getattr(m, "m", m)),
            }

    # ---- 90 % split-conformal interval for the deployed T_max model
    fit_part, cal = train_test_split(tr, test_size=0.2, random_state=SEED)
    m_c = tmax_model().fit(featurise(fit_part), fit_part["t_max"])
    q = conformal_q(np.abs(cal["t_max"].to_numpy() - m_c.predict(featurise(cal))))
    cover = (np.abs(te["t_max"].to_numpy() - m_c.predict(xte)) <= q).mean()
    cover_ood = (np.abs(ood["t_max"].to_numpy() - m_c.predict(xood)) <= q).mean()
    res["t_max_interval"] = {"method": "split conformal, |residual| quantile",
                             "nominal": 0.90, "half_width_c": round(q, 3),
                             "empirical_coverage_test": round(float(cover), 4),
                             "empirical_coverage_ood": round(float(cover_ood), 4)}

    # ---- risk-band agreement (normal <40, warning 40-45, critical >45)
    best_te, best_ood = preds[("t_max", "greybox_hgb (deployed)")]
    band = lambda v: np.digitize(v, [40.0, 45.0])  # noqa: E731
    res["risk_band_accuracy"] = {
        "test": round(float((band(te["t_max"].to_numpy()) == band(best_te)).mean()), 4),
        "ood": round(float((band(ood["t_max"].to_numpy()) == band(best_ood)).mean()), 4)}

    # ---- hotspot: majority-class rule vs HGB classifier
    ytr = tr["hotspot_cell"].map(CELLS.index).values
    yte = te["hotspot_cell"].map(CELLS.index).values
    majority = int(np.bincount(ytr).argmax())
    clf = HistGradientBoostingClassifier(**HGB_CLF).fit(xtr, ytr)
    full = np.zeros((len(xte), 10))
    full[:, clf.classes_] = clf.predict_proba(xte)
    res["hotspot_cell"] = {
        "majority_rule_baseline": {"rule": f"always predict {CELLS[majority]}",
                                   "top1_accuracy": round(float((yte == majority).mean()), 4)},
        "hist_gradient_boosting": {
            "top1_accuracy": round(float(accuracy_score(yte, full.argmax(1))), 4),
            "top3_accuracy": round(float(top_k_accuracy_score(yte, full, k=3, labels=list(range(10)))), 4)},
        "label_distribution": tr["hotspot_cell"].value_counts(normalize=True).round(3).to_dict(),
    }
    _figures(te, ood, best_te, best_ood, res, fig_dir)
    return res


def _figures(te, ood, p_te, p_ood, res, fig_dir: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    C1, C2, INK, INK2, GRID = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e4e3df"
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": INK2, "axes.labelcolor": INK2, "xtick.color": INK2,
                         "ytick.color": INK2, "grid.color": GRID, "axes.axisbelow": True})
    fig_dir.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(5.4, 4.6), dpi=160)
    ax.grid(True)
    ax.scatter(te["t_max"], p_te, s=5, alpha=0.35, color=C1, label=f"test, ambient ≤ 42 °C (n={len(te)})", linewidths=0)
    ax.scatter(ood["t_max"], p_ood, s=7, alpha=0.6, color=C2, label=f"unseen hot weather, > 42 °C (n={len(ood)})", linewidths=0)
    lo = min(te["t_max"].min(), p_te.min()) - 1
    hi = max(ood["t_max"].max(), p_ood.max()) + 1
    ax.plot([lo, hi], [lo, hi], color=INK2, lw=1, ls="--", label="perfect prediction")
    for v in (40, 45):
        ax.axvline(v, color=INK2, lw=0.6, ls=":")
        ax.axhline(v, color=INK2, lw=0.6, ls=":")
    ax.set_xlabel("Simulated peak cell temperature (°C)")
    ax.set_ylabel("Predicted peak cell temperature (°C)")
    ax.set_title("Grey-box model: predicted vs simulated T_max", fontsize=9.5, color=INK, loc="left")
    ax.legend(fontsize=7.5, loc="upper left", frameon=False)
    fig.tight_layout()
    fig.savefig(fig_dir / "tmax_pred_vs_actual.png")
    plt.close(fig)

    names = list(res["t_max"])
    short = {"physics_closed_form_baseline": "Physics\nbaseline", "linear_regression": "Linear\nregression",
             "random_forest": "Random\nforest", "hgb_pure_ml": "HGB\n(pure ML)", "greybox_hgb (deployed)": "Grey-box\nHGB"}
    test = [res["t_max"][n]["test"]["mae"] for n in names]
    oodv = [res["t_max"][n]["ood"]["mae"] for n in names]
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(6.4, 3.3), dpi=160)
    ax.grid(True, axis="y")
    b1 = ax.bar(x - 0.19, test, 0.36, color=C1, label="test (ambient ≤ 42 °C)", edgecolor="white", linewidth=1.5)
    b2 = ax.bar(x + 0.19, oodv, 0.36, color=C2, label="unseen hot weather (> 42 °C)", edgecolor="white", linewidth=1.5)
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{b.get_height():.2f}",
                    ha="center", va="bottom", fontsize=7.5, color=INK)
    ax.set_xticks(x, [short[n] for n in names])
    ax.set_ylabel("MAE of T_max (°C)")
    ax.set_title("Model comparison: mean absolute error of peak-temperature prediction",
                 fontsize=9.5, color=INK, loc="left")
    ax.legend(fontsize=7.5, frameon=False)
    fig.tight_layout()
    fig.savefig(fig_dir / "tmax_model_comparison.png")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data/btms_synthetic.csv")
    ap.add_argument("--model-out", default="models/btms_model.joblib")
    ap.add_argument("--metrics-out", default="models/metrics.json")
    ap.add_argument("--fig-dir", default="results/figures")
    ap.add_argument("--deploy-only", action="store_true",
                    help="skip evaluation, only fit the deployed bundle (used in the Docker build)")
    a = ap.parse_args()

    df = pd.read_csv(a.data)
    metrics = {}
    if not a.deploy_only:
        t0 = time.time()
        metrics = evaluate(df, Path(a.fig_dir))
        print(f"evaluation done in {time.time() - t0:.1f}s")

    t0 = time.time()
    x = featurise(df)
    bundle, intervals = fit_bundle(x, df)
    meta = {
        "version": MODEL_VERSION,
        "model_type": "grey-box: closed-form physics prior + HistGradientBoosting residual (T_max); "
                      "HGB (dT, hotspot)",
        "features": FEATURES,
        "cells": CELLS,
        "trained_rows": len(df),
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sklearn": sklearn.__version__,
        "python": platform.python_version(),
        "feature_ranges": {f: [round(float(x[f].min()), 3), round(float(x[f].max()), 3)] for f in FEATURES},
        "intervals": {k: round(v, 4) for k, v in intervals.items()},
        "monotonic_constraints": {"t_max_residual": MONO_TMAX, "delta_t_max": MONO_DT},
    }
    Path(a.model_out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"meta": meta, "models": bundle}, a.model_out, compress=3)
    print(f"saved deployed bundle -> {a.model_out} ({Path(a.model_out).stat().st_size / 1024:.0f} KB) "
          f"in {time.time() - t0:.1f}s")
    if metrics:
        metrics["deployed_bundle"] = meta
        Path(a.metrics_out).write_text(json.dumps(metrics, indent=2))
        print(json.dumps({k: v for k, v in metrics.items() if k != "deployed_bundle"}, indent=2))


if __name__ == "__main__":
    main()

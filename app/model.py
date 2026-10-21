"""Inference, rule-based safety layer and cooling-setpoint optimiser."""
from __future__ import annotations

import os
import threading
import time

import joblib
import numpy as np
import pandas as pd

from app import physics
from app.train import featurise

# Safety limits from the use-case document: recommended operating range 15-45 C,
# target cell-to-cell uniformity dT < 5 C. 40 C is used as the default control target
# so the 90 % upper bound keeps a margin below the 45 C limit.
CRITICAL_T = 45.0
WARNING_T = 40.0
MAX_DELTA_T = 5.0
CHILLER_COP = 3.5           # assumed coefficient of performance of the chiller loop

RE_GRID = np.arange(400, 701, 50, dtype=float)
PHI_GRID = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
INLET_MIN = 18.0


class ModelUnavailable(RuntimeError):
    pass


class ThermalModel:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.bundle = None
        self.load_error = None
        self.loaded_at = None
        self.load()

    # ------------------------------------------------------------ loading
    def load(self):
        with self._lock:
            try:
                self.bundle = joblib.load(self.path)
                self.load_error = None
                self.loaded_at = time.time()
            except Exception as exc:          # missing / corrupt file -> degraded mode
                self.bundle = None
                self.load_error = f"{type(exc).__name__}: {exc}"

    @property
    def ready(self) -> bool:
        return self.bundle is not None

    @property
    def meta(self) -> dict:
        if not self.ready:
            raise ModelUnavailable(self.load_error or "model not loaded")
        return self.bundle["meta"]

    def _m(self, name):
        if not self.ready:
            raise ModelUnavailable(self.load_error or "model not loaded")
        return self.bundle["models"][name]

    # ------------------------------------------------------------ helpers
    def _frame(self, records: list[dict]) -> pd.DataFrame:
        return self._frame_df(pd.DataFrame(records))

    @staticmethod
    def risk(t_hi: float, dt_hi: float, start_temp: float) -> dict:
        reasons = []
        level = "normal"
        if t_hi > CRITICAL_T or start_temp > CRITICAL_T:
            level = "critical"
            reasons.append(f"peak temperature upper bound {t_hi:.1f} °C exceeds {CRITICAL_T} °C safety limit")
        elif t_hi > WARNING_T:
            level = "warning"
            reasons.append(f"peak temperature upper bound {t_hi:.1f} °C above {WARNING_T} °C control target")
        if dt_hi > MAX_DELTA_T:
            level = "critical" if level == "critical" else "warning"
            reasons.append(f"cell-to-cell spread upper bound {dt_hi:.2f} °C exceeds {MAX_DELTA_T} °C")
        if not reasons:
            reasons.append("within control target and uniformity limit")
        action = {
            "normal": "Maintain current cooling settings.",
            "warning": "Increase cooling: call /api/optimize for a setpoint recommendation.",
            "critical": "Derate charge/discharge current and apply maximum cooling; escalate to engineer.",
        }[level]
        return {"level": level, "reasons": reasons, "recommended_action": action}

    # ------------------------------------------------------------ prediction
    def predict(self, records: list[dict]) -> list[dict]:
        x = self._frame(records)
        q_t = self.meta["intervals"]["t_max_q90"]
        q_d = self.meta["intervals"]["delta_t_q90"]
        t = self._m("t_max").predict(x)
        d = np.clip(self._m("delta_t").predict(x), 0, None)
        clf = self._m("hotspot")
        proba = np.zeros((len(x), 10))
        proba[:, clf.classes_] = clf.predict_proba(x)
        hyd = physics.hydraulics(x["reynolds"].values, x["phi_pct"].values, x["inlet_temp"].values)
        cells = self.meta["cells"]
        out = []
        for i, rec in enumerate(records):
            top3 = np.argsort(proba[i])[::-1][:3]
            t_hi, d_hi = t[i] + q_t, d[i] + q_d
            out.append({
                "t_max_c": round(float(t[i]), 2),
                "t_max_interval_90": [round(float(t[i] - q_t), 2), round(float(t_hi), 2)],
                "delta_t_c": round(float(d[i]), 2),
                "delta_t_interval_90": [round(float(max(d[i] - q_d, 0)), 2), round(float(d_hi), 2)],
                "hotspot": {
                    "cell": cells[top3[0]],
                    "probability": round(float(proba[i, top3[0]]), 3),
                    "top3": [{"cell": cells[j], "probability": round(float(proba[i, j]), 3)} for j in top3],
                },
                "hydraulics": {
                    "flow_l_min": round(float(hyd["flow_l_min"][i]), 3),
                    "pressure_drop_pa": round(float(hyd["pressure_drop_pa"][i]), 1),
                    "pumping_power_w": round(float(hyd["pumping_power_w"][i]), 5),
                },
                "risk": self.risk(float(t_hi), float(d_hi), rec["start_temp"]),
            })
        return out

    # ------------------------------------------------------------ optimiser
    def optimize(self, rec: dict, target_t_max: float = WARNING_T,
                 max_delta_t: float = MAX_DELTA_T, optimise_phi: bool = True) -> dict:
        phis = PHI_GRID if optimise_phi else np.array([rec["phi_pct"]])
        inlets = np.arange(INLET_MIN, rec["inlet_temp"] + 1e-9, 1.0)
        if inlets[-1] != rec["inlet_temp"]:
            inlets = np.append(inlets, rec["inlet_temp"])
        g_re, g_phi, g_in = np.meshgrid(RE_GRID, phis, inlets, indexing="ij")
        n = g_re.size
        cand = pd.DataFrame({k: np.repeat(v, n) for k, v in rec.items() if k in self.meta["features"] or k == "mode"})
        cand["mode"] = rec["mode"]
        cand["reynolds"], cand["phi_pct"], cand["inlet_temp"] = g_re.ravel(), g_phi.ravel(), g_in.ravel()
        x = self._frame_df(cand)

        q_t = self.meta["intervals"]["t_max_q90"]
        q_d = self.meta["intervals"]["delta_t_q90"]
        shape = g_re.shape
        t_hi = self.monotone_in_re((self._m("t_max").predict(x) + q_t).reshape(shape)).ravel()
        d_hi = self.monotone_in_re((np.clip(self._m("delta_t").predict(x), 0, None) + q_d).reshape(shape)).ravel()
        hyd = physics.hydraulics(cand["reynolds"].values, cand["phi_pct"].values, cand["inlet_temp"].values)
        chill_w = hyd["mcp_w_k"] * (rec["inlet_temp"] - cand["inlet_temp"].values) / CHILLER_COP
        total_w = hyd["pumping_power_w"] + chill_w
        feasible = (t_hi <= target_t_max) & (d_hi <= max_delta_t)

        def describe(i):
            return {
                "reynolds": float(cand["reynolds"].iat[i]),
                "phi_pct": float(cand["phi_pct"].iat[i]),
                "inlet_temp_c": float(cand["inlet_temp"].iat[i]),
                "flow_l_min": round(float(hyd["flow_l_min"][i]), 3),
                "predicted_t_max_upper_c": round(float(t_hi[i]), 2),
                "predicted_delta_t_upper_c": round(float(d_hi[i]), 2),
                "pumping_power_w": round(float(hyd["pumping_power_w"][i]), 5),
                "chiller_power_w": round(float(chill_w[i]), 3),
                "total_cooling_power_w": round(float(total_w[i]), 3),
            }

        current = self.predict([rec])[0]
        # "conventional" baseline: always run maximum flow, coldest coolant
        max_idx = int(np.argmin(np.abs(cand["reynolds"].values - 700) + np.abs(cand["inlet_temp"].values - INLET_MIN)
                                + np.abs(cand["phi_pct"].values - phis.max())))
        result = {
            "candidates_evaluated": int(n),
            "constraints": {"t_max_upper_le_c": target_t_max, "delta_t_upper_le_c": max_delta_t},
            "current_setting": {"reynolds": rec["reynolds"], "phi_pct": rec["phi_pct"],
                                "inlet_temp_c": rec["inlet_temp"],
                                "flow_l_min": current["hydraulics"]["flow_l_min"],
                                "total_cooling_power_w": round(current["hydraulics"]["pumping_power_w"], 3),
                                "predicted_t_max_upper_c": current["t_max_interval_90"][1],
                                "risk": current["risk"]["level"]},
            "max_cooling_baseline": describe(max_idx),
            "human_review_required": True,
            "note": "Nanofluid concentration (phi) is a design-time choice; Reynolds number and "
                    "inlet setpoint are run-time actions. Recommendations are advisory and must be "
                    "approved by a thermal engineer.",
        }
        if feasible.any():
            idx = np.where(feasible)[0]
            # min total power, then lower Reynolds (less pressure drop), then lower phi
            best = idx[np.lexsort((cand["phi_pct"].values[idx], cand["reynolds"].values[idx], total_w[idx]))[0]]
            result["status"] = "feasible"
            result["recommendation"] = describe(best)
            base_w = total_w[max_idx]
            result["energy_saving_vs_max_cooling_w"] = round(float(base_w - total_w[best]), 3)
            result["energy_saving_vs_max_cooling_pct"] = round(float((base_w - total_w[best]) / base_w * 100), 1) if base_w > 0 else 0.0
            result["action"] = (f"Set coolant Re≈{int(cand['reynolds'].iat[best])} "
                                f"({hyd['flow_l_min'][best]:.2f} L/min), inlet {cand['inlet_temp'].iat[best]:.0f} °C, "
                                f"nanofluid {cand['phi_pct'].iat[best]:.1f} vol%.")
        else:
            best = int(np.argmin(t_hi))
            result["status"] = "infeasible"
            result["recommendation"] = describe(best)
            result["max_safe_c_rate"] = self._max_safe_c_rate(rec, cand.iloc[best], target_t_max, max_delta_t)
            msg = "No cooling setting meets the target at the requested load. Apply maximum cooling"
            if rec["start_temp"] >= target_t_max:
                msg = (f"Pack already starts at {rec['start_temp']:.1f} °C, at or above the "
                       f"{target_t_max:.0f} °C target. Pre-cool the pack before applying load")
                result["max_safe_c_rate"] = None
                msg += "; escalate to engineer."
            elif result["max_safe_c_rate"]:
                msg += f" and derate to {result['max_safe_c_rate']}C."
            else:
                msg += " and stop charging/discharging; escalate to engineer."
            result["action"] = msg
        return result

    @staticmethod
    def monotone_in_re(grid: np.ndarray) -> np.ndarray:
        """Conservative envelope along the Reynolds axis (axis 0, ascending Re):
        a lower flow is never rated cooler than any higher flow. Removes small
        non-physical wiggles of the learned surface before the optimiser uses it."""
        return np.flip(np.maximum.accumulate(np.flip(grid, axis=0), axis=0), axis=0)

    def _frame_df(self, df: pd.DataFrame) -> pd.DataFrame:
        # same feature pipeline as training, incl. the closed-form physics prior
        return featurise(df)[self.meta["features"]]

    def _max_safe_c_rate(self, rec, setting, target, max_dt):
        rates = np.round(np.arange(rec["c_rate"] - 0.1, 0.49, -0.1), 2)
        if len(rates) == 0:
            return None
        df = pd.DataFrame([{**rec, "reynolds": setting["reynolds"], "phi_pct": setting["phi_pct"],
                            "inlet_temp": setting["inlet_temp"], "c_rate": r} for r in rates])
        x = self._frame_df(df)
        t_hi = self._m("t_max").predict(x) + self.meta["intervals"]["t_max_q90"]
        d_hi = self._m("delta_t").predict(x) + self.meta["intervals"]["delta_t_q90"]
        ok = np.where((t_hi <= target) & (d_hi <= max_dt))[0]
        return float(rates[ok[0]]) if len(ok) else None

    # ------------------------------------------------------------ digital twin
    @staticmethod
    def simulate(rec: dict) -> dict:
        params = {k: np.array([rec[k]]) for k in rec if k != "mode"}
        params["mode"] = np.array([rec["mode"]])
        # nominal pack: no random cell-to-cell imbalance (that is what the ML model averages over)
        out = physics.simulate(params, record=True)
        cells = physics.CELL_NAMES
        final = out["final_cell_temps"][0]
        return {
            "t_max_c": round(float(out["t_max"][0]), 2),
            "delta_t_c": round(float(out["delta_t_max"][0]), 2),
            "hotspot_cell": cells[int(out["hotspot_cell"][0])],
            "final_cell_temps": {c: round(float(v), 2) for c, v in zip(cells, final)},
            "soc_end": round(float(out["soc_end"][0]), 3),
            "heat_generated_kj": round(float(out["heat_generated_kj"][0]), 2),
            "series": out["series"],
            "cell_layout": {"names": cells, "flow_position": physics.CELL_X.tolist()},
        }


def model_path() -> str:
    return os.environ.get("MODEL_PATH", os.path.join(os.path.dirname(__file__), "..", "models", "btms_model.joblib"))

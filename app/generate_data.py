"""
Generate the SYNTHETIC battery-thermal dataset used to train the prototype model.

Each row is one operating scenario of the 10-cell micro-channel pack, simulated
with the lumped physics surrogate in app/physics.py. Sensor noise and unobserved
cell-to-cell scatter are added so the ML task is not trivially invertible.

Usage:  python -m app.generate_data [--n 20000] [--seed 42] [--out data/btms_synthetic.csv]
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from app.physics import CELL_NAMES, N_CELLS, ageing_scatter, simulate

FEATURES = [
    "mode", "c_rate", "soc_start", "soh", "ambient_temp", "inlet_temp",
    "start_temp", "reynolds", "phi_pct", "duration_min",
]


def sample_scenarios(n: int, rng: np.random.Generator) -> dict:
    mode = np.where(rng.random(n) < 0.65, "discharge", "charge")
    dis = mode == "discharge"
    c_rate = np.where(dis, rng.uniform(0.5, 3.0, n), rng.uniform(0.5, 2.0, n))
    soc = np.where(dis, rng.uniform(0.40, 0.98, n), rng.uniform(0.05, 0.60, n))
    ambient = rng.uniform(15, 45, n)
    start = np.clip(ambient + rng.uniform(-3, 3, n), 15, 48)
    return {
        "mode": mode,
        "c_rate": c_rate.round(3),
        "soc_start": soc.round(3),
        "soh": rng.uniform(0.70, 1.00, n).round(3),
        "ambient_temp": ambient.round(2),
        "inlet_temp": rng.uniform(18, 35, n).round(2),
        "start_temp": start.round(2),
        "reynolds": rng.uniform(400, 700, n).round(1),
        "phi_pct": rng.uniform(0, 2.0, n).round(3),
        "duration_min": rng.uniform(5, 30, n).round(2),
    }


def build(n: int = 20000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    params = sample_scenarios(n, rng)
    variation = 1 + rng.uniform(-0.03, 0.03, (n, N_CELLS))       # contact scatter
    r_scatter = ageing_scatter(params["soh"], rng=rng)            # ageing imbalance
    out = simulate(params, variation=variation, r_scatter=r_scatter)

    df = pd.DataFrame(params)
    # temperature sensors: +/-0.25 C (1 sigma) on peak, +/-0.12 C on spread
    df["t_max"] = (out["t_max"] + rng.normal(0, 0.25, n)).round(3)
    df["delta_t_max"] = np.clip(out["delta_t_max"] + rng.normal(0, 0.12, n), 0.05, None).round(3)
    df["hotspot_cell"] = [CELL_NAMES[i] for i in out["hotspot_cell"]]
    df["pumping_power_w"] = out["pumping_power_w"].round(5)
    df["flow_l_min"] = out["flow_l_min"].round(4)
    df["pressure_drop_pa"] = out["pressure_drop_pa"].round(1)
    df["heat_generated_kj"] = out["heat_generated_kj"].round(3)
    df["soc_end"] = out["soc_end"].round(3)
    df.insert(0, "scenario_id", np.arange(1, n + 1))
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/btms_synthetic.csv")
    a = ap.parse_args()
    t0 = time.time()
    df = build(a.n, a.seed)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(a.out, index=False)
    print(f"wrote {len(df)} synthetic scenarios -> {a.out} in {time.time() - t0:.1f}s")
    print(df[["t_max", "delta_t_max", "pumping_power_w"]].describe().round(3).to_string())


if __name__ == "__main__":
    main()

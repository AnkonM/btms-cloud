"""
Physics-informed surrogate of the KJS-CES-02 micro-channel battery pack.

This is NOT a CFD model. It is a lumped-capacitance thermal network that
reproduces the main trends described in the use-case document so that we
can generate a clearly-labelled SYNTHETIC dataset and run a digital-twin
style simulation inside the cloud prototype:

  * 10 cylindrical 21700 Li-ion cells (C1..C10) in an aluminium casing
  * 40 circular micro-channels, hydraulic diameter Dh = 1 mm, length 137.4 mm
  * Al2O3 / water nanofluid coolant, 0-2 vol%
  * Laminar flow, Reynolds number 400-700

Heat generation : Joule (I^2 R) + reversible (entropic) heat
Cell resistance : Arrhenius in temperature, rises at low/high SOC and with ageing
Convection      : Hausen correlation for developing laminar flow in a tube
Nanofluid       : Maxwell (conductivity), Batchelor (viscosity), mixture rules
Hydraulics      : Hagen-Poiseuille (f = 64/Re) pressure drop -> pumping power
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------- geometry
N_CELLS = 10
CELL_NAMES = [f"C{i}" for i in range(1, N_CELLS + 1)]
DH = 1.0e-3                      # micro-channel hydraulic diameter [m]
CH_LEN = 0.1374                  # channel length = pack length [m]
N_CHANNELS = 40                  # 2 columns x 20 channels
A_CH = np.pi / 4 * DH ** 2       # cross-section of one channel [m2]
A_WET = np.pi * DH * CH_LEN * N_CHANNELS   # total wetted area [m2]

# Normalised position of each cell along the coolant flow path (0 = inlet).
# Coolant enters next to C5/C6 and leaves next to C1/C10 (Fig. 1 of the use case).
CELL_X = np.array([0.9, 0.7, 0.5, 0.3, 0.1, 0.1, 0.3, 0.5, 0.7, 0.9])
FLOW_GROUPS = [(4, 5), (3, 6), (2, 7), (1, 8), (0, 9)]   # upstream -> downstream

# Fixed cell-to-casing conduction resistance [K/W]. End cells (C1, C5, C6, C10)
# conduct slightly better through the end walls. A fixed +/-5 % pattern
# represents micro-channel manufacturing tolerance (use-case challenge c).
_rng_fixed = np.random.default_rng(7)
R_COND = 1.30 * np.where(np.isin(np.arange(N_CELLS), [0, 4, 5, 9]), 0.94, 1.0) \
    * (1 + _rng_fixed.uniform(-0.05, 0.05, N_CELLS))

# ---------------------------------------------------------------- cell data
CAP_AH = 5.0                     # 21700 nominal capacity [Ah]
HEAT_CAP = 0.068 * 1000 + 0.06 * 900   # cell + share of Al casing [J/K]
R0_REF = 0.022                   # effective DC resistance at 25 C incl. tabs, fresh cell [ohm]
EA_OVER_R = 2700.0               # Arrhenius constant [K]
DUDT = 1.2e-4                    # entropic coefficient magnitude [V/K]
UA_AMB = 0.04                    # cell -> ambient through outer skin [W/K]

# ---------------------------------------------------------------- fluids
RHO_W, CP_W, K_W = 997.0, 4180.0, 0.607
RHO_P, CP_P, K_P = 3970.0, 765.0, 40.0          # Al2O3


def water_viscosity(t_c):
    """Dynamic viscosity of water [Pa s] (Vogel equation)."""
    t_k = np.asarray(t_c) + 273.15
    return 2.414e-5 * 10 ** (247.8 / (t_k - 140.0))


def nanofluid_props(phi_pct, t_c):
    """Return rho, cp, k, mu of Al2O3/water nanofluid at volume fraction phi_pct (%)."""
    phi = np.asarray(phi_pct, dtype=float) / 100.0
    rho = (1 - phi) * RHO_W + phi * RHO_P
    cp = ((1 - phi) * RHO_W * CP_W + phi * RHO_P * CP_P) / rho
    k = K_W * (K_P + 2 * K_W + 2 * phi * (K_P - K_W)) / (K_P + 2 * K_W - phi * (K_P - K_W))
    mu = water_viscosity(t_c) * (1 + 2.5 * phi + 6.2 * phi ** 2)
    return rho, cp, k, mu


def hydraulics(reynolds, phi_pct, inlet_temp):
    """Flow, pressure drop, pumping power and heat-transfer coefficient."""
    rho, cp, k, mu = nanofluid_props(phi_pct, inlet_temp)
    re = np.asarray(reynolds, dtype=float)
    u = re * mu / (rho * DH)                       # mean velocity [m/s]
    q_vol = u * A_CH * N_CHANNELS                  # volumetric flow [m3/s]
    m_dot = rho * q_vol                            # mass flow [kg/s]
    f = 64.0 / re                                  # laminar friction factor
    dp = f * (CH_LEN / DH) * rho * u ** 2 / 2      # pressure drop [Pa]
    pump_w = dp * q_vol                            # ideal pumping power [W]
    pr = mu * cp / k
    gz = re * pr * DH / CH_LEN
    nu = 3.66 + 0.0668 * gz / (1 + 0.04 * gz ** (2 / 3))   # Hausen
    h = nu * k / DH
    return {
        "velocity_m_s": u,
        "flow_l_min": q_vol * 6.0e4,
        "m_dot_kg_s": m_dot,
        "mcp_w_k": m_dot * cp,
        "pressure_drop_pa": dp,
        "pumping_power_w": pump_w,
        "h_w_m2k": h,
    }


def _resistance(t_c, soc, soh, charging):
    t_k = t_c + 273.15
    r = R0_REF * np.exp(EA_OVER_R * (1 / t_k - 1 / 298.15))
    r *= 1 + 1.2 * (1 - soh)
    r *= 1 + 0.6 * np.clip(0.25 - soc, 0, None) / 0.25 + 0.15 * np.clip(soc - 0.85, 0, None) / 0.15
    r *= np.where(charging, 1.10, 1.0)
    return r


def simulate(params: dict, dt: float = 2.0, record: bool = False, variation=None, r_scatter=None):
    """
    Vectorised transient simulation of N operating scenarios.

    params: dict of equal-length arrays with keys
        mode ('charge'/'discharge'), c_rate, soc_start, soh, ambient_temp,
        inlet_temp, start_temp, reynolds, phi_pct, duration_min
    variation: optional (N, 10) multiplicative factor on conduction resistance
        (unobserved cell-to-cell contact scatter); defaults to 1.
    r_scatter: optional (N, 10) multiplicative factor on cell internal resistance
        (unobserved cell-to-cell ageing imbalance); defaults to 1.
    Returns dict of arrays (N,) and, if record, time series for scenario 0.
    """
    mode = np.asarray(params["mode"])
    charging = mode == "charge"
    n = len(mode)
    c_rate = np.asarray(params["c_rate"], float)
    soc = np.asarray(params["soc_start"], float).copy()
    soh = np.asarray(params["soh"], float)
    t_amb = np.asarray(params["ambient_temp"], float)
    t_in = np.asarray(params["inlet_temp"], float)
    dur_s = np.asarray(params["duration_min"], float) * 60.0
    hyd = hydraulics(params["reynolds"], params["phi_pct"], t_in)

    var = np.ones((n, N_CELLS)) if variation is None else variation
    rsc = np.ones((n, N_CELLS)) if r_scatter is None else r_scatter
    hA_cell = hyd["h_w_m2k"] * A_WET / N_CELLS                     # (n,)
    r_tot = R_COND[None, :] * var + 1.0 / hA_cell[:, None]         # (n,10)
    mcp = hyd["mcp_w_k"]                                           # (n,)

    temp = np.repeat(np.asarray(params["start_temp"], float)[:, None], N_CELLS, axis=1)
    current = c_rate * CAP_AH                                      # (n,)
    sign = np.where(charging, -1.0, 1.0)

    t_peak = temp.max(axis=1)
    temp_sum = np.zeros_like(temp)
    dt_peak = np.zeros(n)
    q_removed = np.zeros(n)
    heat_gen = np.zeros(n)
    steps = int(np.ceil(dur_s.max() / dt))
    series = {"t_s": [], "cells": [], "coolant_out": []} if record else None

    for k in range(steps):
        active = (k * dt) < dur_s                                  # scenario still running
        # current stops once the SOC window is exhausted
        soc_ok = np.where(charging, soc < 0.98, soc > 0.02)
        i_now = np.where(active & soc_ok, current, 0.0)
        r_int = _resistance(temp, soc[:, None], soh[:, None], charging[:, None]) * rsc
        q_gen = i_now[:, None] ** 2 * r_int + i_now[:, None] * (temp + 273.15) * DUDT * sign[:, None]

        # coolant marches downstream, picking up heat group by group
        t_cool = np.empty_like(temp)
        t_c = t_in.copy()
        q_cool = np.empty_like(temp)
        for g in FLOW_GROUPS:
            idx = list(g)
            t_cool[:, idx] = t_c[:, None]
            q = (temp[:, idx] - t_c[:, None]) / r_tot[:, idx]
            q_cool[:, idx] = q
            t_c = t_c + q.sum(axis=1) / mcp
        q_amb = UA_AMB * (temp - t_amb[:, None])
        d_temp = (q_gen - q_cool - q_amb) / HEAT_CAP * dt
        temp = np.where(active[:, None], temp + d_temp, temp)
        soc = np.where(active, soc - sign * i_now * dt / (CAP_AH * 3600.0), soc)

        tmax = temp.max(axis=1)
        temp_sum += np.where(active[:, None], temp, 0.0)
        t_peak = np.where(active, np.maximum(t_peak, tmax), t_peak)
        spread = temp.max(axis=1) - temp.min(axis=1)
        dt_peak = np.where(active, np.maximum(dt_peak, spread), dt_peak)
        q_removed += np.where(active, q_cool.sum(axis=1) * dt, 0)
        heat_gen += np.where(active, q_gen.sum(axis=1) * dt, 0)
        if record and active[0] and (k % 5 == 0):
            series["t_s"].append(round(k * dt, 1))
            series["cells"].append(np.round(temp[0], 2).tolist())
            series["coolant_out"].append(round(float(t_c[0]), 2))

    # hotspot = cell that runs hottest on average over the operating window
    hot_idx = temp_sum.argmax(axis=1)
    out = {
        "t_max": t_peak,
        "delta_t_max": dt_peak,
        "hotspot_cell": hot_idx,
        "final_cell_temps": temp,
        "soc_end": soc,
        "heat_generated_kj": heat_gen / 1000,
        "heat_to_coolant_kj": q_removed / 1000,
        "pumping_power_w": hyd["pumping_power_w"],
        "flow_l_min": hyd["flow_l_min"],
        "pressure_drop_pa": hyd["pressure_drop_pa"],
        "h_w_m2k": hyd["h_w_m2k"],
    }
    if record:
        out["series"] = series
    return out


def ageing_scatter(soh, n_cells: int = N_CELLS, rng=None):
    """Cell-to-cell internal-resistance imbalance that grows as the pack ages.
    sigma = 2 % for a fresh pack, about 8 % at SOH = 0.70."""
    soh = np.atleast_1d(np.asarray(soh, float))
    rng = np.random.default_rng(11) if rng is None else rng
    sigma = 0.02 + 0.20 * (1 - soh)
    return 1 + rng.standard_normal((len(soh), n_cells)) * sigma[:, None]


def quick_estimate(params: dict) -> np.ndarray:
    """
    Closed-form (non-iterative) estimate of the peak cell temperature, used as the
    physics prior of the grey-box ML model. Lumped first-order response of a mean
    cell to its Joule + entropic heat, cooled by coolant at the outlet-side temperature.
    Vectorised; ~1000x cheaper than simulate().
    """
    charging = np.asarray(params["mode"]) == "charge"
    c = np.asarray(params["c_rate"], float)
    soc0 = np.asarray(params["soc_start"], float)
    soh = np.asarray(params["soh"], float)
    t_amb = np.asarray(params["ambient_temp"], float)
    t_in = np.asarray(params["inlet_temp"], float)
    t0 = np.asarray(params["start_temp"], float)
    dur = np.asarray(params["duration_min"], float) * 60
    hyd = hydraulics(params["reynolds"], params["phi_pct"], t_in)

    i = c * CAP_AH
    # time until the SOC window is exhausted, then current stops
    t_avail = np.where(charging, (0.98 - soc0), (soc0 - 0.02)) / np.maximum(c, 1e-6) * 3600
    t_on = np.minimum(dur, np.clip(t_avail, 0, None))
    soc_end = np.where(charging, soc0 + c * t_on / 3600, soc0 - c * t_on / 3600)
    r_tot = R_COND.mean() + 1.0 / (hyd["h_w_m2k"] * A_WET / N_CELLS)
    t_guess = t_in + 6.0
    r_int = _resistance(t_guess, soc_end, soh, charging)
    q = i ** 2 * r_int + i * (t_guess + 273.15) * DUDT * np.where(charging, -1.0, 1.0)
    t_cool = t_in + 0.9 * N_CELLS * q / hyd["mcp_w_k"]          # near-outlet coolant
    g = 1 / r_tot + UA_AMB
    t_ss = (q + t_cool / r_tot + UA_AMB * t_amb) / g
    tau = HEAT_CAP / g
    t_end = t0 + (t_ss - t0) * (1 - np.exp(-t_on / tau))
    return np.maximum(t0, t_end)

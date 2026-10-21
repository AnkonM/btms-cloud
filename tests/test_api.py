"""Functional, invalid-input and failure-scenario tests for the BTMS service.
Run:  pytest -q
"""
import json
from pathlib import Path

import numpy as np
import pytest

from app.api import SAMPLE_INPUT, create_app
from app.model import ThermalModel, model_path
from app.physics import hydraulics

ROOT = Path(__file__).resolve().parents[1]
SCEN = json.loads((ROOT / "data" / "sample_inputs.json").read_text())


@pytest.fixture(scope="module")
def client():
    return create_app().test_client()


# ---------------------------------------------------------------- happy path
def test_health_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json["model_loaded"] is True


def test_dashboard_renders(client):
    r = client.get("/")
    assert r.status_code == 200 and b"Educational prototype" in r.data


@pytest.mark.parametrize("name", list(SCEN["valid"]))
def test_predict_representative_inputs(client, name):
    r = client.post("/api/predict", json=SCEN["valid"][name])
    assert r.status_code == 200
    p = r.json["prediction"]
    lo, hi = p["t_max_interval_90"]
    assert lo < p["t_max_c"] < hi
    assert 15 <= p["t_max_c"] <= 55
    assert p["risk"]["level"] in ("normal", "warning", "critical")
    assert p["hotspot"]["cell"] in [f"C{i}" for i in range(1, 11)]


def test_optimize_feasible_meets_constraint(client):
    r = client.post("/api/optimize", json=SAMPLE_INPUT)
    o = r.json["optimisation"]
    assert r.status_code == 200 and o["status"] == "feasible"
    assert o["recommendation"]["predicted_t_max_upper_c"] <= 40.0
    assert o["human_review_required"] is True


def test_optimize_precool_case_is_flagged(client):
    o = client.post("/api/optimize", json=SCEN["valid"]["T5_hot_soaked_pack_precool"]).json["optimisation"]
    assert o["status"] == "infeasible" and "Pre-cool" in o["action"]


def test_batch_predict(client):
    recs = list(SCEN["valid"].values())
    r = client.post("/api/predict/batch", json={"records": recs})
    assert r.status_code == 200 and r.json["count"] == len(recs)


def test_digital_twin(client):
    r = client.post("/api/simulate", json=SAMPLE_INPUT)
    d = r.json["digital_twin"]
    assert r.status_code == 200 and len(d["final_cell_temps"]) == 10 and len(d["series"]["t_s"]) > 10


# ---------------------------------------------------------------- invalid input
@pytest.mark.parametrize("name", list(SCEN["invalid"]))
def test_invalid_inputs_rejected(client, name):
    r = client.post("/api/predict", json=SCEN["invalid"][name])
    assert r.status_code == 422 and r.json["error"] == "validation failed"


def test_malformed_json(client):
    r = client.post("/api/predict", data="{not json", content_type="application/json")
    assert r.status_code == 400


def test_batch_too_large(client):
    r = client.post("/api/predict/batch", json={"records": [SAMPLE_INPUT] * 501})
    assert r.status_code == 413


def test_bad_optimiser_option(client):
    r = client.post("/api/optimize", json=dict(SAMPLE_INPUT, target_t_max=60))
    assert r.status_code == 422


def test_unknown_route_and_method(client):
    assert client.get("/api/nope").status_code == 404
    assert client.get("/api/predict").status_code == 405


# ---------------------------------------------------------------- failure scenario
def test_missing_model_file_degrades_gracefully(tmp_path):
    broken = create_app(ThermalModel(str(tmp_path / "missing.joblib"))).test_client()
    h = broken.get("/api/health")
    assert h.status_code == 503 and h.json["status"] == "degraded"
    p = broken.post("/api/predict", json=SAMPLE_INPUT)
    assert p.status_code == 503      # readiness probe fails -> Kubernetes stops routing traffic


# ---------------------------------------------------------------- physics / model sanity
def test_physics_trends():
    lo, hi = hydraulics(400, 0, 25), hydraulics(700, 0, 25)
    assert hi["pumping_power_w"] > lo["pumping_power_w"]        # more flow costs more pumping
    assert hi["h_w_m2k"] > lo["h_w_m2k"]                        # ...and transfers more heat
    nf = hydraulics(500, 2.0, 25)
    assert nf["h_w_m2k"] > hydraulics(500, 0, 25)["h_w_m2k"]    # nanofluid raises h


def test_optimiser_envelope_monotonic_in_reynolds():
    m = ThermalModel(model_path())
    recs = [dict(SAMPLE_INPUT, reynolds=float(re)) for re in np.arange(400, 701, 25)]
    raw = np.array([p["t_max_c"] for p in m.predict(recs)])
    env = ThermalModel.monotone_in_re(raw.reshape(-1, 1, 1)).ravel()
    assert np.all(np.diff(env) <= 1e-9)           # higher flow is never rated hotter
    assert np.all(env >= raw - 1e-9)              # envelope is conservative (never cooler)
    assert raw[0] > raw[-1]                        # overall trend: more flow -> cooler

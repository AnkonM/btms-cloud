"""
Run the representative, invalid-input and failure scenarios through the API and
save the real responses to results/test_scenarios.json (used by the report).

Usage:  python tests/run_scenarios.py
"""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api import SAMPLE_INPUT, create_app  # noqa: E402
from app.model import ThermalModel  # noqa: E402


def timed(client, path, body, n=50):
    client.post(path, json=body)                 # warm-up
    t = []
    for _ in range(n):
        t0 = time.perf_counter()
        client.post(path, json=body)
        t.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(t), 2)


def main():
    scen = json.loads((ROOT / "data" / "sample_inputs.json").read_text())
    c = create_app().test_client()
    out = {"valid": [], "invalid": [], "failure": [], "latency_ms_in_process": {}}

    for name, rec in scen["valid"].items():
        p = c.post("/api/predict", json=rec).json["prediction"]
        o = c.post("/api/optimize", json=rec).json["optimisation"]
        tw = c.post("/api/simulate", json=rec).json["digital_twin"]
        out["valid"].append({
            "name": name, "input": rec,
            "t_max_c": p["t_max_c"], "t_max_interval_90": p["t_max_interval_90"],
            "delta_t_c": p["delta_t_c"], "hotspot": p["hotspot"]["cell"],
            "hotspot_top3": [h["cell"] for h in p["hotspot"]["top3"]],
            "risk": p["risk"]["level"], "risk_action": p["risk"]["recommended_action"],
            "opt_status": o["status"], "opt_action": o["action"],
            "opt_recommendation": o["recommendation"],
            "twin_t_max_c": tw["t_max_c"], "twin_delta_t_c": tw["delta_t_c"], "twin_hotspot": tw["hotspot_cell"],
        })

    for name, rec in scen["invalid"].items():
        r = c.post("/api/predict", json=rec)
        out["invalid"].append({"name": name, "status": r.status_code,
                               "errors": r.json.get("details")})
    r = c.post("/api/predict", data="{not json", content_type="application/json")
    out["invalid"].append({"name": "I6_malformed_json", "status": r.status_code, "errors": r.json.get("error")})
    r = c.post("/api/predict/batch", json={"records": [SAMPLE_INPUT] * 501})
    out["invalid"].append({"name": "I7_batch_over_500", "status": r.status_code, "errors": r.json.get("error")})

    with tempfile.TemporaryDirectory() as d:
        broken = create_app(ThermalModel(str(Path(d) / "missing.joblib"))).test_client()
        h = broken.get("/api/health")
        p = broken.post("/api/predict", json=SAMPLE_INPUT)
        out["failure"].append({"name": "F1_model_file_missing",
                               "health_status": h.status_code, "health_body": h.json,
                               "predict_status": p.status_code, "predict_body": p.json})

    out["latency_ms_in_process"] = {
        "/api/predict": timed(c, "/api/predict", SAMPLE_INPUT),
        "/api/optimize": timed(c, "/api/optimize", SAMPLE_INPUT, 30),
        "/api/simulate": timed(c, "/api/simulate", SAMPLE_INPUT, 10),
        "note": "median wall time, Flask test client, single thread, OMP_NUM_THREADS=1",
    }
    dest = ROOT / "results" / "test_scenarios.json"
    dest.write_text(json.dumps(out, indent=2))
    for v in out["valid"]:
        print(f"{v['name']:32s} T_max {v['t_max_c']:5.1f} {v['t_max_interval_90']}  dT {v['delta_t_c']:.2f}  "
              f"{v['risk']:8s} hot {v['hotspot']:3s} | twin {v['twin_t_max_c']:5.1f} {v['twin_hotspot']:3s} | {v['opt_status']}: {v['opt_action']}")
    for v in out["invalid"]:
        print(v["name"], v["status"])
    print(out["failure"][0]["health_status"], out["failure"][0]["predict_status"], out["latency_ms_in_process"])
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()

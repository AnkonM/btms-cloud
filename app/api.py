"""Flask REST API + dashboard for the AI-enabled micro-channel BTMS prototype."""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from collections import defaultdict

from flask import Flask, g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from app.model import ModelUnavailable, ThermalModel, model_path
from app.validation import MAX_BATCH, ValidationError, validate_record

SAMPLE_INPUT = {
    "mode": "discharge", "c_rate": 2.8, "soc_start": 0.95, "soh": 0.75,
    "ambient_temp": 40.0, "inlet_temp": 32.0, "start_temp": 37.0,
    "reynolds": 450.0, "phi_pct": 0.5, "duration_min": 20.0,
}


class Metrics:
    """Tiny in-process request metrics, exposed in Prometheus text format."""

    def __init__(self):
        self.lock = threading.Lock()
        self.count = defaultdict(int)
        self.lat_sum = defaultdict(float)
        self.started = time.time()

    def observe(self, endpoint, status, seconds):
        with self.lock:
            self.count[(endpoint, status)] += 1
            self.lat_sum[endpoint] += seconds

    def render(self) -> str:
        lines = ["# TYPE btms_requests_total counter"]
        with self.lock:
            for (ep, st), c in sorted(self.count.items()):
                lines.append(f'btms_requests_total{{endpoint="{ep}",status="{st}"}} {c}')
            lines.append("# TYPE btms_request_seconds_sum counter")
            for ep, s in sorted(self.lat_sum.items()):
                lines.append(f'btms_request_seconds_sum{{endpoint="{ep}"}} {s:.6f}')
        lines.append(f"btms_uptime_seconds {time.time() - self.started:.1f}")
        return "\n".join(lines) + "\n"


def create_app(model: ThermalModel | None = None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024          # 256 KB request cap
    app.config["JSON_SORT_KEYS"] = False
    app.json.sort_keys = False
    model = model or ThermalModel(model_path())
    metrics = Metrics()
    host = os.environ.get("HOSTNAME", socket.gethostname())
    log = logging.getLogger("btms")

    # ---------------------------------------------------------- middleware
    @app.before_request
    def _start():
        g.t0 = time.perf_counter()

    @app.after_request
    def _finish(resp):
        dt = time.perf_counter() - g.get("t0", time.perf_counter())
        ep = request.url_rule.rule if request.url_rule else "unmatched"
        metrics.observe(ep, resp.status_code, dt)
        resp.headers["X-Served-By"] = host
        resp.headers["X-Response-Time-ms"] = f"{dt * 1000:.2f}"
        return resp

    def body():
        data = request.get_json(silent=True)
        if data is None:
            return None, (jsonify(error="request body must be valid JSON "
                                        "with Content-Type: application/json"), 400)
        return data, None

    # ---------------------------------------------------------- pages
    scen_path = os.path.join(os.path.dirname(__file__), "..", "data", "sample_inputs.json")
    try:
        with open(scen_path) as fh:
            scenarios = json.load(fh)
    except OSError:
        scenarios = {"valid": {}, "invalid": {}}

    @app.get("/")
    def dashboard():
        return render_template("index.html", sample=SAMPLE_INPUT, scenarios=scenarios)

    # ---------------------------------------------------------- ops endpoints
    @app.get("/api/health")
    def health():
        ok = model.ready
        payload = {"status": "ok" if ok else "degraded", "host": host,
                   "model_loaded": ok, "uptime_s": round(time.time() - metrics.started, 1)}
        if ok:
            payload["model_version"] = model.meta["version"]
        else:
            payload["error"] = model.load_error
        return jsonify(payload), (200 if ok else 503)

    @app.get("/metrics")
    def prom():
        return metrics.render(), 200, {"Content-Type": "text/plain; version=0.0.4"}

    @app.get("/api/model-info")
    def model_info():
        m = model.meta
        return jsonify(version=m["version"], features=m["features"], trained_rows=m["trained_rows"],
                       trained_at=m["trained_at"], sklearn=m["sklearn"], intervals=m["intervals"],
                       data="synthetic (physics-informed surrogate, see README)")

    @app.get("/api/sample")
    def sample():
        return jsonify(SAMPLE_INPUT)

    # ---------------------------------------------------------- ML endpoints
    @app.post("/api/predict")
    def predict():
        data, err = body()
        if err:
            return err
        rec = validate_record(data)
        return jsonify(input=rec, prediction=model.predict([rec])[0],
                       label="Educational prototype trained on synthetic data")

    @app.post("/api/predict/batch")
    def predict_batch():
        data, err = body()
        if err:
            return err
        recs = data.get("records") if isinstance(data, dict) else None
        if not isinstance(recs, list) or not recs:
            return jsonify(error="body must be {\"records\": [ ... ]} with at least one record"), 400
        if len(recs) > MAX_BATCH:
            return jsonify(error=f"batch too large: {len(recs)} > {MAX_BATCH}"), 413
        clean, errors = [], []
        for i, r in enumerate(recs):
            try:
                clean.append(validate_record(r))
            except ValidationError as e:
                errors.append({"index": i, "errors": e.errors})
        if errors:
            return jsonify(error="validation failed", details=errors), 422
        return jsonify(count=len(clean), predictions=model.predict(clean))

    @app.post("/api/optimize")
    def optimize():
        data, err = body()
        if err:
            return err
        rec = validate_record(data)
        opts = {}
        extra = []
        if "target_t_max" in data:
            v = data["target_t_max"]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not (30 <= v <= 45):
                extra.append({"field": "target_t_max", "error": "must be a number in [30, 45] °C"})
            else:
                opts["target_t_max"] = float(v)
        if "max_delta_t" in data:
            v = data["max_delta_t"]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not (1 <= v <= 10):
                extra.append({"field": "max_delta_t", "error": "must be a number in [1, 10] °C"})
            else:
                opts["max_delta_t"] = float(v)
        if "optimise_phi" in data:
            if not isinstance(data["optimise_phi"], bool):
                extra.append({"field": "optimise_phi", "error": "must be true or false"})
            else:
                opts["optimise_phi"] = data["optimise_phi"]
        if extra:
            raise ValidationError(extra)
        return jsonify(input=rec, optimisation=model.optimize(rec, **opts))

    @app.post("/api/simulate")
    def simulate():
        data, err = body()
        if err:
            return err
        rec = validate_record(data)
        return jsonify(input=rec, digital_twin=model.simulate(rec),
                       prediction=model.predict([rec])[0] if model.ready else None)

    # ---------------------------------------------------------- errors
    @app.errorhandler(ValidationError)
    def _val(e):
        return jsonify(error="validation failed", details=e.errors), 422

    @app.errorhandler(ModelUnavailable)
    def _unavail(e):
        return jsonify(error="model unavailable, service degraded", detail=str(e)), 503

    @app.errorhandler(HTTPException)
    def _http(e):
        return jsonify(error=e.name, detail=e.description), e.code

    @app.errorhandler(Exception)
    def _boom(e):
        log.exception("unhandled error")
        return jsonify(error="internal server error"), 500

    return app

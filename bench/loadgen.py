"""
Deterministic HTTP load generator for Experiments 7 and 8.

Workload model (same for every configuration)
  A depot of 100 EV packs. Each pack's gateway asks for a thermal prediction every
  5 s (20 req/s) and for a cooling-setpoint optimisation every ~30 s (3.3 req/s).
  -> design load 25 req/s, request mix 85 % /api/predict, 15 % /api/optimize.
  Payloads are drawn from a fixed-seed generator, so every run sends identical requests.

Two phases are supported:
  fixed_rate   open-loop: requests are scheduled at a constant rate; latency is measured
               from the scheduled send time (avoids coordinated omission).
  closed_loop  N concurrent virtual users send back-to-back -> maximum sustainable throughput.

Usage:  python bench/loadgen.py --url http://localhost:8000 --rps 25 --seconds 60
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

RANGES = {
    "c_rate": (0.5, 3.0), "soc_start": (0.4, 0.98), "soh": (0.70, 1.0),
    "ambient_temp": (20, 45), "inlet_temp": (18, 35), "reynolds": (400, 700),
    "phi_pct": (0, 2.0), "duration_min": (5, 30),
}
MIX_OPTIMIZE = 0.15
TIMEOUT_S = 5.0


def make_requests(n: int, seed: int = 2026) -> list[tuple[str, dict]]:
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        rec = {k: round(rnd.uniform(*v), 3) for k, v in RANGES.items()}
        rec["mode"] = "discharge" if rnd.random() < 0.7 else "charge"
        if rec["mode"] == "charge":
            rec["c_rate"] = round(min(rec["c_rate"], 2.0), 3)
            rec["soc_start"] = round(rnd.uniform(0.05, 0.6), 3)
        rec["start_temp"] = round(min(48, max(15, rec["ambient_temp"] + rnd.uniform(-3, 3))), 2)
        path = "/api/optimize" if rnd.random() < MIX_OPTIMIZE else "/api/predict"
        out.append((path, rec))
    return out


_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def _send(base: str, path: str, body: dict) -> tuple[bool, str]:
    try:
        r = _session().post(base + path, json=body, timeout=TIMEOUT_S)
        return r.status_code == 200, str(r.status_code)
    except requests.RequestException as e:
        return False, type(e).__name__


def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    k = min(len(sorted_vals) - 1, max(0, int(round(q / 100 * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def summarise(records: list[dict], wall_s: float, offered_rps: float | None) -> dict:
    lat = sorted(r["ms"] for r in records if r["ok"])
    ok = sum(r["ok"] for r in records)
    errs = {}
    for r in records:
        if not r["ok"]:
            errs[r["code"]] = errs.get(r["code"], 0) + 1
    by_ep = {}
    for ep in ("/api/predict", "/api/optimize"):
        l = sorted(r["ms"] for r in records if r["ok"] and r["path"] == ep)
        by_ep[ep] = {"n": len(l), "p50_ms": _r(_pct(l, 50)), "p95_ms": _r(_pct(l, 95))}
    return {
        "requests": len(records), "ok": ok, "errors": len(records) - ok,
        "error_rate": round((len(records) - ok) / max(1, len(records)), 4), "error_codes": errs,
        "offered_rps": offered_rps, "achieved_rps": round(ok / wall_s, 2), "wall_s": round(wall_s, 2),
        "mean_ms": _r(sum(lat) / len(lat)) if lat else None,
        "p50_ms": _r(_pct(lat, 50)), "p95_ms": _r(_pct(lat, 95)), "p99_ms": _r(_pct(lat, 99)),
        "max_ms": _r(lat[-1]) if lat else None, "by_endpoint": by_ep,
    }


def _r(v):
    return None if v is None else round(v, 2)


def fixed_rate(base: str, rps: float, seconds: float, seed: int = 2026, pool: int = 64) -> dict:
    reqs = make_requests(int(rps * seconds), seed)
    interval = 1.0 / rps
    records, lock = [], threading.Lock()

    def job(i, sched, path, body):
        ok, code = _send(base, path, body)
        ms = (time.perf_counter() - sched) * 1000      # includes any client-side queueing
        with lock:
            records.append({"i": i, "ok": ok, "code": code, "ms": ms, "path": path})

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=pool) as ex:
        for i, (path, body) in enumerate(reqs):
            sched = t0 + i * interval
            delay = sched - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            ex.submit(job, i, sched, path, body)
    wall = time.perf_counter() - t0
    return summarise(records, wall, rps)


def closed_loop(base: str, concurrency: int, seconds: float, seed: int = 7) -> dict:
    reqs = make_requests(20000, seed)
    records, lock = [], threading.Lock()
    counter = iter(range(len(reqs)))
    stop = time.perf_counter() + seconds

    def user():
        while time.perf_counter() < stop:
            with lock:
                i = next(counter)
            path, body = reqs[i % len(reqs)]
            t = time.perf_counter()
            ok, code = _send(base, path, body)
            with lock:
                records.append({"i": i, "ok": ok, "code": code, "ms": (time.perf_counter() - t) * 1000, "path": path})

    t0 = time.perf_counter()
    threads = [threading.Thread(target=user) for _ in range(concurrency)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return summarise(records, time.perf_counter() - t0, None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--rps", type=float, default=25)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--concurrency", type=int, default=0, help="if >0 run closed-loop instead")
    a = ap.parse_args()
    res = closed_loop(a.url, a.concurrency, a.seconds) if a.concurrency else fixed_rate(a.url, a.rps, a.seconds)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()

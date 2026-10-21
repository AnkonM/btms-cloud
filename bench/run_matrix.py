"""
Run the SAME workload against the BTMS container under several CPU/memory limits
(Experiment 7 right-sizing and Experiment 8 FinOps instance-type comparison).

For each configuration it:
  1. starts the container with `docker run --cpus C --memory M -e WORKERS -e THREADS`
  2. waits for /api/health, warms up for a few seconds
  3. phase A - fixed-rate load at the design rate (default 25 req/s) for N seconds
  4. waits until the server has drained any backlog, then
     phase B - closed-loop saturation with K concurrent users (max throughput)
  5. samples `docker stats` every ~1 s during both phases (CPU cores, memory MiB)
  6. stops the container and writes <out>/<name>.json plus <out>/summary.csv

Usage (Windows PowerShell / Linux, Docker Desktop running):
  python bench/run_matrix.py --image <dockerhub-user>/btms-api:1.0 \
         --configs bench/configs_exp8.json --out results/exp8
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import shlex
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loadgen  # noqa: E402

NAME = "btms-bench"


def sh(cmd: list[str], check=True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{r.stderr}")
    return r.stdout.strip()


def parse_mem_mib(s: str) -> float:
    m = re.match(r"([\d.]+)\s*([KMG]i?B|B)", s.strip())
    if not m:
        return float("nan")
    v, u = float(m.group(1)), m.group(2)
    return v * {"B": 1 / 2**20, "KiB": 1 / 1024, "KB": 1 / 1024, "MiB": 1, "MB": 1,
                "GiB": 1024, "GB": 1024}[u]


class StatsSampler(threading.Thread):
    def __init__(self, docker: list[str]):
        super().__init__(daemon=True)
        self.docker, self.samples, self._stop = docker, [], threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                line = sh(self.docker + ["stats", "--no-stream", "--format", "{{json .}}", NAME])
                d = json.loads(line.splitlines()[-1])
                self.samples.append({"t": time.time(),
                                     "cpu_cores": float(d["CPUPerc"].rstrip("%")) / 100,
                                     "mem_mib": parse_mem_mib(d["MemUsage"].split("/")[0])})
            except Exception:
                pass
            self._stop.wait(0.5)

    def stop(self):
        self._stop.set()
        self.join(timeout=10)

    def window(self, t0, t1):
        return [s for s in self.samples if t0 <= s["t"] <= t1]


def res_summary(samples, cfg) -> dict:
    if not samples:
        return {}
    cpu = sorted(s["cpu_cores"] for s in samples)
    mem = sorted(s["mem_mib"] for s in samples)
    p95 = cpu[min(len(cpu) - 1, int(round(0.95 * (len(cpu) - 1))))]
    return {
        "samples": len(samples),
        "cpu_cores_mean": round(statistics.mean(cpu), 3), "cpu_cores_p95": round(p95, 3),
        "cpu_cores_max": round(cpu[-1], 3),
        "cpu_util_of_limit_mean_pct": round(statistics.mean(cpu) / cfg["cpus"] * 100, 1),
        "mem_mib_mean": round(statistics.mean(mem), 1), "mem_mib_max": round(mem[-1], 1),
        "mem_util_of_limit_max_pct": round(mem[-1] / cfg["memory_mb"] * 100, 1),
    }


def wait_healthy(url, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(url + "/api/health", timeout=2).status_code == 200:
                return round(time.time() - t0, 1)
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError("service did not become healthy")


def wait_drained(url, timeout=180) -> float:
    """After an overload phase the server may still be working through abandoned requests.
    Wait until /api/health answers quickly twice in a row so phase B starts from idle."""
    t0, quick = time.time(), 0
    while time.time() - t0 < timeout and quick < 2:
        t = time.time()
        try:
            requests.get(url + "/api/health", timeout=10)
            quick = quick + 1 if time.time() - t < 0.1 else 0
        except requests.RequestException:
            quick = 0
        time.sleep(0.5)
    return round(time.time() - t0, 1)


def run_one(cfg, a, docker) -> dict:
    url = f"http://localhost:{a.port}"
    sh(docker + ["rm", "-f", NAME], check=False)
    cmd = docker + ["run", "-d", "--name", NAME, "--cpus", str(cfg["cpus"]),
                    "--memory", f"{cfg['memory_mb']}m", "--memory-swap", f"{cfg['memory_mb']}m",
                    "-p", f"{a.port}:8000", "-e", f"WORKERS={cfg['workers']}",
                    "-e", f"THREADS={cfg['threads']}", "-e", "OMP_NUM_THREADS=1"]
    cmd += shlex.split(a.extra_run_args) + [a.image] + shlex.split(a.cmd)
    print(f"\n=== {cfg['name']}: {cfg['cpus']} vCPU, {cfg['memory_mb']} MiB, "
          f"{cfg['workers']} workers x {cfg['threads']} threads", flush=True)
    sh(cmd)
    startup_s = wait_healthy(url)
    sampler = StatsSampler(docker)
    sampler.start()
    loadgen.closed_loop(url, 2, a.warmup_seconds)             # warm-up, not recorded

    t0 = time.time()
    fixed = loadgen.fixed_rate(url, a.rps, a.fixed_seconds)
    t1 = time.time()
    print(f"  fixed {a.rps} rps: achieved {fixed['achieved_rps']}, p95 {fixed['p95_ms']} ms, "
          f"errors {fixed['errors']}", flush=True)
    sat, drain_s = None, 0.0
    if a.sat_seconds > 0:
        drain_s = wait_drained(url)
        t2 = time.time()
        sat = loadgen.closed_loop(url, a.sat_concurrency, a.sat_seconds)
        t3 = time.time()
        print(f"  saturation c={a.sat_concurrency}: {sat['achieved_rps']} rps, p95 {sat['p95_ms']} ms", flush=True)
    sampler.stop()
    insp = json.loads(sh(docker + ["inspect", NAME]))[0]["State"]
    sh(docker + ["rm", "-f", NAME], check=False)
    return {
        "config": cfg, "startup_to_healthy_s": startup_s,
        "fixed_rate": {**fixed, "resources": res_summary(sampler.window(t0, t1), cfg)},
        "saturation": None if sat is None else {**sat, "concurrency": a.sat_concurrency,
                                                "resources": res_summary(sampler.window(t2, t3), cfg)},
        "drain_after_fixed_phase_s": drain_s,
        "oom_killed": insp.get("OOMKilled"), "container_exit_code": insp.get("ExitCode"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True)
    ap.add_argument("--configs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="", help="comma-separated config names to run")
    ap.add_argument("--docker", default="docker", help='docker command, e.g. "sudo docker"')
    ap.add_argument("--extra-run-args", default="")
    ap.add_argument("--cmd", default="", help="override container command (normally empty)")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--rps", type=float, default=25)
    ap.add_argument("--fixed-seconds", type=float, default=60)
    ap.add_argument("--sat-concurrency", type=int, default=12)
    ap.add_argument("--sat-seconds", type=float, default=30)
    ap.add_argument("--warmup-seconds", type=float, default=8)
    a = ap.parse_args()

    docker = shlex.split(a.docker)
    cfgs = json.loads(Path(a.configs).read_text())["configs"]
    if a.only:
        keep = set(a.only.split(","))
        cfgs = [c for c in cfgs if c["name"] in keep]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    env = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host_os": f"{platform.system()} {platform.release()}", "host_logical_cpus": os.cpu_count(),
        "docker_server": sh(docker + ["version", "--format", "{{.Server.Version}}"], check=False),
        "image": a.image, "design_rps": a.rps, "fixed_seconds": a.fixed_seconds,
        "sat_concurrency": a.sat_concurrency, "sat_seconds": a.sat_seconds,
        "workload": "85% /api/predict, 15% /api/optimize, fixed-seed payloads (bench/loadgen.py)",
    }
    rows = []
    for cfg in cfgs:
        res = run_one(cfg, a, docker)
        res["environment"] = env
        (out / f"{cfg['name']}.json").write_text(json.dumps(res, indent=2))
        f, s = res["fixed_rate"], res["saturation"] or {}
        rows.append({
            "name": cfg["name"], "cpus": cfg["cpus"], "memory_mb": cfg["memory_mb"],
            "workers": cfg["workers"], "threads": cfg["threads"],
            "fixed_achieved_rps": f["achieved_rps"], "fixed_p50_ms": f["p50_ms"], "fixed_p95_ms": f["p95_ms"],
            "fixed_p99_ms": f["p99_ms"], "fixed_error_rate": f["error_rate"],
            "fixed_cpu_cores_mean": f["resources"].get("cpu_cores_mean"),
            "fixed_cpu_cores_p95": f["resources"].get("cpu_cores_p95"),
            "fixed_cpu_util_pct": f["resources"].get("cpu_util_of_limit_mean_pct"),
            "fixed_mem_mib_max": f["resources"].get("mem_mib_max"),
            "fixed_mem_util_pct": f["resources"].get("mem_util_of_limit_max_pct"),
            "sat_rps": s.get("achieved_rps"), "sat_p95_ms": s.get("p95_ms"),
            "sat_cpu_cores_mean": (s.get("resources") or {}).get("cpu_cores_mean"),
            "sat_mem_mib_max": (s.get("resources") or {}).get("mem_mib_max"),
            "oom_killed": res["oom_killed"],
        })
    with open(out / "summary.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "environment.json").write_text(json.dumps(env, indent=2))
    print(f"\nwrote {out / 'summary.csv'}")


if __name__ == "__main__":
    main()

"""
Turn the benchmark JSON files into the Experiment 7 and 8 tables and figures.

  python bench/analyze.py exp7 results/exp7_before results/exp7_after
  python bench/analyze.py exp8 results/exp8

Service-level objective (SLO) at the 25 req/s design load:
  p95 latency <= 250 ms, error rate <= 1 %, achieved >= 99 % of offered rate.
Costs use bench/pricing.json and are ESTIMATES (see the "basis" field there).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "results" / "figures"
PRICING = json.loads((ROOT / "bench" / "pricing.json").read_text())
SLO_P95_MS, SLO_ERR, SLO_ACHIEVED = 250.0, 0.01, 0.99
FLEET_PEAK_RPS = 100.0          # 400-pack fleet, used for scale-out costing
CAPACITY_FRACTION = 0.70        # plan each task at 70 % of its measured saturation throughput

# reference categorical palette (dataviz skill), text stays in neutral ink
C1, C2, C3, C4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({
    "font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK2, "xtick.color": INK2,
    "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "figure.dpi": 160, "savefig.bbox": "tight",
})


def load_dir(d: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json")) if p.name != "environment.json"]


def hourly_cost(cfg) -> float:
    return cfg["cpus"] * PRICING["vcpu_hour_usd"] + cfg["memory_mb"] / 1024 * PRICING["gb_hour_usd"]


def meets_slo(f) -> bool:
    return (f["p95_ms"] is not None and f["p95_ms"] <= SLO_P95_MS and f["error_rate"] <= SLO_ERR
            and f["achieved_rps"] >= SLO_ACHIEVED * f["offered_rps"])


def row(r) -> dict:
    cfg, f, s = r["config"], r["fixed_rate"], r["saturation"] or {}
    fr, sr = f.get("resources", {}), s.get("resources", {}) or {}
    h = hourly_cost(cfg)
    sat = s.get("achieved_rps") or 0.0
    plan_rps = sat * CAPACITY_FRACTION
    tasks = math.ceil(FLEET_PEAK_RPS / plan_rps) if plan_rps > 0 else None
    return {
        "name": cfg["name"], "vcpu": cfg["cpus"], "memory_gib": cfg["memory_mb"] / 1024,
        "workers": cfg["workers"], "threads": cfg["threads"],
        "achieved_rps": f["achieved_rps"], "p50_ms": f["p50_ms"], "p95_ms": f["p95_ms"], "p99_ms": f["p99_ms"],
        "error_rate_pct": round(f["error_rate"] * 100, 2),
        "cpu_cores_mean": fr.get("cpu_cores_mean"), "cpu_cores_p95": fr.get("cpu_cores_p95"),
        "cpu_util_pct": fr.get("cpu_util_of_limit_mean_pct"),
        "mem_mib_max": fr.get("mem_mib_max"), "mem_util_pct": fr.get("mem_util_of_limit_max_pct"),
        "sat_rps": sat, "sat_p95_ms": s.get("p95_ms"), "sat_cpu_cores": sr.get("cpu_cores_mean"),
        "meets_slo": meets_slo(f),
        "usd_per_hour": round(h, 5), "usd_per_month": round(h * PRICING["hours_per_month"], 2),
        "usd_per_million_req_at_70pct": round(h / (plan_rps * 3600) * 1e6, 4) if plan_rps else None,
        "tasks_for_100rps": tasks,
        "fleet_usd_per_month": round(tasks * h * PRICING["hours_per_month"], 2) if tasks else None,
        "startup_s": r["startup_to_healthy_s"], "oom_killed": r["oom_killed"],
    }


def label(r):
    return f"{r['vcpu']:g} vCPU\n{r['memory_gib']:g} GiB"


def exp7(before_dir: Path, after_dir: Path):
    b, a = row(load_dir(before_dir)[0]), row(load_dir(after_dir)[0])
    rec = json.loads((ROOT / "bench" / "configs_exp7_after.json").read_text())["recommendation"]
    out = {"before": b, "after": a, "recommendation": rec,
           "change": {
               "vcpu_reduction_pct": round((1 - a["vcpu"] / b["vcpu"]) * 100, 1),
               "memory_reduction_pct": round((1 - a["memory_gib"] / b["memory_gib"]) * 100, 1),
               "cost_reduction_pct": round((1 - a["usd_per_hour"] / b["usd_per_hour"]) * 100, 1),
               "monthly_saving_usd": round(b["usd_per_month"] - a["usd_per_month"], 2),
               "p95_delta_ms": round(a["p95_ms"] - b["p95_ms"], 2),
               "cpu_util_before_pct": b["cpu_util_pct"], "cpu_util_after_pct": a["cpu_util_pct"],
               "mem_util_before_pct": b["mem_util_pct"], "mem_util_after_pct": a["mem_util_pct"]},
           "slo": {"p95_ms": SLO_P95_MS, "error_rate": SLO_ERR}, "pricing_basis": PRICING["basis"]}
    (ROOT / "results" / "exp7_comparison.json").write_text(json.dumps(out, indent=2))

    FIG.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(9.2, 2.9))
    names = ["Before\n2 vCPU / 4 GiB", f"After\n{a['vcpu']:g} vCPU / {a['memory_gib']:g} GiB"]
    panels = [("CPU utilisation of limit (%)", [b["cpu_util_pct"], a["cpu_util_pct"]], "{:.0f}%"),
              ("Peak memory utilisation of limit (%)", [b["mem_util_pct"], a["mem_util_pct"]], "{:.1f}%"),
              ("p95 latency at 25 req/s (ms)", [b["p95_ms"], a["p95_ms"]], "{:.0f} ms")]
    for ax, (title, vals, fmt) in zip(axes, panels):
        bars = ax.bar(names, vals, color=[C2, C1], width=0.55, edgecolor="white", linewidth=2)
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, v, fmt.format(v), ha="center", va="bottom", color=INK, fontsize=9)
        ax.set_title(title, fontsize=9, color=INK, loc="left")
        ax.set_ylim(0, max(vals) * 1.25 if "latency" not in title else max(max(vals) * 1.25, SLO_P95_MS * 1.1))
        if "latency" in title:
            ax.axhline(SLO_P95_MS, color=INK2, lw=1, ls="--")
            ax.text(-0.4, SLO_P95_MS, "SLO 250 ms", ha="left", va="bottom", fontsize=8, color=INK2)
    fig.suptitle("Experiment 7: same 25 req/s workload before and after right-sizing", x=0.01, ha="left", fontsize=10, color=INK)
    fig.tight_layout()
    fig.savefig(FIG / "exp7_before_after.png")
    plt.close(fig)
    print(json.dumps(out["change"], indent=2))


def exp8(d: Path):
    rows = sorted((row(r) for r in load_dir(d)), key=lambda r: r["vcpu"])
    passing = [r for r in rows if r["meets_slo"]]
    best = min(passing, key=lambda r: r["usd_per_hour"]) if passing else None
    best_fleet = min((r for r in rows if r["fleet_usd_per_month"]), key=lambda r: r["fleet_usd_per_month"], default=None)
    out = {"rows": rows, "selected_single_task": best["name"] if best else None,
           "cheapest_for_100rps_fleet": best_fleet["name"] if best_fleet else None,
           "slo": {"p95_ms": SLO_P95_MS, "error_rate": SLO_ERR, "achieved_fraction": SLO_ACHIEVED},
           "fleet_peak_rps": FLEET_PEAK_RPS, "capacity_fraction": CAPACITY_FRACTION,
           "pricing": PRICING}
    (ROOT / "results" / "exp8_finops.json").write_text(json.dumps(out, indent=2))
    import csv
    with open(ROOT / "results" / "exp8_finops.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    FIG.mkdir(parents=True, exist_ok=True)
    labels = [label(r) for r in rows]
    fig, axes = plt.subplots(1, 3, figsize=(10, 3.1))
    # p95 latency at design load (clip failed configs to show as off-scale)
    cap = SLO_P95_MS * 2.2
    vals = [min(r["p95_ms"] or cap, cap) for r in rows]
    cols = [C1 if r["meets_slo"] else C2 for r in rows]
    bars = axes[0].bar(labels, vals, color=cols, width=0.6, edgecolor="white", linewidth=2)
    for bar, r in zip(bars, rows):
        if r["p95_ms"] and r["p95_ms"] <= cap:
            txt = f"{r['p95_ms']:.0f} ms"
        else:
            v = r["p95_ms"]
            txt = (f"{v / 1000:.1f} s" if v else "n/a") + "\n(clipped)"
        axes[0].text(bar.get_x() + bar.get_width() / 2, bar.get_height(), txt, ha="center", va="bottom", fontsize=8, color=INK)
    axes[0].axhline(SLO_P95_MS, color=INK2, lw=1, ls="--")
    axes[0].text(len(rows) - 0.5, SLO_P95_MS, "SLO 250 ms", ha="right", va="bottom", fontsize=8, color=INK2)
    axes[0].set_ylim(0, cap * 1.2)
    axes[0].set_title("p95 latency at 25 req/s", fontsize=9, loc="left", color=INK)
    # max throughput
    sat = [r["sat_rps"] for r in rows]
    bars = axes[1].bar(labels, sat, color=cols, width=0.6, edgecolor="white", linewidth=2)
    for bar, v in zip(bars, sat):
        axes[1].text(bar.get_x() + bar.get_width() / 2, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8, color=INK)
    axes[1].axhline(25, color=INK2, lw=1, ls="--")
    axes[1].text(-0.45, 25, "design load 25", ha="left", va="bottom", fontsize=8, color=INK2)
    axes[1].set_title("Max throughput (req/s)", fontsize=9, loc="left", color=INK)
    axes[1].set_ylim(0, max(sat + [25]) * 1.25)
    # monthly cost, single always-on task
    cost = [r["usd_per_month"] for r in rows]
    bars = axes[2].bar(labels, cost, color=[C3 if best and r["name"] == best["name"] else "#b9b8b2" for r in rows],
                       width=0.6, edgecolor="white", linewidth=2)
    for bar, v in zip(bars, cost):
        axes[2].text(bar.get_x() + bar.get_width() / 2, v, f"${v:.2f}", ha="center", va="bottom", fontsize=8, color=INK)
    axes[2].set_title("Est. cost per task-month (USD)", fontsize=9, loc="left", color=INK)
    axes[2].set_ylim(0, max(cost) * 1.25)
    fig.suptitle("Experiment 8: same workload on four Fargate task sizes "
                 "(blue = meets SLO, orange = fails; green = selected)", x=0.01, ha="left", fontsize=10, color=INK)
    fig.tight_layout()
    fig.savefig(FIG / "exp8_instance_comparison.png")
    plt.close(fig)
    for r in rows:
        print({k: r[k] for k in ("name", "achieved_rps", "p95_ms", "error_rate_pct", "cpu_util_pct", "sat_rps",
                                 "meets_slo", "usd_per_month", "usd_per_million_req_at_70pct", "tasks_for_100rps",
                                 "fleet_usd_per_month")})
    print("selected:", out["selected_single_task"], "| cheapest fleet:", out["cheapest_for_100rps_fleet"])


if __name__ == "__main__":
    if sys.argv[1] == "exp7":
        exp7(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        exp8(Path(sys.argv[2]))

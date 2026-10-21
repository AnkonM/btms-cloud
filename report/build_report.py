"""
Build the consolidated report (Word .docx) from the measured result files.

  python report/build_report.py

Inputs (all produced by the project scripts):
  report/student.json                      team names, roll numbers, batch (edit this)
  models/metrics.json                      ML evaluation        (python -m app.train)
  results/test_scenarios.json              scenario test runs   (python tests/run_scenarios.py)
  results/pytest_output.txt                unit/API tests       (pytest -v)
  results/exp7_comparison.json             Exp 7                (bench/analyze.py exp7 ...)
  results/exp8_finops.json                 Exp 8                (bench/analyze.py exp8 ...)
  results/figures/*.png, docs/*.png        figures
  report/screenshots/<ID>.png              YOUR screenshots; any missing one becomes a placeholder box

Re-run it after adding screenshots or re-running the benchmarks on your own machine;
then open the .docx in Word and use File > Save As > PDF.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.section import WD_ORIENT  # noqa: F401
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "report" / "CC_UseCase_Report.docx"
SHOTS = ROOT / "report" / "screenshots"

ACCENT = RGBColor(0x0F, 0x6B, 0x5C)
MUTED = RGBColor(0x52, 0x51, 0x4E)
INK = RGBColor(0x1B, 0x1F, 0x1D)


def load(p):
    return json.loads((ROOT / p).read_text())


STUDENT = load("report/student.json")
MET = load("models/metrics.json")
SCN = load("results/test_scenarios.json")
E7 = load("results/exp7_comparison.json")
E8 = load("results/exp8_finops.json")
ENV = load("results/exp7_before/environment.json")
PRICE = load("bench/pricing.json")
DATA = pd.read_csv(ROOT / "data" / "btms_synthetic.csv")
PYTEST = (ROOT / "results" / "pytest_output.txt").read_text().strip().splitlines()
KUBECONFORM = (ROOT / "results" / "kubeconform_validation.txt").read_text().strip().splitlines()[0]
_obs = ROOT / "results" / "exp2_exp3_observed.json"
OBS = json.loads(_obs.read_text()) if _obs.exists() else {}
E2, E3 = OBS.get("exp2", {}), OBS.get("exp3", {})
_th = ROOT / "results" / "exp8_threads_check.json"
TH = json.loads(_th.read_text()) if _th.exists() else None

doc = Document()
fig_no = [0]
tab_no = [0]


# ------------------------------------------------------------------ helpers
def set_cell_bg(cell, hex_color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def para(text="", bold=False, italic=False, size=None, color=None, align=None, space_after=6, style=None):
    p = doc.add_paragraph(style=style)
    if text:
        add_runs(p, text, bold=bold, italic=italic, size=size, color=color)
    if align:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    return p


def add_runs(p, text, bold=False, italic=False, size=None, color=None):
    """Supports **bold** spans inside text."""
    parts = text.split("**")
    for i, part in enumerate(parts):
        if not part:
            continue
        r = p.add_run(part)
        r.bold = bold or (i % 2 == 1)
        r.italic = italic
        if size:
            r.font.size = Pt(size)
        if color:
            r.font.color.rgb = color
    return p


def bullet(text, level=0):
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    add_runs(p, text)
    p.paragraph_format.space_after = Pt(2)
    return p


_num = [0]


def restart():
    _num[0] = 0


def numbered(text):
    _num[0] += 1
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.75)
    p.paragraph_format.first_line_indent = Cm(-0.55)
    p.paragraph_format.space_after = Pt(2)
    add_runs(p, f"{_num[0]}.\t" + text)
    p.paragraph_format.tab_stops.add_tab_stop(Cm(0.75))
    return p


def h(text, level=1):
    hp = doc.add_heading(text, level=level)
    for r in hp.runs:
        r.font.color.rgb = ACCENT if level <= 2 else INK
    return hp


def code(text):
    for line in text.strip("\n").split("\n"):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.left_indent = Cm(0.3)
        r = p.add_run(line if line else " ")
        r.font.name = "Consolas"
        r._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
        r.font.size = Pt(8)
        pPr = p._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), "F3F4F1")
        pPr.append(shd)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def table(headers, rows, widths=None, font=8.5, caption=None):
    if caption:
        tab_no[0] += 1
        cp = para(f"Table {tab_no[0]}: {caption}", italic=True, size=9, color=MUTED, space_after=3)
        cp.paragraph_format.keep_with_next = True
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, hd in enumerate(headers):
        c = t.rows[0].cells[i]
        c.text = ""
        add_runs(c.paragraphs[0], hd, bold=True, size=font)
        set_cell_bg(c, "E3EFEC")
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            add_runs(cells[i].paragraphs[0], str(v), size=font)
    if widths:
        set_widths(t, widths)
    keep_together(t)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def set_widths(t, widths):
    t.autofit = False
    grid = t._tbl.tblGrid
    for i, gc in enumerate(grid.findall(qn("w:gridCol"))):
        gc.set(qn("w:w"), str(int(Cm(widths[i]).twips)))
    for row in t.rows:
        for i, w in enumerate(widths):
            row.cells[i].width = Cm(w)


def keep_together(t, max_rows=14):
    """Keep short tables on one page; never split a row across pages."""
    for ri, row in enumerate(t.rows):
        trPr = row._tr.get_or_add_trPr()
        cs = OxmlElement("w:cantSplit")
        trPr.append(cs)
        if len(t.rows) <= max_rows and ri < len(t.rows) - 1:
            for c in row.cells:
                for p in c.paragraphs:
                    p.paragraph_format.keep_with_next = True


def figure(path, caption, width_cm=16.0):
    path = ROOT / path
    fig_no[0] += 1
    doc.add_picture(str(path), width=Cm(width_cm))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.paragraphs[-1].paragraph_format.keep_with_next = True
    para(f"Figure {fig_no[0]}: {caption}", italic=True, size=9, color=MUTED, align=WD_ALIGN_PARAGRAPH.CENTER)


def shot(sid, what, how):
    """Insert the student's screenshot if present, else a clearly marked placeholder."""
    for ext in (".png", ".jpg", ".jpeg"):
        f = SHOTS / f"{sid}{ext}"
        if f.exists():
            fig_no[0] += 1
            doc.add_picture(str(f), width=Cm(15.5))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            para(f"Figure {fig_no[0]} ({sid}): {what}", italic=True, size=9, color=MUTED,
                 align=WD_ALIGN_PARAGRAPH.CENTER)
            return
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    c = t.rows[0].cells[0]
    set_cell_bg(c, "FFF6E5")
    c.text = ""
    add_runs(c.paragraphs[0], f"[SCREENSHOT {sid} — insert here] ", bold=True, size=9, color=RGBColor(0x8A, 0x5A, 0x00))
    add_runs(c.paragraphs[0], what, size=9)
    p2 = c.add_paragraph()
    add_runs(p2, f"How: {how}", italic=True, size=8.5, color=MUTED)
    p3 = c.add_paragraph()
    add_runs(p3, f"Or save it as report/screenshots/{sid}.png and re-run python report/build_report.py",
             size=8, color=MUTED)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def fill(label):
    return f"[{label}: fill from your run]"


def page_break():
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def add_page_number_footer(section):
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Cloud Computing mini project · page ")
    r.font.size = Pt(8)
    r.font.color.rgb = MUTED
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "1"
    run.append(t)
    fld.append(run)
    p._p.append(fld)


# ------------------------------------------------------------------ styles & page
st = doc.styles["Normal"]
st.font.name = "Calibri"
st.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
st.font.size = Pt(10.5)
for lvl, size in [(1, 15), (2, 12.5), (3, 11)]:
    hs = doc.styles[f"Heading {lvl}"]
    hs.font.name = "Calibri"
    hs.font.size = Pt(size)
    hs.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
for side in ("left_margin", "right_margin"):
    setattr(sec, side, Cm(2.0))
sec.top_margin, sec.bottom_margin = Cm(1.8), Cm(1.8)
add_page_number_footer(sec)

# ------------------------------------------------------------------ numbers used in text
tm = MET["t_max"]
gb = tm["greybox_hgb (deployed)"]
pure = tm["hgb_pure_ml"]
phys = tm["physics_closed_form_baseline"]
lin = tm["linear_regression"]
rf = tm["random_forest"]
dtm = MET["delta_t_max"]
iv = MET["t_max_interval"]
hs = MET["hotspot_cell"]
rows_ = MET["rows"]
q90 = MET["deployed_bundle"]["intervals"]["t_max_q90"]
b7, a7, c7, rec7 = E7["before"], E7["after"], E7["change"], E7["recommendation"]
r8 = E8["rows"]
sel8 = next((r for r in r8 if r["name"] == E8["selected_single_task"]), None)
fleet8 = next((r for r in r8 if r["name"] == E8["cheapest_for_100rps_fleet"]), None)
fail8 = [r for r in r8 if not r["meets_slo"]]
lat = SCN["latency_ms_in_process"]
n_pass = sum("PASSED" in l for l in PYTEST)
n_fail = sum("FAILED" in l for l in PYTEST)

# ================================================================== COVER
para("K J Somaiya School of Engineering, Somaiya Vidyavihar University", bold=True, size=12,
     align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
para("Cloud Computing · Mini Project Report", size=11, color=MUTED, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=40)
para("AI-Enabled Micro-channel Battery Thermal Management", bold=True, size=22, color=ACCENT,
     align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
para("A cloud-deployed thermal prediction and cooling-recommendation prototype", size=13,
     align=WD_ALIGN_PARAGRAPH.CENTER, space_after=30)
table(["Item", "Details"], [
    ["Team members", "\n".join(f"{m['name']} ({m['roll_no']})" for m in STUDENT["members"])],
    ["Batch", STUDENT["batch"]],
    ["Programme", STUDENT.get("programme", "")],
    ["Course", "Cloud Computing"],
    ["Selected use case", "KJS-CES-02: AI-Enabled Micro-channel Battery Thermal Management System for Electric Vehicles"],
    ["Selected experiments", "2 (Containerization, Docker and Docker Hub) · 3 (Application deployment using Kubernetes) · "
                             "7 (Cloud cost optimization through resource right-sizing) · 8 (FinOps principles and instance-type comparison)"],
    ["Submission date", STUDENT.get("date", date.today().strftime("%d %B %Y"))],
], widths=[4.2, 12.8], font=10)
para("Educational prototype. All battery data in this project is synthetic (physics-informed simulation) and clearly "
     "labelled as such. Cooling recommendations are advisory and require approval by a thermal engineer.",
     italic=True, size=9, color=MUTED, align=WD_ALIGN_PARAGRAPH.CENTER)
page_break()

# ================================================================== CONTENTS
h("Contents", 1)
for line in ["1. Selected use case and experiments", "2. Problem statement, objectives and prototype scope",
             "3. Dataset: synthetic-data generation method", "4. Architecture and tools used",
             "5. Prototype application and AI/ML component",
             "6. Experiment 2: Containerization, Docker and Docker Hub",
             "7. Experiment 3: Application deployment using Kubernetes",
             "8. Experiment 7: Cloud cost optimization through resource right-sizing",
             "9. Experiment 8: FinOps principles and instance-type comparison",
             "10. Testing and performance/cost comparison", "11. Limitations, data privacy and improvements",
             "12. References", "13. Acknowledgement of AI-assisted work",
             "Appendix A. Source code, repository layout and reproduction"]:
    para(line, space_after=2)
page_break()

# ================================================================== 1
h("1. Selected use case and experiments", 1)
para("The prototype implements a manageable software component of Somaiya Vidyavihar use case **KJS-CES-02** "
     "(faculty owners: Dr. Siddappa Bhusnoor and Dr. Manoj Pawar, Department of Mechanical Engineering, KJSSE). "
     "The research project develops a battery thermal management system (BTMS) in which ten cylindrical 21700 "
     "Li-ion cells sit in an aluminium casing with embedded 1 mm micro-channels carrying Al₂O₃/water nanofluid. "
     "This project builds the AI prediction and decision-support layer of that system (use-case workflow steps 3, 4, "
     "5, 6, 9 and 10) as a cloud service, and integrates it with four Cloud Computing experiments.")
table(["Experiment", "Role in this project"], [
    ["2. Containerization, Docker and Docker Hub", "The prediction service and dashboard are packaged as one image, run and health-checked, then pushed to Docker Hub."],
    ["3. Application deployment using Kubernetes", "The same image runs as a 2-replica Deployment behind a Service, with probes, scaling, self-healing and a CPU autoscaler."],
    ["7. Cloud cost optimization through resource right-sizing", "CPU and memory of the service are measured under the fleet workload; the over-provisioned container is right-sized and re-tested."],
    ["8. FinOps principles and instance-type comparison", "The same workload runs on four task sizes; latency, throughput and estimated cost identify the cheapest size that meets the target."],
], widths=[6, 11], caption="Selected experiments and their use-case integration")

# ================================================================== 2
h("2. Problem statement, objectives and prototype scope", 1)
h("2.1 Problem statement", 2)
para("Lithium-ion cells heat up while charging and discharging. High peak temperature and uneven temperature across "
     "cells shorten battery life, reduce efficiency and raise the risk of thermal runaway, while conventional cooling "
     "often spends more pumping or chiller energy than needed. Engineers therefore need a fast estimate of how hot a "
     "pack will get under a given load and cooling setting, early warning when limits will be crossed, and a "
     "recommendation for the least-energy coolant setting that keeps the pack inside the use case's targets "
     "(15–45 °C operating range, cell-to-cell spread ΔT < 5 °C). The service must be packaged, deployed and sized "
     "so that it scales reliably to a fleet and does not waste cloud spend.")
h("2.2 Objectives", 2)
restart()
for o in ["Generate a documented synthetic thermal dataset for the micro-channel pack that reflects the use-case geometry and operating ranges.",
          "Train and evaluate an AI model that predicts peak cell temperature, cell-to-cell spread and the likely hotspot cell, with an uncertainty interval.",
          "Recommend coolant flow (Reynolds number 400–700), inlet temperature and nanofluid concentration that meet the thermal targets at minimum cooling power, with a human-in-the-loop flag.",
          "Containerize the service, publish it to Docker Hub and deploy it on Kubernetes with scaling and recovery (Experiments 2 and 3).",
          "Measure the service's real resource use under a fleet workload, right-size it, and choose the most cost-efficient resource configuration that meets a latency target (Experiments 7 and 8)."]:
    numbered(o)
h("2.3 Prototype scope", 2)
table(["In scope (implemented)", "Out of scope (not required by the brief)"], [
    ["Physics-informed synthetic data generator for the 10-cell pack", "Real sensors, test rig, CFD (ANSYS Fluent) runs"],
    ["Grey-box ML model for T_max, ML models for ΔT and hotspot, 90 % interval", "Validation against experimental battery data"],
    ["Rule-based safety layer and cooling-setpoint optimiser", "Closed-loop control of real pumps or chillers"],
    ["Transient digital-twin simulation of a nominal pack", "Calibrated digital twin synchronised with live sensors"],
    ["REST API, web dashboard, input validation, metrics endpoint", "Authentication, TLS, multi-tenant access control"],
    ["Docker image, Docker Hub, Kubernetes Deployment/Service/HPA", "Managed cloud deployment with real billing"],
    ["Measured right-sizing and instance-size comparison with estimated cost", "Actual cloud invoices"],
], widths=[8.5, 8.5], caption="Prototype scope")

# ================================================================== 3
h("3. Dataset: synthetic-data generation method", 1)
para("No public dataset exists for this exact pack, so a synthetic dataset was generated with a lumped-parameter "
     "physics model written for this project (app/physics.py). It is **not** CFD and **not** measured data; every "
     "output of the prototype is labelled accordingly. The model follows the geometry and ranges in the use-case "
     f"document. The file data/btms_synthetic.csv holds **{len(DATA):,} scenarios** (seed 42, generated in about 15 s).")
h("3.1 Physical model", 2)
for b in ["**Geometry:** 10 × 21700 cells (C1–C10) in two staggered rows; 40 circular micro-channels, hydraulic diameter 1 mm, length 137.4 mm. "
          "Coolant enters beside C5/C6 and leaves beside C1/C10, so it warms as it passes the cells.",
          "**Heat generation per cell:** Joule heat I²R plus reversible (entropic) heat I·T·dU/dT. Resistance R follows an Arrhenius law in temperature, rises below 25 % and above 85 % state of charge, and rises as state of health (SOH) falls.",
          "**Nanofluid properties:** Maxwell model for thermal conductivity, Batchelor model for viscosity, mixture rules for density and heat capacity (Al₂O₃ volume fraction 0–2 %).",
          "**Hydraulics:** velocity from the Reynolds number, laminar friction factor f = 64/Re, pressure drop and ideal pumping power = Δp × flow.",
          "**Convection:** Hausen correlation for developing laminar flow gives the heat-transfer coefficient h.",
          "**Transient thermal network:** each cell is a thermal mass (cell + share of casing) coupled to the local coolant through a conduction resistance and 1/(hA); coolant temperature rises group by group along the flow path; a small loss to ambient. Integrated with a 2 s time step; current stops when the SOC window is exhausted.",
          "**Realism terms:** a fixed ±5 % manufacturing pattern in cell-to-casing resistance; random ±3 % contact scatter per scenario; random cell-to-cell resistance imbalance that grows as SOH falls (2 % fresh, about 8 % at SOH 0.70); Gaussian sensor noise (σ = 0.25 °C on peak temperature, 0.12 °C on spread). These unobserved effects make the ML task non-trivial."]:
    bullet(b)
h("3.2 Sampled operating conditions and labels", 2)
table(["Input feature", "Range sampled (uniform)", "Unit"], [
    ["mode", "discharge 65 %, charge 35 %", "–"], ["c_rate", "0.5–3.0 discharge, 0.5–2.0 charge", "C"],
    ["soc_start", "0.40–0.98 discharge, 0.05–0.60 charge", "fraction"], ["soh", "0.70–1.00", "fraction"],
    ["ambient_temp", "15–45", "°C"], ["inlet_temp (coolant)", "18–35", "°C"], ["start_temp (pack)", "ambient ± 3", "°C"],
    ["reynolds", "400–700 (use-case range)", "–"], ["phi_pct (Al₂O₃)", "0–2", "vol %"], ["duration_min", "5–30", "min"],
], widths=[5, 8, 4], caption="Inputs of each synthetic scenario")
d = DATA
table(["Label", "Definition", "Distribution in the dataset"], [
    ["t_max", "peak cell temperature in the window (°C)",
     f"mean {d.t_max.mean():.1f}, min {d.t_max.min():.1f}, max {d.t_max.max():.1f}; "
     f"{((d.t_max > 40) & (d.t_max <= 45)).mean() * 100:.1f} % in 40–45 °C, {(d.t_max > 45).mean() * 100:.1f} % above 45 °C"],
    ["delta_t_max", "largest cell-to-cell spread in the window (°C)",
     f"mean {d.delta_t_max.mean():.2f}, max {d.delta_t_max.max():.2f}; none above 5 °C (consistent with the use case's micro-channel result)"],
    ["hotspot_cell", "cell with the highest mean temperature",
     ", ".join(f"{k} {v * 100:.0f} %" for k, v in list(hs["label_distribution"].items())[:5]) + ", others < 5 % each"],
], widths=[3, 5.5, 8.5], caption="Labels")
para(f"**Split.** To test generalisation, all {rows_['ood_test']:,} scenarios with ambient above 42 °C were held out as an "
     f"unseen hot-weather set. The remaining rows were split 80/20 at random ({rows_['train']:,} train, {rows_['test']:,} test). "
     f"The deployed model is refit on all {rows_['total']:,} rows after evaluation.")

# ================================================================== 4
h("4. Architecture and tools used", 1)
figure("docs/architecture.png", "Cloud architecture: build and publish (Exp 2), Kubernetes runtime (Exp 3) and the "
       "benchmark harness that feeds right-sizing (Exp 7) and instance selection (Exp 8)", 16.5)
figure("docs/dataflow.png", "Application workflow: input → processing → output", 16.5)
table(["Tool", "Version", "Purpose"], [
    ["Python", "3.13", "application, data generation, training, benchmarks"],
    ["scikit-learn / NumPy / pandas", "1.9.1 / 2.5.3 / 3.0.5", "ML models, numerical simulation"],
    ["Flask + Gunicorn (gthread)", "3.1.3 + 23.0.0", "REST API, dashboard, multi-worker serving"],
    ["Docker Engine / Docker Desktop", ENV.get("docker_server", "29.x"), "build, run, resource limits (--cpus, --memory), docker stats"],
    ["Docker Hub", "–", "public image registry"],
    ["Kubernetes (minikube) + kubectl", "minikube 1.39, Kubernetes 1.37 (manifests checked against 1.31 API)", "Deployment, Service, HPA, metrics-server"],
    ["kubeconform", "0.8.0", "schema validation of the manifests"],
    ["pytest", "9.x", "automated tests"],
    ["matplotlib, Graphviz", "3.11 / 2.x", "figures and diagrams"],
    ["AWS Fargate price list", f"retrieved {PRICE['retrieved']}", "cost estimates for Exp 7 and 8 (no AWS resources created)"],
], widths=[5, 4, 8], caption="Tools and versions")

# ================================================================== 5
h("5. Prototype application and AI/ML component", 1)
h("5.1 Workflow", 2)
for b in ["**Input:** a JSON operating condition (10 fields, Table 2) sent to POST /api/predict or entered in the dashboard.",
          "**Validation:** types, ranges (the training envelope), allowed modes, charging limit 2C and unknown fields are checked; any violation returns HTTP 422 with a per-field message.",
          "**Physics prior:** a closed-form first-order estimate of the peak temperature (app/physics.py, quick_estimate) is computed in microseconds.",
          "**AI models:** a grey-box gradient-boosting model adds a learned correction to the prior to predict T_max; separate gradient-boosting models predict ΔT and the hotspot-cell probabilities. A split-conformal residual quantile gives a 90 % interval (±"
          f"{q90:.2f} °C for T_max).",
          "**Rule-based safety layer:** the upper interval bound decides the risk level: normal (< 40 °C), warning (40–45 °C) or critical (> 45 °C or pack already above 45 °C); ΔT upper bound > 5 °C also raises a warning.",
          "**Optimiser (POST /api/optimize):** evaluates up to ~500 settings (Reynolds 400–700 in steps of 50 × nanofluid 0–2 % × inlet setpoint from 18 °C up to the current inlet) in one batch and picks the lowest total cooling power (pumping + chiller at COP 3.5) whose upper-bound T_max ≤ 40 °C and ΔT ≤ 5 °C. A conservative envelope along Reynolds number guarantees that a lower flow is never rated cooler than a higher one. If nothing is feasible it advises pre-cooling or derating. Every recommendation carries human_review_required = true.",
          "**Digital twin (POST /api/simulate):** runs the transient physics model of a nominal pack and returns per-cell temperatures over time for the dashboard.",
          "**Output:** JSON and the dashboard (Figure 5) showing predictions, interval, risk, hotspot, recommended setpoint and energy saving versus always-maximum cooling."]:
    bullet(b)
h("5.2 Model selection and accuracy", 2)
para("A purely data-driven model was compared with a physics-only rule and with the hybrid. The closed-form physics "
     "estimate is the **rule-based baseline** required by the brief: it needs no training, is fully explainable and is "
     "the prior on which the AI model builds.")
mrows = []
for name, label in [("physics_closed_form_baseline", "Physics closed-form (rule-based baseline)"),
                    ("linear_regression", "Linear regression"), ("random_forest", "Random forest (100 trees)"),
                    ("hgb_pure_ml", "Gradient boosting, pure ML"), ("greybox_hgb (deployed)", "Grey-box: physics + gradient boosting (deployed)")]:
    v = tm[name]
    mrows.append([label, f"{v['test']['mae']:.3f}", f"{v['test']['rmse']:.3f}", f"{v['test']['r2']:.4f}",
                  f"{v['ood']['mae']:.3f}", f"{v['single_row_predict_ms']:.2f}",
                  "–" if not v["model_size_kb"] else f"{v['model_size_kb'] / 1024:.1f} MB" if v["model_size_kb"] > 1024 else f"{v['model_size_kb']:.0f} KB"])
table(["T_max model", "Test MAE °C", "Test RMSE °C", "Test R²", "Hot-weather MAE °C", "Predict ms (1 row)", "Size"],
      mrows, widths=[5.6, 1.8, 1.9, 1.8, 2.2, 2, 1.7], font=8,
      caption="Peak-temperature models on the random test set and on unseen hot weather (ambient > 42 °C)")
figure("results/figures/tmax_model_comparison.png", "Mean absolute error of T_max prediction by model", 14.5)
para(f"**Interpretation.** Pure gradient boosting is accurate inside the training range (MAE {pure['test']['mae']:.2f} °C) "
     f"but its error more than doubles in unseen hot weather ({pure['ood']['mae']:.2f} °C) because trees cannot extrapolate. "
     f"The physics rule extrapolates well (hot-weather MAE {phys['ood']['mae']:.2f} °C) but misses cell imbalance and "
     f"transient effects (test MAE {phys['test']['mae']:.2f} °C). The grey-box model keeps the best of both: test MAE "
     f"**{gb['test']['mae']:.2f} °C** (R² {gb['test']['r2']:.4f}) and hot-weather MAE **{gb['ood']['mae']:.2f} °C**. "
     f"This is close to the 0.25 °C sensor noise built into the labels, so further gains are limited by noise, not the model. "
     f"Random forest is less accurate, about {rf['model_size_kb'] / max(gb['model_size_kb'], 1):.0f} times larger and slower, so it was rejected for deployment.")
figure("results/figures/tmax_pred_vs_actual.png", "Grey-box predictions vs simulated peak temperature; dotted lines mark the 40 °C and 45 °C limits", 11)
table(["Quantity", "Result"], [
    ["90 % interval for T_max (split conformal)", f"half-width ±{iv['half_width_c']:.2f} °C; empirical coverage {iv['empirical_coverage_test'] * 100:.1f} % on test, {iv['empirical_coverage_ood'] * 100:.1f} % on unseen hot weather"],
    ["Risk band agreement (normal / warning / critical)", f"{MET['risk_band_accuracy']['test'] * 100:.1f} % on test, {MET['risk_band_accuracy']['ood'] * 100:.1f} % on unseen hot weather"],
    ["ΔT model (gradient boosting)", f"test MAE {dtm['hgb_monotonic (deployed)']['test']['mae']:.3f} °C, R² {dtm['hgb_monotonic (deployed)']['test']['r2']:.3f} (linear regression R² {dtm['linear_regression']['test']['r2']:.3f})"],
    ["Hotspot cell (10 classes)", f"top-1 accuracy {hs['hist_gradient_boosting']['top1_accuracy'] * 100:.1f} %, top-3 {hs['hist_gradient_boosting']['top3_accuracy'] * 100:.1f} % "
                                  f"vs {hs['majority_rule_baseline']['top1_accuracy'] * 100:.1f} % for the rule '{hs['majority_rule_baseline']['rule']}'"],
], widths=[6, 11], caption="Uncertainty, risk classification, spread and hotspot results")
para("The hotspot is only moderately predictable because it depends mostly on the random cell-to-cell imbalance, which "
     "is not among the inputs. In a real deployment per-cell temperature or resistance telemetry would be needed; the "
     "service therefore reports the top-3 candidate cells with probabilities rather than a single answer.")
h("5.3 Responsible-AI measures (from the use-case governance table)", 2)
for b in ["**Trustworthiness:** every prediction has a calibrated 90 % interval and risk decisions use its upper bound.",
          "**Explainability:** the prediction is physics prior + learned correction, so engineers can see how much the ML term adds; the optimiser returns the current, recommended and maximum-cooling settings side by side.",
          "**Human-in-the-loop:** recommendations are advisory (human_review_required = true); critical cases are escalated, never acted on automatically.",
          "**Safety envelope:** inputs outside the training range are rejected (HTTP 422) rather than extrapolated silently.",
          "**Sustainability:** the optimiser minimises pumping + chiller power instead of always running maximum cooling."]:
    bullet(b)
dash = ROOT / "results" / "screens" / "dashboard_full.png"
if dash.exists():
    figure("results/screens/dashboard_full.png", "Dashboard: prediction with interval and risk, digital-twin pack view and "
           "temperature trace, cooling recommendation and the raw API response (captured from the running service)", 16.5)

# ================================================================== 6  EXP 2
h("6. Experiment 2: Containerization, Docker and Docker Hub", 1)
h("Aim", 3)
para("To containerize the BTMS prediction service, run it as a container, verify it, and publish the image to Docker Hub.")
h("Use-case connection", 3)
para("The image is the deployable unit of the BTMS decision-support service: the same image is later pulled by "
     "Kubernetes (Experiment 3) and benchmarked under different resource limits (Experiments 7 and 8). Building the "
     "model inside the image ties the trained model to the exact pinned library versions, which is the reproducibility "
     "requirement in the use case's model-validation governance row.")
h("Procedure", 3)
restart()
for s in ["Write a Dockerfile on python:3.13-slim: install pinned requirements first (cached layer), copy the code, regenerate the synthetic data and fit the deployed model during the build, create a non-root user, expose port 8000, add a HEALTHCHECK on /api/health and start Gunicorn.",
          "Add a .dockerignore so results, reports, tests and local models are not sent to the build context.",
          "Build and tag the image as <dockerhub-user>/btms-api:1.0, list it, and run it with port 8000 published.",
          "Wait for the container to report (healthy); call /api/health, /api/predict, /api/optimize and send an invalid request; open the dashboard.",
          "Log in to Docker Hub and push the image; confirm the repository page shows the tag."]:
    numbered(s)
h("Configuration: Dockerfile", 3)
code((ROOT / "Dockerfile").read_text())
h("Commands", 3)
code("""$U = "your-dockerhub-username"
docker build -t "$U/btms-api:1.0" .
docker images "$U/btms-api"
docker run -d --name btms -p 8000:8000 "$U/btms-api:1.0"
docker ps                     # STATUS: Up ... (healthy)
Invoke-RestMethod http://localhost:8000/api/health
docker logs btms
docker login
docker push "$U/btms-api:1.0\"""")
h("Screenshots", 3)
for sid, what, how in [
        ("S2a", "docker build completing (layers, model training step, final 'naming to ...' line)", "docker build -t <user>/btms-api:1.0 ."),
        ("S2b", "docker images showing the image tag and size, and docker ps showing STATUS (healthy)", "docker images <user>/btms-api ; docker ps"),
        ("S2c", "Dashboard served by the container at http://localhost:8000 after Predict + Optimise", "browser"),
        ("S2d", "API calls: /api/health JSON and an invalid request returning 422", "Invoke-RestMethod commands in README section 2"),
        ("S2e", "docker push output and the Docker Hub repository page with tag 1.0", "docker push ; hub.docker.com/r/<user>/btms-api")]:
    shot(sid, what, how)
h("Results", 3)
table(["Check", "Result"], [
    ["Image tag", E2.get("image", "<dockerhub-user>/btms-api:1.0")],
    ["Image size (docker images)", E2.get("image_size", fill("size in MB"))],
    ["Build time", f"{E2['build_seconds']} s (docker build --no-cache)" if "build_seconds" in E2 else fill("seconds")],
    ["Container health (docker ps)", E2.get("health", fill("healthy / unhealthy"))],
    ["Docker Hub URL", E2.get("hub_url", fill("https://hub.docker.com/r/<user>/btms-api"))],
    ["Start-up to healthy, measured in the Exp 7/8 container runs", f"{b7['startup_s']:.1f} s (2 vCPU) and {a7['startup_s']:.1f} s (1 vCPU)"],
    ["Container behaviour under limits (Exp 7/8 runs)", f"served the 25 req/s workload with 0 errors at ≥ 1 vCPU; no OOM kills in any run"],
], widths=[7, 10], caption="Experiment 2 results")
h("Conclusion", 3)
para("The service, its model and its dashboard run from a single, versioned, non-root image with a built-in health "
     "check. Publishing it to Docker Hub makes the exact same artefact available to Kubernetes and to the benchmark "
     "harness, so every later experiment measures the same application.")

# ================================================================== 7  EXP 3
h("7. Experiment 3: Application deployment using Kubernetes", 1)
h("Aim", 3)
para("To deploy the containerized BTMS service on Kubernetes, expose it, and demonstrate pod status, service access, scaling and recovery.")
h("Use-case connection", 3)
para("A fleet of packs or a test lab sends telemetry continuously, so the thermal service must stay available when a "
     "pod fails and must add capacity when more packs report at once. Readiness probes use /api/health, which returns "
     "503 if the model cannot be loaded, so Kubernetes never routes battery data to a pod that cannot predict.")
h("Procedure", 3)
restart()
for s in ["Start minikube (Docker driver, 4 CPUs, 4 GB) and enable metrics-server.",
          "Apply the namespace, Deployment (2 replicas, rolling update with maxUnavailable 0, startup/readiness/liveness probes, non-root, requests 300m CPU / 256 Mi, limits 1 CPU / 1 Gi from Experiment 7), NodePort Service and HPA (2–5 replicas at 70 % CPU).",
          "Check pod, service and HPA status; open the service URL and call the API repeatedly to see responses from different pods (X-Served-By header / host field).",
          "Scale to 4 replicas; delete a pod and watch the Deployment replace it.",
          "Generate load with bench/loadgen.py and watch the HPA add replicas; stop the load and watch it scale down.",
          "Failure scenario: roll out a non-existent image tag, observe ImagePullBackOff while old pods keep serving, then kubectl rollout undo."]:
    numbered(s)
h("Configuration: k8s/deployment.yaml (excerpt), service.yaml, hpa.yaml", 3)
dep = (ROOT / "k8s" / "deployment.yaml").read_text().split("\n")
code("\n".join(l for l in dep if not l.startswith("#")))
code("\n".join(l for l in (ROOT / "k8s" / "service.yaml").read_text().split("\n") if not l.startswith("#")))
code("\n".join(l for l in (ROOT / "k8s" / "hpa.yaml").read_text().split("\n") if not l.startswith("#")))
para(f"All four manifests pass schema validation against the Kubernetes 1.31 API (kubeconform -strict): {KUBECONFORM}.")
h("Commands", 3)
code("""minikube start --driver=docker --cpus=4 --memory=4096
minikube addons enable metrics-server
kubectl apply -f k8s/namespace.yaml ; kubectl apply -f k8s/
kubectl get pods,svc,hpa -n btms -o wide
minikube service btms-api -n btms --url
kubectl scale deployment btms-api -n btms --replicas=4
kubectl delete pod <pod-name> -n btms ; kubectl get pods -n btms -w
python bench/loadgen.py --url <service-url> --concurrency 8 --seconds 180   # while: kubectl get hpa -n btms -w
kubectl set image deployment/btms-api btms-api=<user>/btms-api:does-not-exist -n btms
kubectl rollout undo deployment/btms-api -n btms""")
h("Screenshots", 3)
for sid, what, how in [
        ("S3a", "kubectl get pods,svc,hpa -n btms -o wide with 2/2 pods Running and Ready", "after kubectl apply"),
        ("S3b", "Dashboard or /api/health through the minikube service URL; two calls showing different host values", "minikube service btms-api -n btms --url"),
        ("S3c", "Scaling: kubectl scale --replicas=4 and the four Running pods", "kubectl get pods -n btms"),
        ("S3d", "Recovery: a deleted pod being replaced (Terminating + new ContainerCreating/Running)", "kubectl delete pod ... ; kubectl get pods -w"),
        ("S3e", "Autoscaling: kubectl get hpa -w showing CPU above target and REPLICAS increasing", "loadgen with --concurrency 8"),
        ("S3f", "Failure scenario: ImagePullBackOff pod next to Running pods, then rollout undo", "kubectl set image ... ; kubectl rollout undo")]:
    shot(sid, what, how)
h("Results", 3)
table(["Check", "Result"], [
    ["Cluster", E3.get("cluster", fill("minikube / Kubernetes version"))],
    ["Pods ready after apply", E3.get("pods_ready", fill("e.g. 2/2 Running"))],
    ["Service URL", E3.get("service_url", fill("http://127.0.0.1:port"))],
    ["Responses served by different pods", E3.get("load_balanced", fill("yes/no, pod names"))],
    ["Scaled to 4 replicas", E3.get("scale4", fill("time until 4/4 Ready"))],
    ["Pod deleted → replacement Ready", E3.get("pod_replace", fill("seconds"))],
    ["HPA under load", E3.get("hpa", fill("max replicas reached, CPU %"))],
    ["Bad image rollout", E3.get("bad_image", fill("ImagePullBackOff; old pods kept serving? undo successful?"))],
], widths=[7, 10], caption="Experiment 3 results")
h("Conclusion", 3)
para("Kubernetes runs the BTMS service as a self-healing, horizontally scalable deployment. Health-based probes, a "
     "zero-downtime rolling-update strategy and an autoscaler sized from the measured per-request CPU cost make the "
     "service suitable for continuous fleet telemetry; a faulty release is contained and rolled back without an outage.")

# ================================================================== 8  EXP 7
h("8. Experiment 7: Cloud cost optimization through resource right-sizing", 1)
h("Aim", 3)
para("To measure the BTMS service's actual CPU and memory use under its expected workload, reduce over-provisioned "
     "resources, and compare performance before and after right-sizing.")
h("Use-case connection and workload", 3)
para("The workload models a depot or test fleet of **100 EV packs**: each pack's gateway requests a thermal prediction "
     "every 5 s (20 req/s) and a cooling optimisation about every 30 s (3.3 req/s). The design load is therefore "
     "**25 req/s** with a mix of 85 % /api/predict and 15 % /api/optimize. Payloads are drawn from a fixed-seed generator "
     "(bench/loadgen.py), so every configuration receives identical requests. Requests are scheduled open-loop at a "
     "constant rate and latency is measured from the scheduled send time. Service-level objective (SLO): "
     "**p95 latency ≤ 250 ms, error rate ≤ 1 %, ≥ 99 % of the offered rate served.** A second, closed-loop phase with "
     "12 concurrent users measures maximum throughput.")
h("Procedure", 3)
restart()
for s in ["Start the container with the initial, guessed size: 2 vCPU, 4 GiB, 4 Gunicorn workers × 4 threads (docker run --cpus 2 --memory 4096m).",
          "Warm up, then apply 25 req/s for 60 s while sampling docker stats about once per second; then run the 30 s saturation phase.",
          "Right-sizing rule (bench/rightsize.py): required vCPU = p95 CPU at design load ÷ 0.70 (keep p95 utilisation ≤ 70 %); required memory = peak memory × (new workers ÷ old workers) × 1.5; round both up to the smallest valid AWS Fargate task size; workers = 2 × vCPU.",
          "Re-run exactly the same workload on the right-sized container and compare."]:
    numbered(s)
h("Commands", 3)
code("""python bench/run_matrix.py --image <user>/btms-api:1.0 --configs bench/configs_exp7_before.json --out results/exp7_before
python bench/rightsize.py results/exp7_before/exp7_before_2vcpu_4gb.json --out bench/configs_exp7_after.json
python bench/run_matrix.py --image <user>/btms-api:1.0 --configs bench/configs_exp7_after.json --out results/exp7_after
python bench/analyze.py exp7 results/exp7_before results/exp7_after""")
h("Measurement environment", 3)
if ENV["image"].startswith("btms-runtime-local"):
    _img_note = ("For these runs the container used the host's Python 3.13 runtime through read-only mounts instead of the "
                 "python:3.13-slim base layer (Docker Hub was not reachable from that machine); application code, package "
                 "versions, Gunicorn settings and resource limits are identical to the published image. ")
else:
    _img_note = (f"Every run used the published image {ENV['image']} from Experiment 2, unchanged. "
                 + ("The load generator ran in WSL 2 on the same machine and reached the container through Docker's "
                    "published port. "
                    if "WSL2" in ENV["host_os"] or "microsoft" in ENV["host_os"] else ""))
_wsl = "microsoft" in ENV["host_os"].lower() or "wsl" in ENV["host_os"].lower()
_host = (f"{ENV['host_logical_cpus']}-logical-CPU Windows laptop (Docker Desktop, WSL 2)" if _wsl
         else f"{ENV['host_logical_cpus']}-vCPU {ENV['host_os'].split()[0]} host")
para(f"Results below are actual measurements (not simulation) from {ENV['run_at'][:10]} on a {_host} with Docker {ENV['docker_server']}, using cgroup CPU quotas and memory limits. The load "
     "generator ran on the same host. " + _img_note + "Absolute numbers will "
     "differ on another machine; re-running the commands above regenerates every table and figure.")
h("Results", 3)
r = rec7["rule"]
table(["Measured at 25 req/s (before)", "Value", "Right-sizing calculation", "Value"], [
    ["Mean CPU used", f"{b7['cpu_cores_mean']:.3f} vCPU", "p95 CPU ÷ 0.70", f"{r['required_vcpu']:.3f} vCPU → {rec7['after']['cpus']:g} vCPU"],
    ["p95 CPU used", f"{b7['cpu_cores_p95']:.3f} vCPU", "workers = 2 × vCPU", f"{rec7['after']['workers']}"],
    ["Peak memory used", f"{b7['mem_mib_max']:.0f} MiB", "peak × workers ratio × 1.5", f"{r['required_mem_mib']:.0f} MiB → {rec7['after']['memory_mb']} MiB (Fargate minimum for {rec7['after']['cpus']:g} vCPU)"],
    ["CPU / memory utilisation of limit", f"{b7['cpu_util_pct']:.0f} % / {b7['mem_util_pct']:.1f} %", "", ""],
], widths=[4.5, 3, 4.5, 5], caption="Measured usage of the over-provisioned container and the right-sizing rule")
table(["Metric (same workload)", "Before: 2 vCPU / 4 GiB", f"After: {a7['vcpu']:g} vCPU / {a7['memory_gib']:g} GiB", "Change"], [
    ["Achieved rate (req/s)", f"{b7['achieved_rps']}", f"{a7['achieved_rps']}", "same"],
    ["p50 / p95 / p99 latency (ms)", f"{b7['p50_ms']:.0f} / {b7['p95_ms']:.0f} / {b7['p99_ms']:.0f}",
     f"{a7['p50_ms']:.0f} / {a7['p95_ms']:.0f} / {a7['p99_ms']:.0f}", f"p95 {c7['p95_delta_ms']:+.1f} ms"],
    ["Error rate", f"{b7['error_rate_pct']} %", f"{a7['error_rate_pct']} %", ""],
    ["Mean CPU used (vCPU)", f"{b7['cpu_cores_mean']:.2f}", f"{a7['cpu_cores_mean']:.2f}", "same work"],
    ["CPU utilisation of limit", f"{b7['cpu_util_pct']:.0f} %", f"{a7['cpu_util_pct']:.0f} %", "better utilised"],
    ["Peak memory (MiB) / utilisation", f"{b7['mem_mib_max']:.0f} / {b7['mem_util_pct']:.1f} %", f"{a7['mem_mib_max']:.0f} / {a7['mem_util_pct']:.1f} %", "fewer workers"],
    ["Max throughput, 12 users (req/s)", f"{b7['sat_rps']:.0f}", f"{a7['sat_rps']:.0f}", ("headroom ×" + f"{a7['sat_rps'] / 25:.1f} over design load") if a7["sat_rps"] >= 25 else "overload limit, see Exp 8"],
    ["SLO met at 25 req/s", "yes" if b7["meets_slo"] else "no", "yes" if a7["meets_slo"] else "no", ""],
    ["Estimated cost (USD/month, 1 task)", f"{b7['usd_per_month']:.2f}", f"{a7['usd_per_month']:.2f}", f"−{c7['cost_reduction_pct']:.0f} % (−{c7['monthly_saving_usd']:.2f})"],
], widths=[5, 3.8, 3.8, 4.4], caption="Before vs after right-sizing under the same 25 req/s workload")
figure("results/figures/exp7_before_after.png", "Utilisation and p95 latency before and after right-sizing", 16.5)
para(f"Costs are **estimates**, not charges: {PRICE['basis']} vCPU-hour USD {PRICE['vcpu_hour_usd']:.5f}, GB-hour USD "
     f"{PRICE['gb_hour_usd']:.6f}, {PRICE['hours_per_month']} h/month. {PRICE['caveat']}", size=9, color=MUTED)
h("Screenshots", 3)
for sid, what, how in [("S7a", "docker stats btms-bench during the 'before' run (CPU % and MEM USAGE / LIMIT)", "second terminal: docker stats btms-bench"),
                       ("S7b", "Console output of run_matrix.py for the before and after runs, and rightsize.py output", "commands above")]:
    shot(sid, what, how)
h("Conclusion", 3)
para(f"The initial container used only about {b7['cpu_util_pct']:.0f} % of its CPU and {b7['mem_util_pct']:.0f} % of its "
     f"memory at the design load. Halving CPU and memory, and reducing workers from 4 to {a7['workers']}, kept p95 latency "
     f"essentially unchanged ({b7['p95_ms']:.0f} → {a7['p95_ms']:.0f} ms, well inside the 250 ms SLO) with zero errors, "
     f"raised utilisation to {a7['cpu_util_pct']:.0f} %, and cut the estimated cost by {c7['cost_reduction_pct']:.0f} %. "
     + (f"The right-sized task still has {a7['sat_rps']:.0f} req/s of capacity, about {a7['sat_rps'] / 25:.1f}× the design load, "
        "and the Kubernetes HPA adds replicas beyond that. " if a7["sat_rps"] >= 25 else
        f"Under deliberate overload (12 closed-loop users) the 1 vCPU task completed only {a7['sat_rps']:.0f} req/s, fewer than it "
        "serves at the paced design load, because per-request CPU cost rises sharply when Gunicorn threads contend (explained in "
        "Experiment 8); bursts above the design load are therefore handled by the Kubernetes HPA adding replicas, as shown in "
        "Experiment 3. ")
     + "The right-sized limits are used in the Experiment 3 manifests.")

# ================================================================== 9  EXP 8
h("9. Experiment 8: FinOps principles and instance-type comparison", 1)
h("Aim", 3)
para("To run the same BTMS workload on different resource configurations and identify the most cost-efficient option that meets the performance target.")
h("Use-case connection", 3)
para("The use case stresses scalability from a laboratory prototype to commercial packs without large cost increases. "
     "FinOps applies that principle to the cloud side: every configuration is judged on cost per unit of useful work "
     "(predictions served within the SLO), not on raw speed.")
h("Procedure", 3)
restart()
for s in ["Choose four valid AWS Fargate task sizes, from 0.25 vCPU / 0.5 GiB to 2 vCPU / 4 GiB; Gunicorn workers scale with vCPU (bench/configs_exp8.json).",
          "Run the identical workload and conditions used in Experiment 7 (25 req/s for 60 s, then 12-user saturation for 30 s) on each size, sampling docker stats.",
          "Price each size with bench/pricing.json (estimates). Compute cost per task-month and cost per million requests when a task is loaded to 70 % of its measured maximum throughput.",
          "Select the cheapest size that meets the SLO at the design load. Also cost a 100 req/s peak (400 packs) by scaling out tasks of each size."]:
    numbered(s)
h("Commands", 3)
code("""python bench/run_matrix.py --image <user>/btms-api:1.0 --configs bench/configs_exp8.json --out results/exp8
python bench/analyze.py exp8 results/exp8""")
h("Results", 3)
rows8 = []
for x in r8:
    rows8.append([f"{x['vcpu']:g} vCPU / {x['memory_gib']:g} GiB ({x['workers']}w)",
                  f"{x['achieved_rps']}", f"{x['p95_ms']:.0f}" if x["p95_ms"] else "–", f"{x['error_rate_pct']}",
                  f"{x['cpu_util_pct']:.0f}" if x["cpu_util_pct"] is not None else "–",
                  f"{x['sat_rps']:.1f}", f"{x['sat_cpu_cores'] / x['sat_rps'] * 1000:.0f}" if x["sat_rps"] else "–",
                  "yes" if x["meets_slo"] else "no",
                  f"{x['usd_per_month']:.2f}",
                  f"{x['usd_per_million_req_at_70pct']:.3f}" if x["usd_per_million_req_at_70pct"] else "–",
                  f"{x['tasks_for_100rps']} → {x['fleet_usd_per_month']:.0f}" if x["tasks_for_100rps"] else "–"])
table(["Task size", "Served req/s (of 25)", "p95 ms", "Errors %", "CPU util %", "Max req/s", "CPU ms / req", "Meets SLO",
       "USD / task-month", "USD per 1M req", "Tasks for 100 req/s → USD/month*"],
      rows8, widths=[2.9, 1.5, 1.4, 1.2, 1.2, 1.3, 1.2, 1.2, 1.6, 1.5, 2.0], font=7,
      caption="Same workload on four task sizes (measured performance, estimated cost)")
para("* Tasks needed for a 100 req/s peak if each runs at 70 % of its measured maximum throughput.", size=8, color=MUTED)
figure("results/figures/exp8_instance_comparison.png", "p95 latency at the design load, maximum throughput and estimated monthly cost per task size", 16.5)
txt = []
eff = {x["name"]: (x["sat_cpu_cores"] / x["sat_rps"] * 1000 if x["sat_rps"] else None) for x in r8}
ok8 = [x for x in r8 if x["meets_slo"]]
fixed_ms = {x["name"]: (x["cpu_util_pct"] / 100 * x["vcpu"] / x["achieved_rps"] * 1000 if x["achieved_rps"] and x["cpu_util_pct"] is not None else None) for x in r8}
if fail8:
    txt.append("The " + " and ".join(f"{x['vcpu']:g} vCPU" for x in fail8) + " sizes fail the SLO. At 25 req/s they run at "
               "their CPU quota (about 100 % utilisation), requests queue, and once queueing passes the 5 s client timeout "
               "answers arrive too late (" + "; ".join(f"{x['vcpu']:g} vCPU: {x['achieved_rps']} req/s answered in time, "
                                                       f"{x['error_rate_pct']} % timeouts" for x in fail8) + ").")
if sel8:
    txt.append(f"The **{sel8['vcpu']:g} vCPU / {sel8['memory_gib']:g} GiB** task is the cheapest size that meets the SLO "
               f"(p95 {sel8['p95_ms']:.0f} ms, {sel8['error_rate_pct']} % errors, {sel8['cpu_util_pct']:.0f} % CPU) at about "
               f"USD {sel8['usd_per_month']:.2f} per month, the same size Experiment 7 arrived at from utilisation alone; "
               "2 vCPU gives no measurable latency benefit at the design load for twice the price.")
ok_eff = [x for x in ok8 if eff[x["name"]]]
if ok_eff:
    txt.append("The saturation phase revealed a second effect. At the paced design load the service needs about " +
               " / ".join(f"{fixed_ms[x['name']]:.0f}" for x in ok8) + " ms of CPU per request, but under 12-user overload "
               "this rose to about " + " / ".join(f"{eff[x['name']]:.0f}" for x in ok_eff) + " ms, so the measured maximum "
               "throughput (" + ", ".join(f"{x['vcpu']:g} vCPU: {x['sat_rps']:.0f}" for x in r8) + " req/s) is far below "
               "what the CPU quota alone would allow.")
    if TH:
        txt.append(f"A follow-up check on the 2 vCPU size, changing only the Gunicorn threads per worker, confirmed the cause: "
                   f"with 4 threads it completed {TH['threads_4']['achieved_rps']:.0f} req/s, with 1 thread "
                   f"{TH['threads_1']['achieved_rps']:.0f} req/s for the same CPU time (results/exp8_threads_check.json). "
                   "The model inference is CPU-bound Python, so extra threads only contend for the interpreter lock while the "
                   "kernel throttles the CPU quota. The reported runs keep the published configuration (4 threads); using one "
                   "thread per worker is the first improvement to make before sizing for peaks.")
if fleet8:
    by_fleet = sorted((x for x in r8 if x["fleet_usd_per_month"]), key=lambda x: x["fleet_usd_per_month"])
    txt.append("Because the overload throughput is depressed in the same way for every size, cost per million requests at 70 % "
               "of that throughput is almost identical across sizes (" +
               ", ".join(f"{x['vcpu']:g} vCPU USD {x['usd_per_million_req_at_70pct']:.2f}" for x in by_fleet) +
               "), and the 100 req/s fleet estimates are close (" +
               ", ".join(f"{x['tasks_for_100rps']} × {x['vcpu']:g} vCPU ≈ USD {x['fleet_usd_per_month']:.0f}" for x in by_fleet) +
               "). Sizes that fail the SLO at the design load are excluded from the choice, since one task would breach the "
               "SLO whenever the autoscaler lags. The FinOps decision is therefore driven by the SLO at the design load: "
               f"{sel8['vcpu']:g} vCPU tasks, scaled out horizontally, give the smallest capacity step that is known to work.")
para(" ".join(txt))
para(f"Costs are **estimates** from list prices ({PRICE['region']}, retrieved {PRICE['retrieved']}); no cloud resources "
     f"were created and no charges were incurred. For reference, EC2 t3.small (2 vCPU burstable, 2 GiB) in Mumbai lists "
     f"at USD {PRICE['ec2_reference_ap_south_1']['t3.small']['usd_hour']}/hour (source in bench/pricing.json); burstable "
     "credits make t3 throughput at sustained load less predictable, so the comparison above uses fixed CPU quotas.",
     size=9, color=MUTED)
h("Screenshots", 3)
shot("S8a", "Console output of run_matrix.py for the four sizes and analyze.py exp8 summary", "commands above")
h("Conclusion", 3)
para((f"Running one workload across four sizes showed a threshold rather than a smooth trade-off: the fractional "
      f"sizes cannot carry the design load at all, while 2 vCPU only adds idle headroom ({next(x for x in r8 if x['vcpu'] == 2)['cpu_util_pct']:.0f} % "
      f"utilisation) at twice the price. The FinOps choice is the {sel8['vcpu']:g} vCPU / {sel8['memory_gib']:g} GiB task, "
      "scaled horizontally for peaks, with cost tracked per million predictions. The overload test also exposed a cheap "
      "efficiency gain (one Gunicorn thread per worker) that would raise capacity per task without buying more CPU.")
     if sel8 else "No size met the SLO.")

# ================================================================== 10 TESTING
h("10. Testing and performance/cost comparison", 1)
h("10.1 Representative inputs", 2)
trows = []
for v in SCN["valid"]:
    rec = v["opt_recommendation"]
    trows.append([v["name"].split("_", 1)[0], v["name"].split("_", 1)[1].replace("_", " "),
                  f"{v['t_max_c']:.1f} [{v['t_max_interval_90'][0]:.1f}, {v['t_max_interval_90'][1]:.1f}]",
                  f"{v['twin_t_max_c']:.1f}", f"{v['delta_t_c']:.2f}", v["risk"], ", ".join(v["hotspot_top3"]),
                  ("Re " + f"{rec['reynolds']:.0f}, inlet {rec['inlet_temp_c']:.0f} °C, φ {rec['phi_pct']:.1f} %")
                  if v["opt_status"] == "feasible" else "infeasible: pre-cool / escalate"])
table(["ID", "Scenario", "T_max °C [90 % interval]", "Twin T_max °C", "ΔT °C", "Risk", "Hotspot top-3", "Optimiser"],
      trows, widths=[0.9, 3.3, 2.6, 1.5, 1.1, 1.5, 2.2, 3.9], font=7.5,
      caption="Six representative scenarios (data/sample_inputs.json): real API outputs")
para("All six scenarios produce physically sensible results: normal for city driving and moderate charging, warning on a "
     "hot highway day, critical for 3C discharge in a heatwave, and an explicit 'pre-cool' instruction when the pack is "
     "already at 44 °C before load. The digital-twin column is a nominal pack without random cell imbalance, so it is "
     "expected to sit slightly below the ML prediction for aged packs.")
h("10.2 Invalid inputs and failure scenarios", 2)
irows = []
for v in SCN["invalid"]:
    e = v["errors"]
    msg = "; ".join(f"{x['field']}: {x['error']}" for x in e) if isinstance(e, list) else str(e)
    irows.append([v["name"].split("_", 1)[0], v["name"].split("_", 1)[1].replace("_", " "), v["status"], msg[:150]])
f1 = SCN["failure"][0]
irows.append(["F1", "model file missing at start-up", f"{f1['health_status']} / {f1['predict_status']}",
              "health reports 'degraded'; predict returns 503 'model unavailable' instead of crashing; the readiness probe takes the pod out of the Service"])
ov = next((x for x in r8 if not x["meets_slo"]), None)
if ov:
    irows.append(["F2", f"overload: {ov['vcpu']:g} vCPU task at 25 req/s", "timeouts",
                  f"only {ov['achieved_rps']} req/s answered within 5 s, {ov['error_rate_pct']} % errors (Exp 8); motivates right-sizing, HPA and load shedding"])
irows.append(["F3", "pod deleted / bad image rollout (Exp 3)", "200 (old pods)" if E3 else fill("observed"), E3.get("f3", "Deployment recreates the pod; ImagePullBackOff contained, rollout undo")])
table(["ID", "Case", "HTTP status", "Service response"], irows, widths=[0.9, 4.2, 2, 9.9], font=7.5,
      caption="Invalid-input and failure-scenario tests")
para(f"**Automated tests:** pytest runs {n_pass + n_fail} tests (health, dashboard, six representative scenarios, "
     f"optimiser constraints, batch endpoint, digital twin, five invalid inputs, malformed JSON, oversize batch, bad "
     f"optimiser option, unknown route/method, missing-model failure, physics trends and the optimiser's monotone "
     f"envelope). Result: **{n_pass} passed, {n_fail} failed** (results/pytest_output.txt).")
h("10.3 Performance and cost summary", 2)
table(["Measurement", "Value", "Source"], [
    ["T_max prediction error (grey-box)", f"MAE {gb['test']['mae']:.2f} °C test, {gb['ood']['mae']:.2f} °C unseen hot weather", "models/metrics.json"],
    ["90 % interval coverage", f"{iv['empirical_coverage_test'] * 100:.1f} % (target 90 %)", "models/metrics.json"],
    ["In-process latency, single thread", f"predict {lat['/api/predict']:.1f} ms, optimise {lat['/api/optimize']:.1f} ms, digital twin {lat['/api/simulate']:.1f} ms", "results/test_scenarios.json"],
    ["Containerised p95 at 25 req/s (1 vCPU)", f"{a7['p95_ms']:.0f} ms, {a7['error_rate_pct']} % errors", "Exp 7"],
    ["Max throughput per task", ", ".join(f"{x['vcpu']:g} vCPU: {x['sat_rps']:.0f}" for x in r8) + " req/s", "Exp 8"],
    ["Right-sizing saving", f"{c7['cost_reduction_pct']:.0f} % (USD {c7['monthly_saving_usd']:.2f} per task-month, estimate)", "Exp 7"],
    ["Selected size", f"{sel8['vcpu']:g} vCPU / {sel8['memory_gib']:g} GiB, USD {sel8['usd_per_month']:.2f}/month (estimate)" if sel8 else "–", "Exp 8"],
], widths=[5, 8.5, 3.5], caption="Key measurements")

# ================================================================== 11
h("11. Limitations, data privacy and improvements", 1)
h("11.1 Limitations", 2)
for b in ["**Synthetic data.** Labels come from a simplified lumped model written for this project, not from ANSYS CFD or the bench-scale rig. The physics prior fits so well partly because data and prior share the same physics family; with real data the ML correction would carry more of the load and accuracy would need re-validation.",
          "**Hotspot location** is only moderately predictable (top-1 " f"{hs['hist_gradient_boosting']['top1_accuracy'] * 100:.0f} %) without per-cell sensor inputs.",
          f"**Extrapolation.** Accuracy held on the unseen hot-weather set (MAE {gb['ood']['mae']:.2f} °C, interval coverage "
          f"{iv['empirical_coverage_ood'] * 100:.0f} %), but that test only extends ambient temperature by 3 °C; combinations far "
          "outside the training envelope are untested, so such inputs are rejected rather than predicted.",
          "**Chiller model** uses a fixed coefficient of performance; nanofluid stability, agglomeration and channel clogging (use-case challenge b) are not modelled.",
          "**Benchmarks** ran on one Windows laptop (Docker Desktop VM) with the load generator on the same machine; overload throughput was limited by Gunicorn thread contention under CPU quotas (Experiment 8), and minikube is a single-node cluster.",
          "**Costs** are list-price estimates for us-east-1 Fargate; Mumbai prices, data transfer, load balancer and storage are excluded."]:
    bullet(b)
h("11.2 Data privacy and security", 2)
para("The data is synthetic and contains no personal information. In production, pack telemetry is commercially "
     "sensitive (it reveals cell chemistry behaviour and fleet usage) and, when tied to a vehicle, can become personal "
     "data (location and driving patterns), which brings India's Digital Personal Data Protection Act 2023 into scope. "
     "Measures implemented: input validation with a 256 KB request cap, no request bodies written to logs, non-root "
     "container, minimal base image. Measures still needed: TLS at the ingress, API keys or OAuth per gateway, Kubernetes "
     "Secrets and NetworkPolicies, pseudonymised pack identifiers, encryption of stored telemetry, and role-based access "
     "for research and industrial-partner datasets, as listed in the use case's governance table.")
h("11.3 Possible improvements", 2)
for b in ["Retrain on CFD and bench-rig data from the KJSSE project; keep the physics prior and conformal calibration; add drift monitoring and periodic recalibration.",
          "Add per-cell temperature and resistance inputs to locate hotspots and to synchronise the digital twin with live data.",
          "Edge deployment: export the model (e.g. ONNX) for the vehicle control unit, with the cloud service for fleet analytics (use-case challenge g).",
          "Admission control or load shedding so overload degrades gracefully instead of timing out; autoscale on request rate as well as CPU.",
          "Add authentication, TLS, Prometheus/Grafana dashboards from the /metrics endpoint, and a CI pipeline that runs tests and pushes images.",
          "Run Experiments 7 and 8 on a real cloud account and reconcile measured costs against these estimates."]:
    bullet(b)

# ================================================================== 12
h("12. References", 1)
restart()
for ref in [
    "Somaiya Vidyavihar University, 'Framework for AI Use Case Integration in Curriculum Delivery: KJS-CES-02 AI-Enabled Micro-channel Battery Thermal Management System for Electric Vehicles', faculty owners Dr. S. Bhusnoor and Dr. M. Pawar.",
    "Somaiya Vidyavihar University, 'Cloud Computing Experiments through Somaiya Vidyavihar AI Use Cases', assignment brief and experiment manuals 2, 3, 7, 8.",
    "J. C. Maxwell, A Treatise on Electricity and Magnetism, Clarendon Press, 1873 (effective conductivity of suspensions).",
    "G. K. Batchelor, 'The effect of Brownian motion on the bulk stress in a suspension of spherical particles', J. Fluid Mech., 83(1), 1977.",
    "F. P. Incropera, D. P. DeWitt et al., Fundamentals of Heat and Mass Transfer, Wiley (laminar internal flow, Hausen correlation).",
    "D. Bernardi, E. Pawlikowski, J. Newman, 'A general energy balance for battery systems', J. Electrochem. Soc., 132(1), 1985.",
    "A. N. Angelopoulos, S. Bates, 'A gentle introduction to conformal prediction and distribution-free uncertainty quantification', arXiv:2107.07511, 2021.",
    "F. Pedregosa et al., 'Scikit-learn: Machine Learning in Python', JMLR 12, 2011; scikit-learn 1.9 documentation (HistGradientBoosting, monotonic constraints).",
    "Docker documentation: Dockerfile reference, docker run resource constraints, docker stats. https://docs.docker.com",
    "Kubernetes documentation: Deployments, Services, probes, Horizontal Pod Autoscaling. https://kubernetes.io/docs",
    "AWS Fargate pricing, https://aws.amazon.com/fargate/pricing/ (retrieved " + PRICE["retrieved"] + ").",
    "FinOps Foundation, FinOps Framework principles, https://www.finops.org/framework/",
]:
    numbered(ref)

# ================================================================== 13
h("13. Acknowledgement of AI-assisted work", 1)
para(STUDENT.get("ai_acknowledgement",
     "An AI assistant (Claude Code, by Anthropic) was used throughout this project. It helped write the application, "
     "benchmark and report-generation code and a first draft of this report and, under our supervision on our own "
     "machine, it ran the Docker, Kubernetes and benchmark experiments and captured the screenshots from those runs. "
     "We reviewed the code, checked the numbers in this report against the generated result files and raw command "
     "logs, and are able to explain each component of the implementation."))

# ================================================================== APPENDIX
h("Appendix A. Source code, repository layout and reproduction", 1)
para(f"Repository / ZIP: {STUDENT.get('repo_url', fill('GitHub link or ZIP name'))}")
code("""app/            physics.py, generate_data.py, train.py, greybox.py, model.py, validation.py, api.py, templates/
wsgi.py, gunicorn.conf.py, Dockerfile, .dockerignore, requirements.txt, requirements-dev.txt
k8s/            namespace.yaml, deployment.yaml, service.yaml, hpa.yaml
bench/          loadgen.py, run_matrix.py, rightsize.py, analyze.py, configs_exp7_*.json, configs_exp8.json, pricing.json
tests/          test_api.py (pytest), run_scenarios.py
data/           btms_synthetic.csv (generated), sample_inputs.json
models/         btms_model.joblib, metrics.json (generated)
results/        exp7_before/, exp7_after/, exp8/, figures/, test_scenarios.json, pytest_output.txt
docs/           architecture.dot/png, dataflow.dot/png, demo_script.md
report/         build_report.py, student.json, screenshots/""")
code("""pip install -r requirements-dev.txt
python -m app.generate_data && python -m app.train && pytest -v && python tests/run_scenarios.py
docker build -t <user>/btms-api:1.0 .            # Exp 2  (see README for all steps)
kubectl apply -f k8s/                            # Exp 3
python bench/run_matrix.py ... ; python bench/analyze.py ...   # Exp 7, 8
python report/build_report.py                    # rebuild this report""")

doc.save(OUT)
print(f"wrote {OUT}")

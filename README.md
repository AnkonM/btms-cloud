# AI-Enabled Micro-channel BTMS: Cloud Prototype

Cloud Computing mini project, K J Somaiya School of Engineering.
Team (Batch B1): Aneeket Banerjee (16014223009), Ankon Mukherjee (16014223014), Atharva Gondavale (16014223023).

## Overview

**Use case KJS-CES-02:** AI-Enabled Micro-channel Battery Thermal Management System for Electric Vehicles.

The project is a cloud-hosted prediction service for a 10-cell 21700 battery pack cooled by micro-channels. You give it
an operating condition: C-rate, state of charge, state of health, ambient and coolant temperature, Reynolds number and
nanofluid concentration. It returns:

* the predicted peak cell temperature, with a 90 % confidence interval
* the temperature spread across cells (ΔT) and the likely hotspot cell
* a risk level from a rule-based safety layer
* a recommended coolant setting (Reynolds number, nanofluid %, inlet temperature) that uses the least cooling power while
  keeping T_max ≤ 40 °C and ΔT ≤ 5 °C, flagged as "engineer approval required"
* an optional digital-twin time series of per-cell temperatures

The model is **grey-box**: a closed-form physics estimate plus a gradient-boosting correction. It is trained on
**synthetic** data from a physics-informed lumped thermal model (not CFD or real measurements), so recommendations are
advisory only.

**Stack:** Python 3.13, scikit-learn, Flask + Gunicorn, Docker, Docker Hub, Kubernetes (minikube), metrics-server / HPA.

**Experiments covered:**

| Exp | Topic | Where |
|---|---|---|
| 2 | Containerization, Docker and Docker Hub | `Dockerfile`, image `ankonm/btms-api:1.0` |
| 3 | Application deployment using Kubernetes | `k8s/` (namespace, deployment, NodePort service, HPA) |
| 7 | Cloud cost optimization through resource right-sizing | `bench/run_matrix.py`, `bench/rightsize.py` |
| 8 | FinOps principles and instance-type comparison | `bench/configs_exp8.json`, `bench/analyze.py`, `bench/pricing.json` |

**Layout:** `app/` (physics model, data generator, training, API and dashboard), `k8s/`, `bench/` (load generator and
benchmarks), `tests/`, `data/`, `models/`, `results/` (measured outputs and figures), `docs/` (architecture diagrams,
demo script), `report/` (report generator and screenshots).

**API:** `GET /` dashboard · `GET /api/health` · `GET /api/model-info` · `GET /api/sample` · `POST /api/predict` ·
`POST /api/predict/batch` · `POST /api/optimize` · `POST /api/simulate` · `GET /metrics`

---

## Prerequisites (Windows 10/11, PowerShell)

1. Python 3.13 on PATH
2. Docker Desktop (WSL 2 backend), running
3. minikube and kubectl: `winget install Kubernetes.minikube Kubernetes.kubectl`
4. A Docker Hub account (only needed to push your own image)

Run every command from the project folder. Set your Docker Hub username once per PowerShell window:

```powershell
$U = "your-dockerhub-username"     # or use "ankonm" to pull the published image instead of building
```

## 1. Run locally and test

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m app.generate_data            # data/btms_synthetic.csv (20,000 rows, seed 42)
python -m app.train                    # models/btms_model.joblib, models/metrics.json, results/figures/
pytest -v                              # expect 24 passed
python tests/run_scenarios.py          # results/test_scenarios.json
python wsgi.py                         # dashboard at http://localhost:8000, Ctrl+C to stop
```

## 2. Experiment 2: Docker and Docker Hub

```powershell
docker build -t "$U/btms-api:1.0" .
docker images "$U/btms-api"
docker run -d --name btms -p 8000:8000 "$U/btms-api:1.0"
docker ps                                          # wait for STATUS "(healthy)"
Invoke-RestMethod http://localhost:8000/api/health

$S = Get-Content data\sample_inputs.json -Raw | ConvertFrom-Json
$body = $S.valid.T2_highway_hot_day_warning | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/predict  -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/optimize -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 6

$bad = $S.invalid.I1_out_of_range_reynolds | ConvertTo-Json      # expect HTTP 422
try { Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/predict -ContentType "application/json" -Body $bad } catch { $_.Exception.Response.StatusCode; $_.ErrorDetails.Message }

docker logs btms
docker login
docker push "$U/btms-api:1.0"
docker rm -f btms
```

## 3. Experiment 3: Kubernetes (minikube)

```powershell
minikube start --driver=docker --cpus=4 --memory=4096
minikube addons enable metrics-server
kubectl apply -f k8s\namespace.yaml
(Get-Content k8s\deployment.yaml) -replace 'DOCKERHUB_USER', $U | kubectl apply -f -
kubectl apply -f k8s\service.yaml -f k8s\hpa.yaml
kubectl rollout status deployment/btms-api -n btms
kubectl get pods,svc,hpa -n btms -o wide
minikube service btms-api -n btms --url            # leave this window open; note the printed URL
```

In a second PowerShell window (set `$U` again):

```powershell
$URL = "http://127.0.0.1:<port-from-above>"
1..6 | % { (Invoke-RestMethod "$URL/api/health" -DisableKeepAlive).host }   # load balancing: pod names alternate

# Manual scaling (the HPA would scale straight back, so remove it for this step)
kubectl delete hpa btms-api -n btms
kubectl scale deployment btms-api -n btms --replicas=4
kubectl get pods -n btms

# Self-healing
kubectl delete pod <one-pod-name> -n btms
kubectl get pods -n btms -w                        # replacement pod becomes Ready; Ctrl+C

# Autoscaling under load
kubectl scale deployment btms-api -n btms --replicas=2
kubectl apply -f k8s\hpa.yaml
kubectl get hpa -n btms -w                         # in a third window
python bench\loadgen.py --url $URL --concurrency 8 --seconds 180

# Failure scenario: bad image tag, then roll back
kubectl set image deployment/btms-api btms-api="$U/btms-api:does-not-exist" -n btms
kubectl get pods -n btms                           # ImagePullBackOff; old pods keep serving
Invoke-RestMethod "$URL/api/health"
kubectl rollout undo deployment/btms-api -n btms
kubectl rollout status deployment/btms-api -n btms

# Clean up
kubectl delete namespace btms
minikube stop
```

## 4. Experiment 7: Right-sizing

Docker Desktop must be running. Each benchmark run takes about 2 minutes.

```powershell
python bench\run_matrix.py --image "$U/btms-api:1.0" --configs bench\configs_exp7_before.json --out results\exp7_before
python bench\rightsize.py results\exp7_before\exp7_before_2vcpu_4gb.json --out bench\configs_exp7_after.json
python bench\run_matrix.py --image "$U/btms-api:1.0" --configs bench\configs_exp7_after.json --out results\exp7_after
python bench\analyze.py exp7 results\exp7_before results\exp7_after
```

To watch resource use during a run, use `docker stats btms-bench` in another window.
Outputs: `results/exp7_comparison.json` and `results/figures/exp7_before_after.png`.

## 5. Experiment 8: FinOps instance-size comparison

```powershell
python bench\run_matrix.py --image "$U/btms-api:1.0" --configs bench\configs_exp8.json --out results\exp8
python bench\analyze.py exp8 results\exp8
```

Outputs: `results/exp8_finops.csv`, `results/exp8_finops.json` and `results/figures/exp8_instance_comparison.png`.
Costs are estimates from AWS Fargate list prices in `bench/pricing.json`. Nothing is deployed to AWS.

## 6. Rebuild the report (optional)

```powershell
pip install python-docx
python report\build_report.py                      # report\CC_UseCase_Report.docx, built from results\ and report\screenshots\
```

Open the .docx in Word and use **File → Save As → PDF**.

## Reproducibility notes

* Seeds are fixed (data 42, load-generator payloads 2026/7) and dependency versions are pinned in `requirements.txt`.
* The Docker build regenerates the dataset and retrains the model, so the image doesn't depend on a model file built
  with a different scikit-learn version.
* Benchmark numbers depend on the host, because the load generator shares the CPU with the container. Absolute
  throughput and latency will differ between machines; the procedure and the comparisons stay the same.

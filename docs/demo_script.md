# 5-minute demonstration video: script and shot list

Record with OBS or the Windows Game Bar (Win + Alt + R). Keep the terminal font large (≥ 16 pt).
Say your name and roll number in the first 5 seconds. Times are targets.

| Time | Show on screen | Say (paraphrase in your own words) |
|---|---|---|
| 0:00-0:30 | Title slide or README; Fig. 1 of the use-case PDF | "Use case KJS-CES-02: AI-enabled micro-channel battery cooling for EVs. I built a cloud prediction service that forecasts peak cell temperature, cell spread and hotspot, and recommends coolant settings. The model is grey-box: a physics estimate plus a gradient-boosting correction. Data is synthetic from a physics surrogate. Experiments 2, 3, 7 and 8." |
| 0:30-0:50 | Architecture diagram (docs/architecture.png) | Walk the path: data generator → model → Docker image → Docker Hub → Kubernetes → users; benchmark harness on the side. |
| 0:50-1:50 | **App working.** Dashboard: pick scenario "T2 highway hot day warning" → Predict → Optimise cooling → Run digital twin. Then "Send invalid input" | Point at the 90 % interval, the warning level, the recommended Re/inlet, "engineer approval required". Show the 422 response listing the bad fields. |
| 1:50-2:40 | **Exp 2.** Terminal: `docker images`, `docker ps` (healthy), `docker logs btms`, browser on your Docker Hub repo page | "The image builds from python:3.13-slim, retrains the model at build time, runs as a non-root user and has a HEALTHCHECK. It is pushed to Docker Hub." |
| 2:40-3:50 | **Exp 3.** `kubectl get pods,svc,hpa -n btms -o wide`; call `/api/health` twice (host changes); `kubectl scale --replicas=4`; `kubectl delete pod …` then `kubectl get pods -w` showing the replacement; HPA under load if time allows | "Two replicas behind a NodePort service, readiness and liveness probes on /api/health, the HPA adds pods above 70 % CPU. Deleting a pod: the Deployment recreates it with no downtime." |
| 3:50-4:30 | **Exp 7.** `results/figures/exp7_before_after.png` and the numbers table; optionally `docker stats` during a run | "Initial 2 vCPU / 4 GiB used about a fifth of its CPU and under 5 % of its memory at 25 req/s. Rule: p95 CPU ÷ 0.7, rounded up to a Fargate size → 1 vCPU / 2 GiB. Same workload afterwards: still meets the 250 ms p95 target, half the estimated cost." |
| 4:30-4:55 | **Exp 8.** `results/figures/exp8_instance_comparison.png` | "Same workload on four sizes. 0.25 and 0.5 vCPU fail the latency target; 1 vCPU is the cheapest that passes. Costs are estimates from AWS Fargate list prices." |
| 4:55-5:00 | README / repo link | "Code, configs and README are in the repository." |

Before recording: start Docker Desktop and minikube, open the dashboard tab, have the figures open, and
paste long commands into a text file so you can copy them quickly.

# Demo video script (about 5½ minutes, three speakers)

| Part | Speaker | Covers | Target time |
|---|---|---|---|
| 1 | Aneeket Banerjee (16014223009) | Intro, use case, architecture, the app working | 0:00 – 1:45 |
| 2 | Ankon Mukherjee (16014223014) | Experiment 2 (Docker, Docker Hub) and Experiment 3 (Kubernetes) | 1:45 – 3:45 |
| 3 | Atharva Gondavale (16014223023) | Experiment 7 (right-sizing), Experiment 8 (FinOps), wrap-up | 3:45 – 5:30 |

Swap the speakers around if you like. Each part stands on its own.

**Before you hit record**
- Start Docker Desktop. Run `docker run -d --name btms -p 8000:8000 -e ACCESS_LOG=1 ankonm/btms-api:1.0` and wait until `docker ps` shows *healthy*.
- Start minikube and deploy the app (README step 3). Keep the `minikube service --url` window open.
- Open these and leave them ready:
  - a browser tab on `http://localhost:8000`
  - a tab on the Docker Hub repo page
  - a tab on the GitHub repo
  - `docs/architecture.png`
  - `results/figures/exp7_before_after.png`
  - `results/figures/exp8_instance_comparison.png`
- Paste the long commands into a notepad so you can copy them in without typing live.
- Set the terminal font to 16 pt or bigger. Use OBS or Win + Alt + R to record.
- Lines in *italics* are what to do on screen. Everything else is what you say. You don't have to read it word for word. It's fine to put it in your own words.

---

## Part 1: Aneeket (intro and the app)

*[Screen: the GitHub repo README, or a title slide]*

Hi, I'm Aneeket Banerjee, roll number 16014223009. With me are Ankon and Atharva, and we're from batch B1. This is our Cloud Computing mini project.

We picked use case KJS-CES-02: an AI-based battery thermal management system for electric vehicles. The idea is a battery pack cooled by tiny micro-channels with coolant flowing through them. The problem is that if a cell gets too hot, or if some cells run much hotter than others, the battery wears out faster and can even become unsafe.

So we built a cloud service that predicts how hot the pack's hottest cell will get, how uneven the temperatures will be across cells, and which cell is likely to be the hotspot. It then suggests coolant settings that keep everything safe while using as little cooling power as possible.

One thing to be upfront about: the data is synthetic. We generated it from a physics-based model of the pack, because we don't have a real battery rig. So treat this as a prototype, not a product.

*[Screen: docs/architecture.png]*

Here's the big picture. We generate the data and train the model. The model is what we call grey-box: a simple physics formula makes a first guess, and a gradient-boosting model learns to correct it. That gets us within about a quarter of a degree on the test data.

Then the model is wrapped in a Flask API, packaged into a Docker image, pushed to Docker Hub and deployed on Kubernetes. On the side there's a benchmarking setup that we used for the cost experiments.

*[Screen: browser on localhost:8000. Pick "T2 highway hot day warning" from the Scenario dropdown and click Predict]*

This is the dashboard. I'll pick a tough case: highway driving on a hot day with an older battery. When I click Predict, it says the hottest cell will reach about 41 degrees, and it gives a range rather than just one number. That's slightly over our 40-degree limit, so the risk shows as *warning*. You can also see which cell is the likely hotspot on the pack diagram.

*[Click "Optimise cooling"]*

Now if I hit Optimise cooling, it tries around 500 different coolant settings and picks the cheapest one that brings us back under the limit. Here it says to push the flow up and use more nanofluid. And notice it says engineer approval is required. It only advises, it doesn't control anything by itself.

*[Click "Run digital twin"]*

The digital twin simulates every cell's temperature over the next 20 minutes, so you can see how it plays out over time.

*[Click "Send invalid input"]*

And finally, if someone sends bad data, like a flow rate that's out of range, the API rejects it with a 422 error and tells you exactly which field is wrong.

Over to Ankon for the Docker and Kubernetes part.

---

## Part 2: Ankon (Experiments 2 and 3)

*[Screen: terminal]*

Thanks, Aneeket. I'm Ankon Mukherjee, roll number 16014223014. I'll show how we containerised the app and ran it on Kubernetes.

*[Run: `docker images ankonm/btms-api`]*

Starting with Experiment 2, Docker. This is our image. It's built on Python 3.13 slim, and during the build it actually regenerates the data and retrains the model, so the model always matches the library versions inside the image. It also runs as a non-root user, which is a basic security thing.

*[Run: `docker ps`]*

Here it's running, and you can see it says *healthy*. That's because we added a health check: Docker keeps calling our health endpoint to make sure the app is actually working, not just that the process is up.

*[Run: `docker logs btms`]*

These are the logs. You can see Gunicorn starting up its workers, and then the requests Aneeket just made from the dashboard.

*[Switch to the Docker Hub tab]*

And we pushed the image to Docker Hub, so anyone can pull it with a single command. That's also where Kubernetes pulls it from.

*[Back to terminal. Run: `kubectl get pods,svc,hpa -n btms -o wide`]*

Now Experiment 3, Kubernetes. We're running it locally on minikube. We've got two copies of the app running, called pods, behind a service, plus an autoscaler that adds more pods when CPU usage goes above 70 percent.

*[Run: `1..6 | % { (Invoke-RestMethod "$URL/api/health" -DisableKeepAlive).host }`]*

If I call the health endpoint a few times, look at the host name: it keeps switching between the two pods. So Kubernetes is spreading the traffic across them.

*[Run: `kubectl delete hpa btms-api -n btms`, then `kubectl scale deployment btms-api -n btms --replicas=4`, then `kubectl get pods -n btms`]*

Scaling is one command. I'll switch the autoscaler off for a second, otherwise it would just scale straight back down. Then I ask for four pods, and within a few seconds all four are up and ready.

*[Run: `kubectl delete pod <a pod name> -n btms`, then `kubectl get pods -n btms -w`]*

Now let's break something. I'll delete one of the pods... and Kubernetes notices straight away and starts a replacement. In our test that took about four and a half seconds, and the other pods kept answering in the meantime.

*[Ctrl+C. Run: `kubectl scale deployment btms-api -n btms --replicas=2` and `kubectl apply -f k8s\hpa.yaml`]*

I'll put things back to two pods and turn the autoscaler back on. When we load-tested it earlier, the autoscaler went from two pods up to five, and out of almost eleven thousand requests, not a single one failed.

*[Optional, if time allows: `kubectl set image deployment/btms-api btms-api=ankonm/btms-api:does-not-exist -n btms`, `kubectl get pods -n btms`, then `kubectl rollout undo deployment/btms-api -n btms`]*

One last failure case: if someone deploys a broken image tag, the new pod just gets stuck, but the old pods keep serving traffic, so users don't notice anything. Then one undo command rolls it back.

Atharva will now talk about cost.

---

## Part 3: Atharva (Experiments 7 and 8, wrap-up)

*[Screen: results/figures/exp7_before_after.png]*

Thanks, Ankon. I'm Atharva Gondavale, roll number 16014223023. I'll cover the cost side: Experiments 7 and 8.

Experiment 7 is right-sizing. We started with a container sized like a typical default: 2 virtual CPUs and 4 gigs of memory. Then we hit it with a steady 25 requests per second and measured what it actually used.

It turned out it was only using about 20 percent of its CPU and around 5 percent of its memory. So we were paying for a lot of capacity that just sat there.

Our rule was simple: take the peak CPU usage, divide by 0.7 so we keep some headroom, and round up to the nearest size AWS Fargate actually offers. That gave us 1 CPU and 2 gigs.

Then we ran the exact same test on the smaller size. Latency stayed basically the same, around 24 milliseconds at the 95th percentile, way under our 250-millisecond target, with zero errors. The estimated cost dropped by half, from about 72 dollars a month to 36.

*[Screen: results/figures/exp8_instance_comparison.png]*

Experiment 8 takes a FinOps angle: instead of only asking what's cheapest, we ask what's cheapest while still meeting the service target. We ran the same workload on four sizes, from a quarter of a CPU up to two CPUs.

The two smallest ones look cheap on paper, but they just couldn't keep up. Response times shot up to around 4 seconds, so they fail. The 1-CPU size is the cheapest one that actually passes, so that's what we'd pick. It's also the same answer Experiment 7 gave us, which is a nice cross-check.

All the costs are estimates from AWS Fargate's public prices. We didn't deploy anything on AWS.

There's one interesting thing we found along the way. When we pushed the containers to their limit, more threads per worker actually made it slower, because of how Python handles threads. With one thread per worker, the same CPU handled about three times more requests. That's something we'd tune next.

*[Screen: GitHub repo page]*

To wrap up: we built an AI model for battery cooling, packaged it with Docker, ran it on Kubernetes with scaling and self-healing, and then used real measurements to cut its estimated cost in half without hurting performance.

All the code, the Kubernetes files, the benchmark results and step-by-step instructions are in our GitHub repo. Thanks for watching!

---

## Numbers to keep handy (all from our runs)

| What | Value |
|---|---|
| Model accuracy (peak temperature) | about 0.23 °C average error on test data |
| T2 scenario prediction | about 41.3 °C, risk *warning* |
| Docker image | `ankonm/btms-api:1.0`, built in about 2½ min, healthy about 6 s after start |
| Pods ready after deploy | 51 s (includes pulling the image from Docker Hub) |
| Scale to 4 pods | 4.7 s |
| Deleted pod replaced | 4.5 s |
| Autoscaler under load | 2 → 5 pods, 10,930 requests, 0 errors |
| Exp 7 before → after | 2 vCPU / 4 GiB → 1 vCPU / 2 GiB; p95 27 → 24 ms; about $72 → $36 per month |
| Exp 8 | 0.25 and 0.5 vCPU fail (p95 about 4 s); 1 vCPU is the cheapest that passes |
| Threads finding | 4 threads: 37 req/s; 1 thread: 120 req/s on the same CPU |

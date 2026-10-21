# ---------------------------------------------------------------------------
# BTMS prediction service - Experiment 2 (Containerization, Docker, Docker Hub)
# Build:  docker build -t <dockerhub-user>/btms-api:1.0 .
# Run:    docker run -d -p 8000:8000 --name btms <dockerhub-user>/btms-api:1.0
# ---------------------------------------------------------------------------
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_NUM_THREADS=1 \
    WORKERS=2 \
    THREADS=4 \
    PORT=8000

WORKDIR /app

# 1. dependencies first, so this layer is cached between code changes
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2. application code and sample scenarios
COPY app/ app/
COPY data/sample_inputs.json data/sample_inputs.json
COPY wsgi.py gunicorn.conf.py ./

# 3. regenerate the synthetic dataset and fit the deployed model inside the image.
#    Seeds are fixed, so this reproduces models/btms_model.joblib and guarantees the
#    pickle matches the pinned scikit-learn version.
RUN python -m app.generate_data --out data/btms_synthetic.csv \
 && python -m app.train --deploy-only

# 4. run as an unprivileged user
RUN useradd --uid 10001 --create-home btms && chown -R btms /app
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]

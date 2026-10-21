"""Gunicorn settings. WORKERS / THREADS are set per deployment (Experiments 7 and 8)."""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WORKERS", "2"))
threads = int(os.environ.get("THREADS", "4"))
worker_class = "gthread"
timeout = 30
graceful_timeout = 10
keepalive = 5
preload_app = True          # load the model once, share pages between workers (copy-on-write)
accesslog = "-" if os.environ.get("ACCESS_LOG", "0") == "1" else None
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")

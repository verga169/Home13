import os

# Render expects the service to listen on 0.0.0.0 and the provided PORT.
raw_port = (os.environ.get("PORT") or "").strip()
port = raw_port if raw_port.isdigit() and 1 <= int(raw_port) <= 65535 else "10000"
bind = f"0.0.0.0:{port}"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "2"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
preload_app = False

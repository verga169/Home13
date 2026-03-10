import os


def _safe_int(name: str, default: int, minimum: int = 1) -> int:
	raw = (os.environ.get(name) or "").strip()
	if not raw:
		return default
	try:
		value = int(raw)
	except ValueError:
		return default
	return value if value >= minimum else default


# Render expects the service to listen on 0.0.0.0 and the provided PORT.
raw_port = (os.environ.get("PORT") or "").strip()
port = raw_port if raw_port.isdigit() and 1 <= int(raw_port) <= 65535 else "10000"
bind = f"0.0.0.0:{port}"
workers = _safe_int("WEB_CONCURRENCY", 1)
threads = _safe_int("GUNICORN_THREADS", 2)
timeout = _safe_int("GUNICORN_TIMEOUT", 120)
preload_app = False

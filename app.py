
from __future__ import annotations

import csv
import math
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import psutil
from flask import Flask, jsonify, render_template, request

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
METRICS_FILE = DATA_DIR / "metrics.csv"
BLOG_METRICS_FILE = DATA_DIR / "blog_metrics.csv"

DATA_DIR.mkdir(exist_ok=True)

app = Flask(__name__)

CSV_FIELDS = [
    "timestamp",
    "latency_ms",
    "request_rate_per_min",
    "error_rate_pct",
    "cpu_percent",
    "memory_percent",
    "status_code",
    "route",
    "method",
    "client_ip",
]

BLOG_CSV_FIELDS = [
    "timestamp",
    "latency_ms",
    "status_code",
    "route",
    "method",
    "client_ip",
    "cpu_percent",
    "memory_percent",
]

CSV_LOCK = threading.Lock()

RECENT_REQUESTS = deque(maxlen=5000)
IP_REQUESTS = {}
MAX_IP_REQUESTS_PER_MINUTE = 60
METRIC_HISTORY = deque(maxlen=120)

BLOG_METRIC_HISTORY = deque(maxlen=5000)
BLOG_RECENT_REQUESTS = deque(maxlen=5000)

STARTED_AT = time.time()

INGEST_TOKEN = os.environ.get(
    "PREDICTOPS_INGEST_TOKEN",
    "local-demo-token",
)


# --------------------------------------------------
# UTILITY FUNCTIONS
# --------------------------------------------------

def utc_now():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def ensure_csv():
    with CSV_LOCK:
        for path, fields in (
            (METRICS_FILE, CSV_FIELDS),
            (BLOG_METRICS_FILE, BLOG_CSV_FIELDS),
        ):
            if not path.exists() or path.stat().st_size == 0:
                with path.open(
                    "w", newline="", encoding="utf-8"
                ) as file:
                    csv.DictWriter(
                        file, fieldnames=fields
                    ).writeheader()


def record_metric(row):
    with CSV_LOCK:
        with METRICS_FILE.open(
            "a", newline="", encoding="utf-8"
        ) as file:
            csv.DictWriter(
                file, fieldnames=CSV_FIELDS
            ).writerow(row)


def record_blog_metric(metric):
    with CSV_LOCK:
        with BLOG_METRICS_FILE.open(
            "a", newline="", encoding="utf-8"
        ) as file:
            csv.DictWriter(
                file, fieldnames=BLOG_CSV_FIELDS
            ).writerow({
                "timestamp": metric["timestamp"],
                "latency_ms": metric["latency_ms"],
                "status_code": metric["status_code"],
                "route": metric["route"],
                "method": metric["method"],
                "client_ip": metric["client_ip"],
                "cpu_percent": metric["cpu_percent"],
                "memory_percent": metric["memory_percent"],
            })


def current_request_rate(now=None):
    now = now or time.time()

    while RECENT_REQUESTS and now - RECENT_REQUESTS[0] > 60:
        RECENT_REQUESTS.popleft()

    return len(RECENT_REQUESTS)


def recent_blog_metrics():
    now = time.time()

    return [
        metric
        for metric in BLOG_METRIC_HISTORY
        if now - metric["epoch"] <= 60
    ]


def calculate_summary(metrics):
    latencies = [metric["latency_ms"] for metric in metrics]
    errors = sum(
        metric["status_code"] >= 500 for metric in metrics
    )

    return {
        "requests_last_minute": len(metrics),
        "request_rate_per_min": len(metrics),
        "error_rate_pct": round(
            errors * 100 / len(metrics), 2
        ) if metrics else 0.0,
        "avg_latency_ms": round(
            sum(latencies) / len(latencies), 2
        ) if latencies else 0.0,
        "max_latency_ms": round(
            max(latencies), 2
        ) if latencies else 0.0,
        "recent": list(metrics)[-30:],
    }


# --------------------------------------------------
# DASHBOARD REQUEST MONITORING
# --------------------------------------------------

@app.before_request
def start_timer():
    request.environ["predictops_start"] = time.perf_counter()


@app.after_request
def log_dashboard_request(response):
    # API calls are excluded from dashboard request history.
    if request.path.startswith("/api/"):
        return response

    start = request.environ.get(
        "predictops_start", time.perf_counter()
    )

    latency_ms = round(
        (time.perf_counter() - start) * 1000, 2
    )

    now = time.time()
    ip = request.remote_addr or "unknown"

    RECENT_REQUESTS.append(now)

    per_ip = IP_REQUESTS.setdefault(ip, deque())
    per_ip.append(now)

    while per_ip and now - per_ip[0] > 60:
        per_ip.popleft()

    cpu = psutil.cpu_percent(interval=None)
    memory = psutil.virtual_memory().percent
    rate = current_request_rate(now)

    METRIC_HISTORY.append({
        "epoch": now,
        "timestamp": utc_now(),
        "status_code": response.status_code,
        "latency_ms": latency_ms,
        "route": request.path,
        "method": request.method,
        "client_ip": ip,
    })

    recent = [
        metric for metric in METRIC_HISTORY
        if now - metric["epoch"] <= 60
    ]

    errors = sum(
        metric["status_code"] >= 500
        for metric in recent
    )

    row = {
        "timestamp": utc_now(),
        "latency_ms": latency_ms,
        "request_rate_per_min": rate,
        "error_rate_pct": round(
            errors * 100 / len(recent), 2
        ) if recent else 0.0,
        "cpu_percent": round(cpu, 2),
        "memory_percent": round(memory, 2),
        "status_code": response.status_code,
        "route": request.path[:200],
        "method": request.method,
        "client_ip": ip[:64],
    }

    record_metric(row)
    response.headers["X-Response-Time-ms"] = str(latency_ms)

    return response


# --------------------------------------------------
# MAIN DASHBOARD
# --------------------------------------------------

@app.get("/")
def dashboard():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "timestamp": utc_now(),
    })


# --------------------------------------------------
# BLOG METRIC INGESTION API
# --------------------------------------------------

@app.post("/api/ingest")
def ingest_blog_metrics():
    # Must match the header sent by the personal blog.
    if request.headers.get("X-Monitor-Token") != INGEST_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "Expected JSON data"}), 400

    try:
        status_code = int(data["status_code"])
        latency_ms = float(data["latency_ms"])
        cpu = float(data.get("cpu_percent", 0))
        memory = float(data.get("memory_percent", 0))

        if not 100 <= status_code <= 599:
            raise ValueError("Invalid HTTP status code")

        if not math.isfinite(latency_ms) or latency_ms < 0:
            raise ValueError("Invalid latency")

        if (
            not math.isfinite(cpu)
            or not math.isfinite(memory)
            or not 0 <= cpu <= 100
            or not 0 <= memory <= 100
        ):
            raise ValueError("Invalid resource measurements")

        metric = {
            "epoch": time.time(),
            "timestamp": utc_now(),
            "status_code": status_code,
            "latency_ms": round(latency_ms, 2),
            "route": str(data.get("route", "/"))[:200],
            "method": str(data.get("method", "GET"))[:10],
            "client_ip": str(
                data.get("client_ip") or "unknown"
            )[:64],
            "cpu_percent": round(cpu, 2),
            "memory_percent": round(memory, 2),
        }

    except (
        KeyError, TypeError, ValueError, OverflowError
    ):
        return jsonify({"error": "Invalid metric fields"}), 400

    BLOG_METRIC_HISTORY.append(metric)
    BLOG_RECENT_REQUESTS.append(metric["epoch"])
    record_blog_metric(metric)

    return jsonify({
        "status": "received",
        "blog_metrics_stored": len(BLOG_METRIC_HISTORY),
    }), 202


# --------------------------------------------------
# DASHBOARD METRICS API
# --------------------------------------------------

@app.get("/api/metrics")
def api_metrics():
    now = time.time()

    recent = [
        metric for metric in METRIC_HISTORY
        if now - metric["epoch"] <= 60
    ]

    summary = calculate_summary(recent)

    summary.update({
        "timestamp": utc_now(),
        "cpu_percent": round(
            psutil.cpu_percent(interval=0.1), 2
        ),
        "memory_percent": psutil.virtual_memory().percent,
        "uptime_seconds": int(now - STARTED_AT),
        "recent": recent[-30:],
    })

    return jsonify(summary)


# --------------------------------------------------
# PERSONAL BLOG METRICS API
# --------------------------------------------------

@app.get("/api/blog-metrics")
def api_blog_metrics():
    recent = recent_blog_metrics()
    summary = calculate_summary(recent)

    latest = recent[-1] if recent else None

    summary.update({
        "timestamp": utc_now(),
        "source": "personal_blog",
        "cpu_percent": latest["cpu_percent"] if latest else 0,
        "memory_percent": latest["memory_percent"] if latest else 0,
        "recent": recent[-30:],
        "total_metrics_received": len(BLOG_METRIC_HISTORY),
    })

    return jsonify(summary)


# --------------------------------------------------
# ALERTS API
# --------------------------------------------------

@app.get("/api/alerts")
def api_alerts():
    now = time.time()
    alerts = []

    recent = [
        metric for metric in METRIC_HISTORY
        if now - metric["epoch"] <= 60
    ]

    if recent:
        avg_latency = sum(
            metric["latency_ms"] for metric in recent
        ) / len(recent)

        errors = sum(
            metric["status_code"] >= 500
            for metric in recent
        )

        if avg_latency >= 800:
            alerts.append({
                "level": "warning",
                "message": (
                    f"High dashboard latency: "
                    f"{avg_latency:.0f} ms"
                ),
            })

        if errors:
            alerts.append({
                "level": "warning",
                "message": (
                    f"{errors} dashboard server error(s) "
                    "in the last minute"
                ),
            })

    for ip, times in list(IP_REQUESTS.items()):
        while times and now - times[0] > 60:
            times.popleft()

        if len(times) > MAX_IP_REQUESTS_PER_MINUTE:
            alerts.append({
                "level": "warning",
                "message": (
                    f"High request frequency from {ip}: "
                    f"{len(times)} requests/min "
                    "(review; not proof of malicious activity)"
                ),
            })

    cpu = psutil.cpu_percent(interval=None)

    if cpu >= 90:
        alerts.append({
            "level": "warning",
            "message": (
                f"High dashboard-machine CPU usage: {cpu:.0f}%"
            ),
        })

    blog_recent = recent_blog_metrics()

    if blog_recent:
        blog_latency = sum(
            metric["latency_ms"] for metric in blog_recent
        ) / len(blog_recent)

        blog_errors = sum(
            metric["status_code"] >= 500
            for metric in blog_recent
        )

        if blog_latency >= 800:
            alerts.append({
                "level": "warning",
                "message": (
                    f"High personal-blog average latency: "
                    f"{blog_latency:.0f} ms"
                ),
            })

        if blog_errors:
            alerts.append({
                "level": "warning",
                "message": (
                    f"{blog_errors} personal-blog server error(s) "
                    "in the last minute"
                ),
            })

    if not alerts:
        alerts.append({
            "level": "ok",
            "message": "No threshold alerts at this moment",
        })

    return jsonify({"alerts": alerts})


# --------------------------------------------------
# BLOG VISITORS API
# --------------------------------------------------

@app.get("/api/blog-visitors")
def api_blog_visitors():
    recent = recent_blog_metrics()

    return jsonify({
        "source": "personal_blog",
        "count": len(recent),
        "visitors": [
            {
                "timestamp": metric["timestamp"],
                "client_ip": metric["client_ip"],
                "route": metric["route"],
                "method": metric["method"],
                "status_code": metric["status_code"],
                "latency_ms": metric["latency_ms"],
            }
            for metric in recent[-30:]
        ],
    })


# --------------------------------------------------
# DEMO ROUTES
# --------------------------------------------------

@app.get("/demo/slow")
def demo_slow():
    time.sleep(1.2)

    return jsonify({
        "message": "Demo slow route completed",
        "purpose": "testing latency logging",
    })


@app.get("/demo/error")
def demo_error():
    return jsonify({
        "message": "Demo error response for monitoring tests",
    }), 500


# --------------------------------------------------
# START PREDICTOPS
# --------------------------------------------------

if __name__ == "__main__":
    ensure_csv()

    print("PredictOps AI running at http://127.0.0.1:5000")
    print("Dashboard metrics: http://127.0.0.1:5000/api/metrics")
    print("Blog metrics: http://127.0.0.1:5000/api/blog-metrics")
    print("Blog visitors: http://127.0.0.1:5000/api/blog-visitors")
    print("Dashboard CSV:", METRICS_FILE)
    print("Blog CSV:", BLOG_METRICS_FILE)

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        threaded=True,
    )

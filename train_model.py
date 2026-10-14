"""
Train a small demonstration model from collected metrics.

The label for each 10-second window is 1 if the NEXT window has average
latency >= 800 ms OR server-error rate >= 10%; otherwise 0.
Features come only from the current window. A chronological split is used.
This is a learning prototype; evaluate with more data before drawing conclusions.
"""
from pathlib import Path
import json

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.dummy import DummyClassifier

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "data" / "metrics.csv"
MODEL_DIR = BASE_DIR / "models"
MODEL_PATH = MODEL_DIR / "degradation_model.joblib"
REPORT_PATH = MODEL_DIR / "evaluation_report.json"

FEATURES = [
    "avg_latency_ms", "request_rate_per_min", "error_rate_pct",
    "cpu_percent", "memory_percent", "request_count"
]
WINDOW = "10s"
LATENCY_THRESHOLD_MS = 800
ERROR_RATE_THRESHOLD_PCT = 10


def main():
    if not CSV_PATH.exists():
        raise SystemExit("No data/metrics.csv yet. Run app.py and generate some requests first.")

    df = pd.read_csv(CSV_PATH)
    required = {"timestamp", "latency_ms", "request_rate_per_min",
                "error_rate_pct", "cpu_percent", "memory_percent", "status_code"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"CSV is missing columns: {sorted(missing)}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    for col in ["latency_ms", "request_rate_per_min", "error_rate_pct",
                "cpu_percent", "memory_percent", "status_code"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["timestamp", "latency_ms", "status_code"]).sort_values("timestamp")
    if len(df) < 30:
        raise SystemExit(f"Only {len(df)} requests recorded. Collect more varied traffic (at least 30 requests; more is better).")

    df = df.set_index("timestamp")
    windows = df.resample(WINDOW).agg(
        avg_latency_ms=("latency_ms", "mean"),
        request_rate_per_min=("request_rate_per_min", "mean"),
        error_rate_pct=("error_rate_pct", "mean"),
        cpu_percent=("cpu_percent", "mean"),
        memory_percent=("memory_percent", "mean"),
        request_count=("status_code", "count"),
        server_error_count=("status_code", lambda x: (x >= 500).sum()),
    ).dropna(subset=["avg_latency_ms"])

    if len(windows) < 12:
        raise SystemExit("Not enough time windows yet. Keep the app running and generate requests across a few minutes.")

    # Target is whether the next window is degraded; shift -1 avoids using future features.
    future_error_rate = 100 * windows["server_error_count"].shift(-1) / windows["request_count"].shift(-1)
    windows["target_degraded_next_window"] = (
        (windows["avg_latency_ms"].shift(-1) >= LATENCY_THRESHOLD_MS) |
        (future_error_rate >= ERROR_RATE_THRESHOLD_PCT)
    ).astype("float")
    windows = windows.iloc[:-1].copy()
    windows["target_degraded_next_window"] = windows["target_degraded_next_window"].astype(int)
    windows = windows.dropna(subset=FEATURES)

    if len(windows) < 12:
        raise SystemExit("Not enough usable windows after cleaning. Collect more data.")

    # Chronological holdout: earlier windows train; later windows test.
    split = max(8, int(len(windows) * 0.8))
    if split >= len(windows):
        split = len(windows) - 3
    X_train, X_test = windows[FEATURES].iloc[:split], windows[FEATURES].iloc[split:]
    y_train = windows["target_degraded_next_window"].iloc[:split]
    y_test = windows["target_degraded_next_window"].iloc[split:]

    if y_train.nunique() < 2:
        raise SystemExit(
            "Training labels contain only one class. Collect a more varied dataset, including normal activity "
            "and some slow/error test requests, then try again."
        )

    model = RandomForestClassifier(n_estimators=150, max_depth=5, random_state=42,
                                   class_weight="balanced")
    model.fit(X_train, y_train)
    predictions = model.predict(X_test)
    report = {
        "rows": int(len(windows)),
        "train_rows": int(len(X_train)),
        "test_rows": int(len(X_test)),
        "target_definition": "Next 10-second window average latency >= 800 ms OR server-error rate >= 10%",
        "accuracy": float(accuracy_score(y_test, predictions)),
        "confusion_matrix": confusion_matrix(y_test, predictions, labels=[0, 1]).tolist(),
        "classification_report": classification_report(y_test, predictions, labels=[0, 1],
                                                       target_names=["normal", "degraded"],
                                                       output_dict=True, zero_division=0),
        "note": "Small demonstration dataset; test results may be unstable and are not a production guarantee."
    }
    MODEL_DIR.mkdir(exist_ok=True)
    joblib.dump({"model": model, "features": FEATURES}, MODEL_PATH)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Model saved: {MODEL_PATH}")
    print(f"Evaluation report saved: {REPORT_PATH}")
    print(f"Chronological holdout accuracy: {report['accuracy']:.3f}")
    print(f"Confusion matrix (actual rows, predicted columns; [normal, degraded]): {report['confusion_matrix']}")
    print("Review the report and collect more data if the test set is too small or lacks both classes.")


if __name__ == "__main__":
    main()

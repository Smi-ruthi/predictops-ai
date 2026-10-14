# PredictOps AI — Local Project Starter

This project monitors a Flask web application and prepares performance metrics for a future machine-learning model.

## Features in this starter
- Flask web application with sample routes
- Request latency, request count, HTTP status, CPU and memory measurements
- CSV logging in `data/metrics.csv`
- Simple dashboard with auto-refresh
- Basic per-IP request-frequency flags (local/demo use only)
- ML training script that trains only after enough collected data exists

## Requirements
- Python 3.9+ recommended
- VS Code (optional)

## Setup (Windows PowerShell)
1. Open this folder in VS Code.
2. Create and activate a virtual environment:
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   python -m pip install -r requirements.txt
   ```
4. Start the app:
   ```powershell
   python app.py
   ```
5. Open `http://127.0.0.1:5000` in your browser.
6. Visit `/demo/slow` a few times to create slower requests and `/demo/error` to log an error response. These are local test routes, not real failures or attacks.

## Collecting data
- The dashboard polls `/api/metrics` and `/api/alerts`.
- Every request is logged to `data/metrics.csv`.
- For the initial dataset, the ML training script aggregates measurements into 10-second windows and labels a window as degraded when a later window meets the configured threshold.
- Collect data while the app is running, then stop the app and run:
  ```powershell
  python train_model.py
  ```
- Training requires at least 30 usable time windows with both target classes represented. If it does not train, collect more varied data first.

## Important limitations
- This is a student prototype, not a production monitoring/security platform.
- The prediction is a demonstration model and should only be trusted after evaluating it on later time periods.
- CPU/memory values are machine-wide snapshots, not isolated process/container metrics.
- `request_rate` is requests per minute based on recent server logs.
- IP address is only used for a simple request-frequency counter. An IP alone does not establish malicious intent.
- Debug mode is disabled by default. Do not expose this development server to the public internet.

## Project structure
```text
AI_DevOps_Predictive_Maintenance/
├── app.py
├── train_model.py
├── requirements.txt
├── README.md
├── templates/
│   └── index.html
├── static/
│   ├── style.css
│   └── dashboard.js
├── data/
│   └── metrics.csv   (created automatically)
└── models/
    └── (trained model saved here)
```

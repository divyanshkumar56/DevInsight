"""
DevInsight - Flask backend.

Serves the dashboard frontend and exposes the defect-prediction API:
    POST /api/analyze-zip   (multipart field "file": .zip project or single .py file)
    POST /api/analyze-code  (JSON {"code": "...", "filename": "optional.py"} or form field "code")
    GET  /api/health        (model status)
    GET  /api/model-info    (training results & feature metadata)
"""

from __future__ import annotations

import io
import os
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from extractor import (
    DEFECT_THRESHOLD, MEDIUM_THRESHOLD, METRIC_INFO, RADON_15,
    analyze_files, load_model,
)

ROOT_DIR = BASE_DIR.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
MODEL_PATH = Path(os.environ.get("DEVINSIGHT_MODEL", ROOT_DIR / "model.pkl"))
SAMPLE_ZIP = ROOT_DIR / "sample_project.zip"

IGNORED_DIRS = {
    "venv", ".venv", "env", ".env", "__pycache__", ".git", "node_modules",
    "site-packages", ".tox", ".mypy_cache", ".pytest_cache", ".idea", ".vscode", "__MACOSX",
}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024        # 50 MB upload limit
MAX_FILE_BYTES = 2 * 1024 * 1024           # skip single .py files larger than 2 MB
MAX_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024 # zip-bomb guard
MAX_PY_FILES = 3000
MAX_CODE_CHARS = 500_000

MODEL_INFO = {
    "name": "Random Forest Classifier (Regularized)",
    "library": "scikit-learn (joblib)",
    "dataset": "NASA JM1 (PROMISE repository, leak-free split)",
    "records": 7717,
    "features": RADON_15,
    "feature_descriptions": METRIC_INFO,
    "threshold": DEFECT_THRESHOLD,
    "medium_threshold": MEDIUM_THRESHOLD,
    "results": [
        {"model": "Random Forest (selected)", "cv_accuracy": 79.65, "cv_roc_auc": 0.7086, "cv_f1": 0.221, "test_accuracy_050": 79.79, "test_accuracy_026": 74.35, "test_roc_auc": 0.7038, "test_defect_f1": 0.45, "selected": True},
        {"model": "Extra Trees", "cv_accuracy": 79.59, "cv_roc_auc": 0.7100, "cv_f1": 0.094, "selected": False},
        {"model": "Logistic Regression", "cv_accuracy": 79.51, "cv_roc_auc": 0.6955, "cv_f1": 0.177, "selected": False},
        {"model": "Decision Tree", "cv_accuracy": 77.26, "cv_roc_auc": 0.6566, "cv_f1": 0.239, "selected": False},
    ],
}

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.json.sort_keys = False  # keep RADON_15 metric order in responses

print(f"[DevInsight] Loading model from {MODEL_PATH} ...")
_t0 = time.time()
MODEL = load_model(MODEL_PATH)
print(f"[DevInsight] Model loaded: {type(MODEL).__name__} "
      f"({getattr(MODEL, 'n_estimators', '?')} trees) in {time.time() - _t0:.1f}s")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _is_ignored(path: str) -> bool:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    return any(p in IGNORED_DIRS for p in parts[:-1]) or parts[-1].startswith("._")


def _collect_from_zip(data: bytes) -> tuple[list[tuple[str, str]], dict]:
    """Read .py files from a zip archive in memory (never extracted to disk)."""
    files: list[tuple[str, str]] = []
    stats = {"ignored": 0, "skipped_large": 0, "non_python": 0}
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if _is_ignored(name):
                if name.endswith(".py"):
                    stats["ignored"] += 1
                continue
            if not name.lower().endswith(".py"):
                stats["non_python"] += 1
                continue
            if info.file_size > MAX_FILE_BYTES:
                stats["skipped_large"] += 1
                continue
            total += info.file_size
            if total > MAX_TOTAL_UNCOMPRESSED:
                raise ValueError("Archive is too large to analyze (uncompressed size limit exceeded).")
            files.append((name, _decode(zf.read(info))))
            if len(files) >= MAX_PY_FILES:
                break
    files.sort(key=lambda x: x[0])
    return files, stats


def _run_analysis(files: list[tuple[str, str]], source_name: str, stats: dict | None = None):
    t0 = time.time()
    report = analyze_files(files, MODEL)
    report["meta"] = {
        "source": source_name,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "ignored_files": (stats or {}).get("ignored", 0),
        "skipped_large_files": (stats or {}).get("skipped_large", 0),
        "non_python_files": (stats or {}).get("non_python", 0),
        "ignored_dirs": sorted(IGNORED_DIRS),
    }
    return jsonify(report)


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/<path:asset>")
def frontend_asset(asset: str):
    if asset in ("style.css", "script.js", "favicon.svg"):
        return send_from_directory(FRONTEND_DIR, asset)
    return _error("Not found", 404)


@app.get("/sample_project.zip")
def sample_zip():
    if not SAMPLE_ZIP.exists():
        return _error("sample_project.zip not found", 404)
    return send_from_directory(ROOT_DIR, "sample_project.zip", as_attachment=True)


@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "model": type(MODEL).__name__,
        "n_estimators": getattr(MODEL, "n_estimators", None),
        "n_features": getattr(MODEL, "n_features_in_", None),
        "threshold": DEFECT_THRESHOLD,
    })


@app.get("/api/model-info")
def model_info():
    return jsonify(MODEL_INFO)


@app.post("/api/analyze-zip")
def analyze_zip():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _error("No file uploaded. Send a .zip or .py file in the 'file' form field.")

    filename = upload.filename
    data = upload.read()
    if not data:
        return _error("The uploaded file is empty.")

    lower = filename.lower()
    if lower.endswith(".py"):
        return _run_analysis([(Path(filename).name, _decode(data))], filename)

    if lower.endswith(".zip") or zipfile.is_zipfile(io.BytesIO(data)):
        try:
            files, stats = _collect_from_zip(data)
        except zipfile.BadZipFile:
            return _error("The uploaded file is not a valid .zip archive.")
        except ValueError as exc:
            return _error(str(exc), 413)
        if not files:
            return _error("No Python (.py) files found in the archive "
                          f"(ignored folders: {', '.join(sorted(IGNORED_DIRS))}).")
        return _run_analysis(files, filename, stats)

    return _error("Unsupported file type. Please upload a .zip project or a .py file.")


@app.post("/api/analyze-code")
def analyze_code_route():
    payload = request.get_json(silent=True) or {}
    code = payload.get("code") if payload else None
    if code is None:
        code = request.form.get("code")
    filename = (payload.get("filename") if payload else None) or request.form.get("filename") or "pasted_code.py"

    if not isinstance(code, str) or not code.strip():
        return _error("Please provide some Python code to analyze.")
    if len(code) > MAX_CODE_CHARS:
        return _error(f"Code is too long (max {MAX_CODE_CHARS:,} characters).", 413)
    return _run_analysis([(filename, code)], filename)


@app.errorhandler(413)
def too_large(_):
    return _error(f"Upload too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).", 413)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"[DevInsight] Dashboard running at http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)


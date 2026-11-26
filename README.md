# DevInsight — Software Defect Prediction for Python

DevInsight scans Python projects **function by function**, extracts the 15 calibrated Radon + AST software
metrics aligned with the NASA JM1 defect dataset, and predicts each function's **Defect Risk %** with a
leak-free `RandomForestClassifier` (`model.pkl`). For risky code, it explains **why it was flagged and how
to fix it**.

![stack](https://img.shields.io/badge/Flask-3.x-blue) ![model](https://img.shields.io/badge/RandomForest-79.79%25%20acc%20%400.50-green) ![auc](https://img.shields.io/badge/ROC--AUC-0.7038-green)

---

## Features

- **Upload a `.zip` project or a single `.py` file**, or **paste Python code** to test it live.
- Automatically ignores `venv`, `.venv`, `__pycache__`, `.git`, `node_modules`, `site-packages`, etc.
- Analyzes every function and class method (`Class.method`); files with no functions are scored as one
  module-level unit.
- **Project Health Score**, total files & functions scanned, and High / Medium / Safe risk distribution.
- Expandable file & function cards showing line numbers (`Lines 10–80`), risk badge, Defect Risk %,
  key complexity metrics (`LOC`, `Cyclomatic Complexity v(g)`, `Halstead Difficulty`, `Halstead Volume`,
  `Comments`), line-highlighted source preview, and **"Why It Was Flagged & How to Fix It"**.
- Root-cause diagnostics:
  | Rule | Trigger | Advice |
  |---|---|---|
  | High Branching Complexity | `v(g) > 10` | Reduce nested `if/for/while` branches below 10 (guard clauses, dispatch tables, helpers) |
  | Function Too Long | `loc > 50` | Split into smaller helper functions |
  | High Halstead Difficulty | `d > 15` | Dense operators/operands; simplify expressions with named intermediates |
  | Low Comment Coverage | `loc > 25` and `lOComment == 0` | Add a docstring and comments explaining intent |
  | Deep Nesting (AST) | nesting depth `> 4` | Flatten with early returns / extract inner blocks |
  | Bare `except:` (AST) | any | Catch specific exceptions |
  | Mutable default args (AST) | `def f(x=[])`, `{}`, `set()`... | Use `None` and initialize inside the function |
  | Too Many Parameters (AST) | `> 6` (excl. `self`/`cls`) | Group related parameters into a dataclass/config object |
  | Risky Halstead Profile | model says High, no single rule fired | Add unit tests and simplify operator density |
- Export the complete report (including all 15 raw metrics per function) as JSON.

## Model & Evaluation (Leak-Free)

- **Algorithm:** Regularized `RandomForestClassifier` (`n_estimators=500`, `max_depth=12`, `min_samples_leaf=5`, `max_features="sqrt"`).
- **Dataset:** **NASA JM1** (PROMISE repository), integrity-cleaned from 10,885 raw rows to **7,717 unique modules** (header rows, `?` nulls, zero-LOC/token rows, Halstead length mismatches, exact duplicates, and contradictory twins removed).
- **5-Fold Cross-Validation on `X_train` Only (6,173 modules, default `0.50` cutoff):**
  - **Random Forest (selected):** `79.65%` Accuracy, `0.7086` ROC-AUC, `0.221` Defect F1
  - **Extra Trees:** `79.59%` Accuracy, `0.7100` ROC-AUC, `0.094` Defect F1
  - **Logistic Regression:** `79.51%` Accuracy, `0.6955` ROC-AUC, `0.177` Defect F1
  - **Decision Tree:** `77.26%` Accuracy, `0.6566` ROC-AUC, `0.239` Defect F1
- **Untouched Holdout Test Set (1,544 modules; threshold frozen via 5-fold CV on `X_train` only):**
  - **ROC-AUC:** `0.7038`
  - **Accuracy (@ standard `0.50`):** `79.79%`
  - **Deployed CV-tuned threshold (`0.26`):** `74.35%` Accuracy, **50.2% Defect Recall (`162/323`)**, `0.45` Defect F1 (`TN=986, FP=235, FN=161, TP=162`)
- **Risk Bands:**
  - `P(defect) >= 0.26` -> **High Risk** (predicted defective)
  - `0.20 <= P(defect) < 0.26` -> **Medium Risk** (UI watch list)
  - `P(defect) < 0.20` -> **Safe**
- **Exact Feature Order (`RADON_15`):**
  `['loc', 'v(g)', 'v', 'd', 'e', 'lOCode', 'lOComment', 'lOBlank', 'n', 'uniq_Op', 'uniq_Opnd', 'total_Op', 'total_Opnd', 'b', 't']`

### Calibrated Radon + AST Metric Extraction

| Feature | Source |
|---|---|
| `loc` | `radon.raw.analyze(...).loc` |
| `v(g)` | `radon.complexity.cc_visit` complexity (nested closures folded in) |
| `lOCode` | `max(min(raw.lloc, raw.loc - lOComment - lOBlank), 1)` |
| `lOComment` | `raw.comments + raw.multi` |
| `lOBlank` | `raw.blank` |
| `uniq_Op`, `total_Op` | Radon expression operators (`h1`, `N1`) + AST statement/call operators (`If`, `For`, `While`, `Try`, `Return`, `Assign`, `Call`, `Attribute`, `Subscript`, etc.) |
| `uniq_Opnd`, `total_Opnd` | Radon Halstead operands (`h2`, `N2`) |
| `n`, `v`, `d`, `e`, `b`, `t` | Recomputed from calibrated operator/operand counts via standard Halstead formulas |

## Project Structure

```
DevInsightFinal/
├── model.pkl                  # leak-free RandomForestClassifier (joblib, compressed)
├── backend/
│   ├── app.py                 # Flask server + REST API, serves the frontend
│   └── extractor.py           # AST + calibrated Radon extraction, prediction, diagnostics
├── frontend/
│   ├── index.html             # clean dark dashboard
│   ├── style.css              # styling
│   ├── script.js              # upload / paste / rendering logic
│   └── favicon.svg
├── sample_project/            # source files of the test sample (clean + spaghetti code)
├── sample_project.zip         # ready-to-upload test archive
├── scripts/build_sample_zip.py
├── requirements.txt
└── README.md
```

## Getting Started

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the server
python backend/app.py
```

Open **http://127.0.0.1:5000**, then click **"Try sample_project.zip"** or upload your own `.zip` / `.py` file.

Environment variables: `PORT` (default `5000`), `DEVINSIGHT_MODEL` (custom path to `model.pkl`).

## API

- `POST /api/analyze-zip` — Multipart form field `file` (`.zip` project or `.py` file).
- `POST /api/analyze-code` — JSON `{"code": "...", "filename": "optional.py"}`.
- `GET /api/health` — Model & server status.
- `GET /api/model-info` — Model metadata & feature list.
- `GET /sample_project.zip` — Download the sample test project.

## CLI

```bash
python backend/extractor.py sample_project/order_processor.py
```

## Limitations & Future Work

- **Cross-language domain shift:** NASA JM1 consists of C functions while DevInsight analyzes Python; probabilities should be interpreted as relative risk rankings alongside the AST/Radon diagnostics.
- **Static metrics only:** Structural metrics cannot detect semantic/logic bugs in short functions (e.g., off-by-one errors or wrong business formulas).
- **Future Work:** Incorporate Git process metrics (code churn, contributor history) and fine-tune transformer models (e.g., CodeBERT) on native Python bug datasets such as **PyTraceBugs** or **BugsInPy**.

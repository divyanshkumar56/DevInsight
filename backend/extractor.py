"""
DevInsight - Radon metric extractor, defect predictor and diagnostics engine.

Analyzes Python source code function-by-function (or the module as a whole when
it contains no functions), extracts the 15 Radon metrics expected by the trained
RandomForestClassifier (NASA JM1 feature space), predicts the defect risk and
produces root-cause diagnostics with concrete refactoring advice.
"""

from __future__ import annotations

import ast
import math
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from radon.complexity import cc_visit
from radon.metrics import h_visit
from radon.raw import analyze
from radon.visitors import ComplexityVisitor

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Exact feature order expected by model.pkl -- DO NOT REORDER.
RADON_15 = [
    "loc", "v(g)", "v", "d", "e", "lOCode", "lOComment", "lOBlank",
    "n", "uniq_Op", "uniq_Opnd", "total_Op", "total_Opnd", "b", "t",
]

METRIC_INFO = {
    "loc":        "Total lines of code (incl. comments & blanks)",
    "v(g)":       "McCabe cyclomatic complexity",
    "v":          "Halstead volume",
    "d":          "Halstead difficulty",
    "e":          "Halstead effort",
    "lOCode":     "Source lines of code",
    "lOComment":  "Comment / docstring lines",
    "lOBlank":    "Blank lines",
    "n":          "Halstead length (N1 + N2)",
    "uniq_Op":    "Unique operators (n1)",
    "uniq_Opnd":  "Unique operands (n2)",
    "total_Op":   "Total operators (N1)",
    "total_Opnd": "Total operands (N2)",
    "b":          "Halstead estimated bugs",
    "t":          "Halstead time to program (s)",
}

DEFECT_THRESHOLD = 0.26   # predict_proba >= 0.26  -> High Risk / Defective (5-fold CV tuned)
MEDIUM_THRESHOLD = 0.20   # 0.20 <= p < 0.26       -> Medium (watch list)

# Diagnostic thresholds
CC_LIMIT = 10
LOC_LIMIT = 50
DIFFICULTY_LIMIT = 15
COMMENT_LOC_LIMIT = 25
NESTING_LIMIT = 4
ARGS_LIMIT = 6

MAX_SOURCE_LINES_RETURNED = 400

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent.parent / "model.pkl"


# --------------------------------------------------------------------------- #
# Model loading / prediction
# --------------------------------------------------------------------------- #

def load_model(path: str | os.PathLike | None = None):
    """Load the trained RandomForestClassifier from disk with joblib."""
    import joblib

    model_path = Path(path or os.environ.get("DEVINSIGHT_MODEL", DEFAULT_MODEL_PATH))
    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")
    with warnings.catch_warnings():
        # model.pkl was trained with scikit-learn 1.6.x; silence version-mismatch noise.
        warnings.simplefilter("ignore")
        model = joblib.load(model_path)
    n_features = getattr(model, "n_features_in_", len(RADON_15))
    if n_features != len(RADON_15):
        raise ValueError(f"Model expects {n_features} features, extractor provides {len(RADON_15)}")
    return model


def risk_level(prob: float) -> str:
    if prob >= DEFECT_THRESHOLD:
        return "High"
    if prob >= MEDIUM_THRESHOLD:
        return "Medium"
    return "Safe"


def predict_probabilities(model, metric_rows: list[dict[str, float]]) -> list[float]:
    """Batch-predict P(defective) for a list of metric dicts (RADON_15 order enforced)."""
    if not metric_rows:
        return []
    X = np.array([[float(row[k]) for k in RADON_15] for row in metric_rows], dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        proba = model.predict_proba(X)
    classes = list(getattr(model, "classes_", [0, 1]))
    pos_idx = classes.index(1) if 1 in classes else 1
    return [float(p[pos_idx]) for p in proba]


# --------------------------------------------------------------------------- #
# Metric extraction
# --------------------------------------------------------------------------- #

@dataclass
class CodeUnit:
    """A function, method or module-level block that gets scored by the model."""
    name: str
    qualname: str
    kind: str                      # function | method | async function | async method | module
    start_line: int
    end_line: int
    source: str
    node: ast.AST
    is_method: bool = False
    metrics: dict[str, float] = field(default_factory=dict)
    smells: dict[str, Any] = field(default_factory=dict)


def _strip_common_indent(lines: list[str]) -> str:
    """Remove the indentation of the first line from every line that carries it.

    Unlike textwrap.dedent this tolerates multi-line string contents that sit at
    column 0 inside an indented method (those are inside string tokens, so the
    parser does not care about their indentation).
    """
    if not lines:
        return ""
    first = lines[0]
    indent = first[: len(first) - len(first.lstrip())]
    if not indent:
        return "".join(lines)
    out = [ln[len(indent):] if ln.startswith(indent) else ln for ln in lines]
    return "".join(out)


_AST_OP_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.ExceptHandler,
    ast.Return, ast.Assign, ast.AugAssign, ast.AnnAssign,
    ast.Call, ast.Attribute, ast.Subscript,
)


def compute_radon_metrics(
    source: str,
    module_level: bool = False,
    node: ast.AST | None = None,
) -> dict[str, float]:
    """Compute the 15 JM1-style metrics for a self-contained chunk of Python source."""
    raw = analyze(source)

    # --- McCabe cyclomatic complexity v(g) ---
    if module_level:
        visitor = ComplexityVisitor.from_code(source)
        vg = visitor.total_complexity
    else:
        blocks = cc_visit(source)
        if blocks:
            main = max(blocks, key=lambda b: b.complexity)
            # Fold nested closures into the enclosing function's complexity.
            vg = main.complexity + sum(max(c.complexity - 1, 0) for c in getattr(main, "closures", []))
        else:
            vg = 1

    # --- Calibrated Halstead metrics (Radon expression ops + AST statement/call ops) ---
    hal = h_visit(source).total
    tree = node if node is not None else ast.parse(source)
    ast_op_types = {type(n) for n in ast.walk(tree) if isinstance(n, _AST_OP_NODES)}
    ast_op_count = sum(1 for n in ast.walk(tree) if isinstance(n, _AST_OP_NODES))

    uniq_Op = hal.h1 + len(ast_op_types)
    total_Op = hal.N1 + ast_op_count
    uniq_Opnd = hal.h2
    total_Opnd = hal.N2

    n = total_Op + total_Opnd
    vocab = uniq_Op + uniq_Opnd
    v = n * math.log2(vocab) if (n > 0 and vocab > 1) else 0.0
    d = (uniq_Op / 2.0) * (total_Opnd / max(uniq_Opnd, 1))
    e = v * d
    b = v / 3000.0
    t = e / 18.0

    lOComment = raw.comments + raw.multi
    lOBlank = raw.blank
    lOCode = max(min(raw.lloc, raw.loc - lOComment - lOBlank), 1) if raw.loc > 0 else 0

    metrics = {
        "loc": raw.loc,
        "v(g)": vg,
        "v": v,
        "d": d,
        "e": e,
        "lOCode": lOCode,
        "lOComment": lOComment,
        "lOBlank": lOBlank,
        "n": n,
        "uniq_Op": uniq_Op,
        "uniq_Opnd": uniq_Opnd,
        "total_Op": total_Op,
        "total_Opnd": total_Opnd,
        "b": b,
        "t": t,
    }
    clean = {}
    for k in RADON_15:
        val = float(metrics[k] or 0.0)
        if not np.isfinite(val):
            val = 0.0
        clean[k] = round(val, 4)
    return clean


def _collect_functions(tree: ast.Module) -> list[tuple[ast.AST, str, bool]]:
    """Return (node, qualname, is_method) for top-level functions and class methods.

    Nested (inner) functions are analyzed as part of their enclosing function.
    """
    found: list[tuple[ast.AST, str, bool]] = []

    def visit(body: Iterable[ast.stmt], prefix: str, in_class: bool):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((node, f"{prefix}{node.name}", in_class))
            elif isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.", True)
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith)) and not prefix:
                # e.g. functions defined under `if TYPE_CHECKING:` / `try:` at module level
                for attr in ("body", "orelse", "finalbody"):
                    visit(getattr(node, attr, []) or [], prefix, in_class)
                for h in getattr(node, "handlers", []) or []:
                    visit(h.body, prefix, in_class)

    visit(tree.body, "", False)
    return found


def extract_units(code: str) -> list[CodeUnit]:
    """Parse code and return scored-ready CodeUnits (functions, or module if none)."""
    tree = ast.parse(code)
    lines = code.splitlines(keepends=True)
    units: list[CodeUnit] = []

    for node, qualname, is_method in _collect_functions(tree):
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        end = getattr(node, "end_lineno", None) or node.lineno
        seg = _strip_common_indent(lines[start - 1:end])
        is_async = isinstance(node, ast.AsyncFunctionDef)
        kind = ("async " if is_async else "") + ("method" if is_method else "function")
        unit = CodeUnit(
            name=node.name, qualname=qualname, kind=kind,
            start_line=start, end_line=end, source=seg, node=node, is_method=is_method,
        )
        try:
            unit.metrics = compute_radon_metrics(seg, node=node)
        except SyntaxError:
            # Extremely unusual layouts: fall back to the AST-unparsed function.
            unit.metrics = compute_radon_metrics(ast.unparse(node), node=node)
        units.append(unit)

    if not units:
        total_lines = max(len(lines), 1)
        unit = CodeUnit(
            name="<module>", qualname="<module>", kind="module",
            start_line=1, end_line=total_lines, source=code, node=tree,
        )
        unit.metrics = compute_radon_metrics(code, module_level=True, node=tree)
        units.append(unit)

    for u in units:
        u.smells = detect_smells(u.node, u.is_method)
    return units


# --------------------------------------------------------------------------- #
# AST smell detection
# --------------------------------------------------------------------------- #

_NESTING_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try,
) + ((ast.Match,) if hasattr(ast, "Match") else ()) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())

_MUTABLE_LITERALS = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)
_MUTABLE_CALLS = {"list", "dict", "set", "defaultdict", "OrderedDict", "deque", "bytearray"}


def _max_nesting(node: ast.AST, depth: int = 0) -> tuple[int, int | None]:
    """Return (max control-flow nesting depth, line where it occurs).

    `depth` is the nesting level of `node` itself; nested control-flow children
    are one level deeper, except `elif` branches which stay on the same level.
    """
    best, best_line = depth, getattr(node, "lineno", None)

    def consider(child: ast.AST, d: int):
        nonlocal best, best_line
        cd, cl = _max_nesting(child, d)
        if cd > best:
            best, best_line = cd, cl

    elif_node = None
    if isinstance(node, ast.If) and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
        elif_node = node.orelse[0]

    for child in ast.iter_child_nodes(node):
        if child is elif_node:
            consider(child, depth)            # `elif` stays on the same level
        elif isinstance(child, _NESTING_NODES):
            consider(child, depth + 1)
        else:
            consider(child, depth)
    return best, best_line


def detect_smells(node: ast.AST, is_method: bool = False) -> dict[str, Any]:
    """Detect common Python code smells inside a function (or module)."""
    smells: dict[str, Any] = {}

    # Deep nesting (control-flow blocks inside the function / module body)
    depth, line = 0, None
    for stmt in node.body:  # type: ignore[attr-defined]
        d, l = _max_nesting(stmt, 1 if isinstance(stmt, _NESTING_NODES) else 0)
        if d > depth:
            depth, line = d, l
    smells["max_nesting"] = depth
    smells["max_nesting_line"] = line

    # Bare except
    smells["bare_except_lines"] = sorted(
        n.lineno for n in ast.walk(node) if isinstance(n, ast.ExceptHandler) and n.type is None
    )

    # Function-signature smells
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        params = [p.arg for p in (a.posonlyargs + a.args + a.kwonlyargs)]
        if is_method and params and params[0] in ("self", "cls"):
            params = params[1:]
        smells["arg_count"] = len(params)

        mutable = []
        defaults = list(a.defaults) + [d for d in a.kw_defaults if d is not None]
        positional = a.posonlyargs + a.args
        pos_with_defaults = positional[len(positional) - len(a.defaults):] if a.defaults else []
        kw_with_defaults = [k for k, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None]
        for arg, default in zip(pos_with_defaults + kw_with_defaults, defaults):
            is_mut = isinstance(default, _MUTABLE_LITERALS) or (
                isinstance(default, ast.Call)
                and isinstance(default.func, ast.Name)
                and default.func.id in _MUTABLE_CALLS
            )
            if is_mut:
                mutable.append({"arg": arg.arg, "default": ast.unparse(default), "line": default.lineno})
        smells["mutable_defaults"] = mutable
    else:
        smells["arg_count"] = 0
        smells["mutable_defaults"] = []
    return smells


# --------------------------------------------------------------------------- #
# Diagnostics & refactoring advice
# --------------------------------------------------------------------------- #

def build_diagnostics(unit: CodeUnit, prob: float) -> list[dict[str, Any]]:
    m, s = unit.metrics, unit.smells
    label = "this module" if unit.kind == "module" else f"`{unit.qualname}`"
    diags: list[dict[str, Any]] = []

    def add(code, title, severity, metric, value, threshold, why, fix):
        diags.append({
            "code": code, "title": title, "severity": severity, "metric": metric,
            "value": value, "threshold": threshold, "why": why, "fix": fix,
        })

    vg = m["v(g)"]
    if vg > CC_LIMIT:
        add("HIGH_COMPLEXITY", "High Branching Complexity", "high" if vg > 2 * CC_LIMIT else "medium",
            "v(g)", int(vg), f"<= {CC_LIMIT}",
            f"{label} has a cyclomatic complexity of {int(vg)}, meaning there are {int(vg)} independent "
            f"execution paths to test. Branch-heavy code is the strongest single predictor of defects "
            f"in the NASA JM1 data.",
            f"Reduce nested if/for/while branches to bring v(g) below {CC_LIMIT}: use guard clauses / early "
            f"returns, replace long if/elif chains with a dict dispatch or polymorphism, and extract "
            f"each loop body or branch group into a well-named helper function.")

    loc = m["loc"]
    if loc > LOC_LIMIT:
        add("TOO_LONG", "Function Too Long", "high" if loc > 2 * LOC_LIMIT else "medium",
            "loc", int(loc), f"<= {LOC_LIMIT}",
            f"{label} spans {int(loc)} lines. Long units mix several responsibilities, are hard to "
            f"review, and hide defects between unrelated steps.",
            "Split it into smaller helper functions that each do one thing (e.g. validate -> transform "
            "-> persist). Aim for units under ~30-50 lines that fit on one screen.")

    d = m["d"]
    if d > DIFFICULTY_LIMIT:
        add("HIGH_DIFFICULTY", "High Halstead Difficulty", "high" if d > 2 * DIFFICULTY_LIMIT else "medium",
            "d", round(d, 2), f"<= {DIFFICULTY_LIMIT}",
            f"Halstead difficulty is {d:.1f}: the code uses a dense mix of operators "
            f"({int(m['uniq_Op'])} unique / {int(m['total_Op'])} total) reusing the same operands many "
            f"times, which makes expressions error-prone to read and modify.",
            "Simplify expressions: break long arithmetic/boolean expressions into named intermediate "
            "variables, replace magic numbers with constants, and move repeated calculations into helpers.")

    if loc > COMMENT_LOC_LIMIT and m["lOComment"] == 0:
        add("NO_COMMENTS", "Low Comment Coverage", "low",
            "lOComment", 0, "> 0 when loc > 25",
            f"{label} is {int(loc)} lines long with no comments or docstring, so intent, edge cases and "
            f"assumptions are undocumented.",
            "Add a docstring describing purpose, parameters, return value and raised exceptions, and "
            "comment the non-obvious business rules (the 'why', not the 'what').")

    depth = s.get("max_nesting", 0)
    if depth > NESTING_LIMIT:
        line = s.get("max_nesting_line")
        add("DEEP_NESTING", "Deeply Nested Code", "high" if depth > NESTING_LIMIT + 2 else "medium",
            "nesting", depth, f"<= {NESTING_LIMIT}",
            f"Control flow is nested {depth} levels deep" + (f" (around line {line})" if line else "") +
            ". Deep nesting forces readers to track many conditions at once and is a classic defect hotspot.",
            "Flatten the structure with guard clauses (`if not ok: return/continue`), invert conditions, "
            "combine related conditions, and extract the innermost blocks into helper functions.")

    bare = s.get("bare_except_lines", [])
    if bare:
        add("BARE_EXCEPT", "Bare `except:` Clause", "medium",
            "except", len(bare), "0",
            f"Bare `except:` found on line(s) {', '.join(map(str, bare))}. It swallows every exception "
            f"(including KeyboardInterrupt and SystemExit) and silently hides real bugs.",
            "Catch specific exceptions (e.g. `except (ValueError, KeyError) as exc:`), log them, and "
            "re-raise anything you cannot actually handle.")

    mut = s.get("mutable_defaults", [])
    if mut:
        names = ", ".join(f"{x['arg']}={x['default']}" for x in mut)
        add("MUTABLE_DEFAULT", "Mutable Default Argument", "medium",
            "defaults", len(mut), "0",
            f"Mutable default value(s) {names}: Python evaluates defaults once, so state leaks between "
            f"calls and causes hard-to-reproduce bugs.",
            "Use `None` as the default and create the object inside the function: "
            "`def f(items=None): items = [] if items is None else items`.")

    argc = s.get("arg_count", 0)
    if argc > ARGS_LIMIT:
        add("TOO_MANY_ARGS", "Too Many Parameters", "medium",
            "args", argc, f"<= {ARGS_LIMIT}",
            f"{label} takes {argc} parameters, a sign that it does too much or that related values "
            f"travel together without a structure.",
            "Group related parameters into a dataclass / NamedTuple / config object, or split the "
            "function by responsibility. Prefer keyword-only arguments for optional settings.")

    if prob >= DEFECT_THRESHOLD and not any(x["severity"] in ("high", "medium") for x in diags):
        add("MODEL_PROFILE", "Risky Halstead Profile", "medium",
            "e", round(m["e"], 1), "model-based",
            f"No single rule threshold is exceeded, but the model rates the combined metric profile as "
            f"defect-prone (volume v={m['v']:.0f}, effort e={m['e']:.0f}, {int(m['n'])} tokens). "
            f"Similar profiles were frequently defective in NASA JM1.",
            "Add focused unit tests for this function, reduce operator/operand density, and consider "
            "extracting sub-steps so each part can be verified independently.")

    order = {"high": 0, "medium": 1, "low": 2}
    diags.sort(key=lambda x: order.get(x["severity"], 3))
    return diags


# --------------------------------------------------------------------------- #
# High-level analysis API
# --------------------------------------------------------------------------- #

def _source_preview(code: str) -> str:
    lines = code.splitlines()
    if len(lines) > MAX_SOURCE_LINES_RETURNED:
        lines = lines[:MAX_SOURCE_LINES_RETURNED] + [f"# ... truncated ({len(code.splitlines())} lines total)"]
    return "\n".join(lines)


def analyze_files(files: list[tuple[str, str]], model) -> dict[str, Any]:
    """Analyze a list of (path, source) pairs and return a full project report.

    All functions across the project are scored in a single batched predict_proba call.
    """
    file_entries: list[dict[str, Any]] = []
    all_units: list[tuple[int, CodeUnit]] = []

    for path, code in files:
        entry: dict[str, Any] = {
            "path": path,
            "loc": len(code.splitlines()),
            "functions": [],
            "error": None,
        }
        try:
            units = extract_units(code)
        except SyntaxError as exc:
            entry["error"] = f"SyntaxError: {exc.msg} (line {exc.lineno})"
            units = []
        except Exception as exc:  # pragma: no cover - defensive
            entry["error"] = f"{type(exc).__name__}: {exc}"
            units = []
        idx = len(file_entries)
        file_entries.append(entry)
        all_units.extend((idx, u) for u in units)

    probs = predict_probabilities(model, [u.metrics for _, u in all_units])

    for (idx, unit), prob in zip(all_units, probs):
        file_entries[idx]["functions"].append({
            "name": unit.name,
            "qualname": unit.qualname,
            "kind": unit.kind,
            "start_line": unit.start_line,
            "end_line": unit.end_line,
            "risk": round(prob * 100, 2),
            "probability": round(prob, 4),
            "prediction": int(prob >= DEFECT_THRESHOLD),
            "risk_level": risk_level(prob),
            "metrics": {k: unit.metrics[k] for k in RADON_15},
            "smells": unit.smells,
            "diagnostics": build_diagnostics(unit, prob),
            "source": _source_preview(unit.source),
        })

    # Per-file aggregation
    for f in file_entries:
        funcs = f["functions"]
        funcs.sort(key=lambda x: x["risk"], reverse=True)
        f["function_count"] = len(funcs)
        f["high_risk_count"] = sum(1 for x in funcs if x["risk_level"] == "High")
        f["medium_risk_count"] = sum(1 for x in funcs if x["risk_level"] == "Medium")
        f["safe_count"] = sum(1 for x in funcs if x["risk_level"] == "Safe")
        f["max_risk"] = max((x["risk"] for x in funcs), default=0.0)
        f["avg_risk"] = round(sum(x["risk"] for x in funcs) / len(funcs), 2) if funcs else 0.0
        f["risk_level"] = "Error" if f["error"] else risk_level(f["max_risk"] / 100)

    # Riskiest files first; files that failed to parse sink to the bottom.
    file_entries.sort(key=lambda f: (f["error"] is None, f["max_risk"]), reverse=True)

    # Project summary
    all_funcs = [fn for f in file_entries for fn in f["functions"]]
    total_funcs = len(all_funcs)
    weights = [max(fn["metrics"]["loc"], 1.0) for fn in all_funcs]
    if total_funcs:
        weighted_risk = sum(fn["probability"] * w for fn, w in zip(all_funcs, weights)) / sum(weights)
        high_share = sum(1 for fn in all_funcs if fn["risk_level"] == "High") / total_funcs
        # Health: LOC-weighted safety, with an extra penalty for the share of high-risk units.
        health = max(0.0, min(100.0, 100.0 * (1.0 - weighted_risk) - 15.0 * high_share))
    else:
        weighted_risk, health = 0.0, 100.0

    summary = {
        "health_score": round(health, 1),
        "health_label": "Healthy" if health >= 75 else ("Needs Attention" if health >= 50 else "Critical"),
        "total_files": len(file_entries),
        "files_with_errors": sum(1 for f in file_entries if f["error"]),
        "total_functions": total_funcs,
        "high_risk": sum(1 for fn in all_funcs if fn["risk_level"] == "High"),
        "medium_risk": sum(1 for fn in all_funcs if fn["risk_level"] == "Medium"),
        "safe": sum(1 for fn in all_funcs if fn["risk_level"] == "Safe"),
        "avg_risk": round(sum(fn["risk"] for fn in all_funcs) / total_funcs, 2) if total_funcs else 0.0,
        "weighted_risk": round(weighted_risk * 100, 2),
        "total_loc": sum(f["loc"] for f in file_entries),
        "threshold": DEFECT_THRESHOLD,
        "medium_threshold": MEDIUM_THRESHOLD,
    }
    return {"summary": summary, "files": file_entries}


def analyze_code(code: str, model, filename: str = "pasted_code.py") -> dict[str, Any]:
    """Convenience wrapper to analyze a single source string."""
    return analyze_files([(filename, code)], model)


if __name__ == "__main__":  # Simple CLI: python extractor.py file.py
    import json
    import sys

    if len(sys.argv) < 2:
        print("usage: python extractor.py <file.py>")
        sys.exit(1)
    src_path = Path(sys.argv[1])
    report = analyze_code(src_path.read_text(encoding="utf-8"), load_model(), src_path.name)
    for f in report["files"]:
        for fn in f["functions"]:
            print(f"{fn['qualname']:<40} L{fn['start_line']}-{fn['end_line']:<6} "
                  f"{fn['risk']:6.2f}%  {fn['risk_level']}")
    print(json.dumps(report["summary"], indent=2))

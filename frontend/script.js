/* ==========================================================================
   DevInsight -- dashboard logic
   ========================================================================== */
(() => {
  "use strict";

  const KEY_METRICS = [
    { key: "loc", label: "LOC", desc: "Total lines of code (incl. comments & blanks)" },
    { key: "v(g)", label: "Cyclomatic Complexity v(g)", desc: "McCabe cyclomatic complexity (target <= 10)" },
    { key: "d", label: "Halstead Difficulty", desc: "Halstead operator/operand difficulty (target <= 15)" },
    { key: "v", label: "Halstead Volume", desc: "Halstead information volume" },
    { key: "lOComment", label: "Comments", desc: "Comment and docstring lines" },
  ];

  const EXAMPLE_BAD = `def handle_request(req, user, db, cache, config, logger, retries=3, history=[]):
    result = None
    cost = 0
    if req is not None:
        if req["type"] == "read":
            for key in req["keys"]:
                if key in cache:
                    if cache[key]["ttl"] > 0:
                        if user["role"] == "admin" or key.startswith(user["id"]):
                            result = cache[key]["value"]
                            cost = cost + len(key) * 0.5 + cache[key]["ttl"] / 60
                        else:
                            logger.warn("denied " + key)
                    else:
                        del cache[key]
                else:
                    try:
                        row = db.get(key)
                        if row and row["owner"] == user["id"] or user["role"] == "admin":
                            cache[key] = {"value": row, "ttl": config["ttl"] * 60 - len(history)}
                            result = row
                            cost = cost + len(str(row)) * 0.01 + 2
                    except:
                        logger.error("db failure")
        elif req["type"] == "write":
            attempt = 0
            while attempt < retries:
                size = len(req["payload"]) * 2 + len(req["key"]) % 7
                if user["quota"] - size > 0 and not config.get("readonly"):
                    db.put(req["key"], req["payload"])
                    user["quota"] = user["quota"] - size
                    history.append(req["key"])
                    cost = cost + size * 0.02
                    break
                elif config.get("queue") and len(history) < config["max_queue"]:
                    history.append(("queued", req["key"]))
                    cost = cost + 1
                    break
                elif attempt == retries - 1 and user["role"] != "admin":
                    logger.warn("quota exceeded for " + user["id"])
                attempt = attempt + 1
        elif req["type"] == "delete" and user["role"] == "admin":
            for k in req["keys"]:
                if k in cache:
                    del cache[k]
                if db.exists(k):
                    db.delete(k)
                    cost = cost + 0.5
                else:
                    logger.warn("missing " + k)
        elif req["type"] == "stats":
            hits = sum(1 for k in cache if cache[k]["ttl"] > 0)
            ratio = hits / (len(cache) or 1) * 100
            if ratio < config.get("min_ratio", 50) and len(history) > 10:
                logger.warn("low cache hit ratio %.2f" % ratio)
            result = {"hits": hits, "ratio": ratio, "size": len(cache), "queued": len(history)}
        else:
            logger.warn("unknown request")
    if cost > config.get("budget", 100) and user["role"] != "admin":
        user["credits"] = user["credits"] - (cost - config.get("budget", 100)) * 1.5
    elif cost > 0:
        user["credits"] = user["credits"] - cost * 0.1
    return result


def add(a, b):
    """Return the sum of two numbers."""
    return a + b
`;

  const EXAMPLE_GOOD = `"""Clean, well-structured helpers."""


def normalize_email(email: str) -> str:
    """Lower-case and strip an email address."""
    return email.strip().lower()


def clamp(value: float, low: float, high: float) -> float:
    """Keep \`value\` within the inclusive range [low, high]."""
    return max(low, min(value, high))


def chunk(items: list, size: int) -> list[list]:
    """Split a list into consecutive chunks of length \`size\`."""
    return [items[i:i + size] for i in range(0, len(items), size)]
`;

  // ---------- DOM helpers ----------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtBytes = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
  const levelColor = (lvl) => ({ High: "var(--high)", Medium: "var(--medium)", Safe: "var(--safe)" }[lvl] || "var(--dim)");
  const badge = (lvl) => `<span class="badge badge-${esc(String(lvl).toLowerCase())}">${esc(lvl)}${lvl === "Error" ? "" : " Risk"}</span>`;

  const ICONS = {
    chev: '<svg class="chev" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>',
    file: '<svg class="file-icon" viewBox="0 0 24 24"><path d="M14 3H6a1 1 0 00-1 1v16a1 1 0 001 1h12a1 1 0 001-1V8z"/><path d="M14 3v5h5"/></svg>',
    high: '<svg viewBox="0 0 24 24"><path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/></svg>',
    medium: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/></svg>',
    low: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    ok: '<svg viewBox="0 0 24 24"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-4.5"/></svg>',
    wrench: '<svg viewBox="0 0 24 24"><path d="M14.7 6.3a4 4 0 00-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 005.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/></svg>',
    chart: '<svg viewBox="0 0 24 24"><path d="M4 19h16M7 16V9M12 16V5M17 16v-4"/></svg>',
  };

  // ---------- State ----------
  let selectedFile = null;
  let lastReport = null;
  let currentFilter = "all";

  // ---------- Analyzer status ----------
  async function checkHealth() {
    const el = $("#model-status");
    try {
      const res = await fetch("/api/health");
      if (!res.ok) throw new Error("unhealthy");
      await res.json();
      el.classList.remove("err");
      el.classList.add("ok");
      $(".label", el).textContent = "Analyzer Ready";
    } catch {
      el.classList.remove("ok");
      el.classList.add("err");
      $(".label", el).textContent = "Backend offline";
    }
  }

  // ---------- Tabs ----------
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    $("#tab-upload").classList.toggle("hidden", tab.dataset.tab !== "upload");
    $("#tab-paste").classList.toggle("hidden", tab.dataset.tab !== "paste");
    hideError();
  }));

  // ---------- File selection ----------
  const dropzone = $("#dropzone");
  const fileInput = $("#file-input");

  function setFile(file) {
    if (!file) return;
    const name = file.name.toLowerCase();
    if (!name.endsWith(".zip") && !name.endsWith(".py")) {
      showError("Unsupported file type - please choose a .zip project or a .py file.");
      return;
    }
    hideError();
    selectedFile = file;
    $("#file-name").textContent = file.name;
    $("#file-size").textContent = fmtBytes(file.size);
    $("#file-chip").classList.remove("hidden");
    $("#btn-analyze-file").disabled = false;
  }

  function clearFile() {
    selectedFile = null;
    fileInput.value = "";
    $("#file-chip").classList.add("hidden");
    $("#btn-analyze-file").disabled = true;
  }

  fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
  ["dragenter", "dragover"].forEach((ev) => dropzone.addEventListener(ev, (e) => {
    e.preventDefault(); dropzone.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach((ev) => dropzone.addEventListener(ev, (e) => {
    e.preventDefault(); dropzone.classList.remove("drag");
  }));
  dropzone.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));
  $("#file-clear").addEventListener("click", (e) => { e.preventDefault(); clearFile(); });

  // ---------- API calls ----------
  async function postAnalysis(url, options, label) {
    hideError();
    setLoading(true);
    try {
      const res = await fetch(url, options);
      let data;
      try { data = await res.json(); } catch { throw new Error(`Server returned ${res.status} ${res.statusText}`); }
      if (!res.ok || data.error) throw new Error(data.error || `Request failed (${res.status})`);
      lastReport = data;
      renderReport(data, label);
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  function analyzeFile(file) {
    const fd = new FormData();
    fd.append("file", file, file.name);
    return postAnalysis("/api/analyze-zip", { method: "POST", body: fd }, file.name);
  }

  $("#btn-analyze-file").addEventListener("click", () => selectedFile && analyzeFile(selectedFile));

  $("#btn-sample").addEventListener("click", async () => {
    try {
      const res = await fetch("/sample_project.zip");
      if (!res.ok) throw new Error("sample_project.zip not found on the server.");
      const blob = await res.blob();
      const file = new File([blob], "sample_project.zip", { type: "application/zip" });
      setFile(file);
      analyzeFile(file);
    } catch (err) {
      showError(err.message);
    }
  });

  const codeInput = $("#code-input");
  $("#btn-analyze-code").addEventListener("click", () => {
    const code = codeInput.value;
    if (!code.trim()) { showError("Paste some Python code first."); return; }
    postAnalysis("/api/analyze-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, filename: "pasted_code.py" }),
    }, "pasted code");
  });

  // ---------- Code editor ----------
  function updateGutter() {
    const n = Math.max(codeInput.value.split("\n").length, 1);
    $("#editor-gutter").textContent = Array.from({ length: n }, (_, i) => i + 1).join("\n");
    $("#editor-gutter").scrollTop = codeInput.scrollTop;
  }
  codeInput.addEventListener("input", updateGutter);
  codeInput.addEventListener("scroll", () => { $("#editor-gutter").scrollTop = codeInput.scrollTop; });
  codeInput.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const { selectionStart: s, selectionEnd: en } = codeInput;
      codeInput.setRangeText("    ", s, en, "end");
      updateGutter();
    } else if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      $("#btn-analyze-code").click();
    }
  });
  const loadExample = (code) => { codeInput.value = code; updateGutter(); hideError(); };
  $("#btn-example-bad").addEventListener("click", () => loadExample(EXAMPLE_BAD));
  $("#btn-example-good").addEventListener("click", () => loadExample(EXAMPLE_GOOD));
  $("#btn-clear-code").addEventListener("click", () => loadExample(""));

  // ---------- UI state helpers ----------
  function setLoading(on) {
    $("#loading").classList.toggle("hidden", !on);
    $$("#btn-analyze-file, #btn-analyze-code, #btn-sample").forEach((b) => {
      if (on) b.disabled = true;
      else b.disabled = b.id === "btn-analyze-file" ? !selectedFile : false;
    });
    if (on) $("#loading").scrollIntoView({ behavior: "smooth", block: "center" });
  }
  function showError(msg) { const box = $("#error-box"); box.textContent = msg; box.classList.remove("hidden"); }
  function hideError() { $("#error-box").classList.add("hidden"); }

  // ---------- Rendering ----------
  function renderReport(report, label) {
    const s = report.summary;
    const meta = report.meta || {};
    $("#results").classList.remove("hidden");
    $("#nav-results").classList.remove("hidden");

    const extras = [];
    if (meta.ignored_files) extras.push(`${meta.ignored_files} file(s) in ignored folders skipped`);
    if (s.files_with_errors) extras.push(`${s.files_with_errors} file(s) could not be parsed`);
    $("#results-meta").innerHTML =
      `Source: ${esc(label)} &bull; ${s.total_loc.toLocaleString()} lines &bull; analyzed in ${esc(meta.elapsed_ms ?? "?")} ms` +
      (extras.length ? ` &bull; ${extras.map(esc).join(" &bull; ")}` : "");

    // Health ring
    const score = s.health_score;
    const ring = $("#health-ring");
    const circ = 2 * Math.PI * 52;
    const color = score >= 75 ? "var(--safe)" : score >= 50 ? "var(--medium)" : "var(--high)";
    ring.style.stroke = color;
    ring.style.strokeDashoffset = circ;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      ring.style.strokeDashoffset = circ * (1 - score / 100);
    }));
    animateNumber($("#health-score"), score, 0);
    $("#health-label").textContent = s.health_label;
    $("#health-label").style.color = color;

    animateNumber($("#stat-files"), s.total_files);
    animateNumber($("#stat-functions"), s.total_functions);
    animateNumber($("#stat-high"), s.high_risk);
    animateNumber($("#stat-medium"), s.medium_risk);
    animateNumber($("#stat-safe"), s.safe);
    animateNumber($("#stat-avg"), s.avg_risk, 1, "%");
    $("#thr-label").textContent = s.threshold;
    $("#med-thr-label").textContent = s.medium_threshold;

    // Distribution bar
    const tot = Math.max(s.total_functions, 1);
    $("#dist-bar").innerHTML = ["high", "medium", "safe"].map((k) => {
      const v = k === "high" ? s.high_risk : k === "medium" ? s.medium_risk : s.safe;
      return `<span class="${k}" style="width:${(v / tot) * 100}%" title="${v} ${k}"></span>`;
    }).join("");

    // Reset filters
    currentFilter = "all";
    $$(".chip").forEach((c) => c.classList.toggle("active", c.dataset.filter === "all"));
    $("#search").value = "";

    $("#file-list").innerHTML = report.files.map(renderFile).join("");
    applyFilters();
    setTimeout(() => $("#results").scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  }

  function animateNumber(el, target, decimals = 0, suffix = "") {
    const start = performance.now();
    const dur = 700;
    const step = (now) => {
      const p = Math.min((now - start) / dur, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = (target * eased).toFixed(decimals) + suffix;
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function renderFile(file, idx) {
    const lvl = file.risk_level;
    const openByDefault = lvl === "High" || idx === 0;
    const sub = file.error
      ? `<span>${file.loc} lines</span><span>parse error</span>`
      : `<span>${file.loc} lines</span><span>${file.function_count} unit(s)</span>` +
        (file.high_risk_count ? `<span class="h">${file.high_risk_count} high</span>` : "") +
        (file.medium_risk_count ? `<span class="m">${file.medium_risk_count} medium</span>` : "") +
        (file.safe_count ? `<span class="s">${file.safe_count} safe</span>` : "");

    const body = file.error
      ? `<div class="file-error">! ${esc(file.error)}</div>`
      : file.functions.map((fn) => renderFunction(fn)).join("");

    return `
      <div class="panel file-card lvl-${esc(lvl)} ${openByDefault ? "open" : ""}" data-path="${esc(file.path.toLowerCase())}">
        <div class="file-head" data-toggle>
          ${ICONS.chev}${ICONS.file}
          <div class="file-title">
            <div class="file-path" title="${esc(file.path)}">${esc(file.path)}</div>
            <div class="file-sub">${sub}</div>
          </div>
          <div class="file-risk">
            ${file.error ? "" : `
            <div class="mini">
              <div class="mini-bar"><span style="width:${file.max_risk}%;background:${levelColor(lvl)}"></span></div>
              <div class="mini-label">max ${file.max_risk.toFixed(1)}% &bull; avg ${file.avg_risk.toFixed(1)}%</div>
            </div>`}
            ${badge(lvl)}
          </div>
        </div>
        <div class="file-body">${body}</div>
      </div>`;
  }

  function metricClass(key, val, m) {
    if (key === "v(g)" && val > 10) return val > 20 ? "bad" : "warn";
    if (key === "loc" && val > 50) return val > 100 ? "bad" : "warn";
    if (key === "d" && val > 15) return val > 30 ? "bad" : "warn";
    if (key === "lOComment" && val === 0 && m.loc > 25) return "warn";
    return "";
  }

  function fmtMetric(v) {
    if (Number.isInteger(v)) return v.toLocaleString();
    if (Math.abs(v) >= 100) return Math.round(v).toLocaleString();
    return v.toFixed(v < 1 ? 3 : 2);
  }

  function renderFunction(fn) {
    const m = fn.metrics;
    const kindTag = fn.kind === "module" ? "module-level code" : fn.kind;
    const issues = fn.diagnostics.length;
    const metricsHtml = KEY_METRICS.map((item) => `
      <div class="metric ${metricClass(item.key, m[item.key], m)}" title="${esc(item.desc)}">
        <div class="metric-key">${esc(item.label)}</div>
        <div class="metric-val">${fmtMetric(m[item.key])}</div>
      </div>`).join("");

    const diagHtml = issues
      ? fn.diagnostics.map((d) => `
        <div class="diag ${esc(d.severity)}">
          <div class="diag-ico">${ICONS[d.severity] || ICONS.low}</div>
          <div>
            <div class="diag-title">${esc(d.title)}
              <span class="val">${esc(d.metric)} = ${esc(d.value)} &bull; target ${esc(d.threshold)}</span>
            </div>
            <p>${inlineCode(d.why)}</p>
            <div class="fix"><b>How to fix:</b> ${inlineCode(d.fix)}</div>
          </div>
        </div>`).join("")
      : `<div class="diag-ok">${ICONS.ok}<span>No root-cause issues detected &mdash; metrics are within healthy thresholds and no Python code smells were found.</span></div>`;

    const hlLines = new Set([...(fn.smells?.bare_except_lines || [])]);
    if (fn.smells?.max_nesting > 4 && fn.smells.max_nesting_line) hlLines.add(fn.smells.max_nesting_line);

    const thr = lastReport?.summary?.threshold ?? 0.26;
    const medThr = lastReport?.summary?.medium_threshold ?? 0.20;
    const risk = fn.risk;
    return `
      <div class="fn-card" data-level="${esc(fn.risk_level)}" data-name="${esc(fn.qualname.toLowerCase())}">
        <div class="fn-head" data-toggle>
          ${ICONS.chev}
          <span class="fn-name">${esc(fn.qualname)}${fn.kind === "module" ? "" : "()"}</span>
          <span class="tag">${esc(kindTag)}</span>
          <span class="fn-lines">Lines ${fn.start_line}&ndash;${fn.end_line}</span>
          <div class="fn-right">
            ${issues ? `<span class="fn-issues">${issues} issue${issues > 1 ? "s" : ""}</span>` : ""}
            ${badge(fn.risk_level)}
            <span class="fn-risk ${esc(fn.risk_level)}">${risk.toFixed(1)}%</span>
          </div>
        </div>
        <div class="fn-body">
          <div class="risk-meter">
            <div class="risk-meter-wrap">
              <div class="risk-meter-track" style="background:linear-gradient(90deg, var(--safe) 0 ${medThr * 100}%, var(--medium) ${medThr * 100}% ${thr * 100}%, var(--high) ${thr * 100}% 100%)"></div>
              <div class="risk-meter-fill" style="width:${risk}%;background:${levelColor(fn.risk_level)}"></div>
              <div class="risk-meter-thr" style="left:${thr * 100}%" title="Decision threshold ${thr}"></div>
            </div>
            <div class="risk-meter-labels"><span>0%</span><span>Defect Risk ${risk.toFixed(2)}% &bull; P(defect) = ${fn.probability.toFixed(4)} &bull; threshold ${thr}</span><span>100%</span></div>
          </div>

          <div class="sub-title">${ICONS.chart} Key Complexity Metrics</div>
          <div class="metrics-grid">${metricsHtml}</div>

          <div class="sub-title">${ICONS.wrench} Why It Was Flagged &amp; How to Fix It</div>
          <div class="diag-wrap">${diagHtml}</div>

          <details class="src">
            <summary>View source (lines ${fn.start_line}&ndash;${fn.end_line})</summary>
            ${renderCode(fn.source, fn.start_line, hlLines)}
          </details>
        </div>
      </div>`;
  }

  function inlineCode(text) {
    return esc(text).replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function renderCode(src, startLine, hlLines) {
    const rows = String(src).split("\n").map((line, i) => {
      const ln = startLine + i;
      return `<tr class="${hlLines.has(ln) ? "hl" : ""}"><td class="ln">${ln}</td><td>${esc(line) || " "}</td></tr>`;
    }).join("");
    return `<div class="code-view"><table>${rows}</table></div>`;
  }

  // ---------- Expand / collapse (event delegation) ----------
  $("#file-list").addEventListener("click", (e) => {
    const head = e.target.closest("[data-toggle]");
    if (!head) return;
    head.parentElement.classList.toggle("open");
  });
  $("#btn-expand").addEventListener("click", () => $$(".file-card, .fn-card").forEach((c) => {
    if (c.style.display !== "none") c.classList.add("open");
  }));
  $("#btn-collapse").addEventListener("click", () => $$(".file-card, .fn-card").forEach((c) => c.classList.remove("open")));

  // ---------- Filters & search ----------
  $$(".chip").forEach((chip) => chip.addEventListener("click", () => {
    currentFilter = chip.dataset.filter;
    $$(".chip").forEach((c) => c.classList.toggle("active", c === chip));
    applyFilters();
  }));
  $("#search").addEventListener("input", applyFilters);

  function applyFilters() {
    const q = $("#search").value.trim().toLowerCase();
    let visibleFns = 0;
    $$(".file-card").forEach((card) => {
      const pathMatch = !q || card.dataset.path.includes(q);
      const fns = $$(".fn-card", card);
      let shown = 0;
      fns.forEach((fn) => {
        const lvlOk = currentFilter === "all" || fn.dataset.level === currentFilter;
        const qOk = pathMatch || fn.dataset.name.includes(q);
        const show = lvlOk && qOk;
        fn.style.display = show ? "" : "none";
        if (show) shown++;
      });
      const isErrorCard = fns.length === 0;
      const showCard = isErrorCard ? currentFilter === "all" && pathMatch : shown > 0;
      card.style.display = showCard ? "" : "none";
      if (showCard && (q || currentFilter !== "all")) card.classList.add("open");
      visibleFns += shown;
    });
    const anyCard = $$(".file-card").some((c) => c.style.display !== "none");
    $("#empty-filter").classList.toggle("hidden", anyCard || !lastReport);
  }

  // ---------- Export ----------
  $("#btn-export").addEventListener("click", () => {
    if (!lastReport) return;
    const blob = new Blob([JSON.stringify(lastReport, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "devinsight_report.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  updateGutter();
  checkHealth();
})();

/* ============================================================================
   OI 对拍器 — 前端逻辑（无框架）
   ========================================================================== */
(() => {
  "use strict";

  const TOKEN = window.__TOKEN__ || "";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const STATUS = {
    AC: { label: "通过", cls: "ok" },
    WA: { label: "答案错误", cls: "bad" },
    TLE: { label: "时间超限", cls: "warn" },
    MLE: { label: "空间超限", cls: "purple" },
    RE: { label: "运行错误", cls: "bad" },
    OLE: { label: "输出超限", cls: "warn" },
    CE: { label: "编译错误", cls: "bad" },
    PE: { label: "格式错误", cls: "warn" },
    PC: { label: "部分分", cls: "info" },
    CJF: { label: "Checker 异常", cls: "purple" },
    RUN: { label: "运行完成", cls: "info" },
    PENDING: { label: "评测中", cls: "pending" },
    SKIP: { label: "已跳过", cls: "muted" },
    STOPPED: { label: "已中止", cls: "muted" },
    NONE: { label: "未评测", cls: "muted" },
  };

  const STATUS_HINT = {
    TLE: "程序运行超过时间限制。可检查是否存在死循环、常数过大或复杂度过高，也可以适当放宽时间限制。",
    MLE: "程序峰值内存超过限制。检查是否有超大数组、未释放的动态内存，或递归过深导致的栈占用。",
    RE: "程序异常退出。常见原因包括数组越界、除以零、空指针访问、递归过深导致栈溢出等。",
    OLE: "输出内容过多。检查是否残留调试输出，或在死循环中持续打印。",
    CE: "编译没有通过，请查看左侧编译日志定位语法或链接错误。",
    PE: "Special Judge 认为输出格式不符合要求（presentation error），检查多余空格、换行或缺失的内容。",
    PC: "Special Judge 给出了部分分。可以查看 Checker 标签页里 checker 的具体说明。",
    CJF: "Checker 自身运行失败（编译错误、崩溃、超时或退出码异常），请查看 Checker 标签页。",
    RUN: "该测试点没有标准答案，只记录运行时间与内存。",
  };

  const PRESETS = {
    strict: { timeMs: 500, memoryMB: 128, spaceMB: 32 },
    normal: { timeMs: 1000, memoryMB: 256, spaceMB: 64 },
    loose: { timeMs: 2000, memoryMB: 512, spaceMB: 128 },
  };

  const App = {
    data: null,
    selected: new Set(),
    known: new Set(),
    filter: "",
    drawerId: null,
    drawerTab: "diff",
    detail: null,
    lastCaseKey: "",
    timer: null,
    busy: 0,
    wasRunning: false,
    compileDismissed: null,
    checkerDismissed: null,
    toastDone: localStorage.getItem("oi-toast-done") !== "0",
  };

  /* ---------------------------------------------------------------- 工具 */

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtBytes(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }

  function fmtTime(ms) {
    const n = Number(ms) || 0;
    if (n >= 1000) return { value: (n / 1000).toFixed(2), unit: "s" };
    if (n >= 100) return { value: Math.round(n).toString(), unit: "ms" };
    return { value: n.toFixed(1), unit: "ms" };
  }

  function fmtMem(kb) {
    const n = Number(kb) || 0;
    if (n >= 1024 * 1024) return { value: (n / 1024 / 1024).toFixed(2), unit: "GB" };
    return { value: (n / 1024).toFixed(1), unit: "MB" };
  }

  function timeAgo(seconds) {
    if (!seconds) return "";
    const diff = Date.now() / 1000 - Number(seconds);
    if (diff < 60) return "刚刚";
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
    return `${Math.floor(diff / 86400)} 天前`;
  }

  async function api(path, body) {
    const joiner = path.includes("?") ? "&" : "?";
    const url = `/api/${path}${joiner}k=${encodeURIComponent(TOKEN)}`;
    const options = body === undefined
      ? { method: "GET" }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const response = await fetch(url, options);
    let data = null;
    try { data = await response.json(); } catch (err) { /* ignore */ }
    if (!response.ok) {
      const message = (data && data.error) || `请求失败 (${response.status})`;
      throw new Error(message);
    }
    return data;
  }

  function toast(message, kind = "info", title = "") {
    const host = $("#toasts");
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    const glyph = kind === "ok" ? "✓" : kind === "bad" ? "!" : kind === "warn" ? "!" : "i";
    el.innerHTML = `<span class="t-icon">${glyph}</span><div>${title ? `<b>${esc(title)}</b><br/>` : ""}${esc(message)}</div>`;
    host.appendChild(el);
    setTimeout(() => {
      el.classList.add("out");
      setTimeout(() => el.remove(), 260);
    }, 3800);
  }

  function showBusy(text) {
    App.busy += 1;
    $("#busyText").textContent = text || "处理中…";
    $("#busy").hidden = false;
  }

  function hideBusy() {
    App.busy = Math.max(0, App.busy - 1);
    if (App.busy === 0) $("#busy").hidden = true;
  }

  /* -------------------------------------------------------------- 状态 */

  function applyState(data, options = {}) {
    const previousFolder = App.data && App.data.folder ? App.data.folder.path : null;
    const nextFolder = data.folder ? data.folder.path : null;
    App.data = data;

    if (previousFolder !== nextFolder) {
      App.selected = new Set();
      App.known = new Set();
      closeDrawer();
    }

    const ids = new Set(data.cases.map((c) => c.id));
    data.cases.forEach((c) => {
      if (!App.known.has(c.id)) {
        App.known.add(c.id);
        App.selected.add(c.id);
      }
    });
    App.known.forEach((id) => {
      if (!ids.has(id)) { App.known.delete(id); App.selected.delete(id); }
    });
    App.selected.forEach((id) => { if (!ids.has(id)) App.selected.delete(id); });

    renderHeader(data);
    renderSource(data);
    renderLimits(data);
    renderCompile(data);
    renderChecker(data);
    renderCases(data);
    renderSummary(data);
    renderJudging(data);
    renderHero(data);
    renderRecent(data);
    updateSelectionUI();
    applyThemeToControls(data.settings.theme);
  }

  function renderHeader(d) {
    const has = !!(d.folder && d.folder.exists);
    $("#problemStrip").classList.toggle("on", has);
    $("#problemName").textContent = has ? d.problem.name : "未打开题目";
    $("#problemPath").textContent = has
      ? d.folder.path
      : "选择一个题目文件夹开始评测";
    $("#problemStrip").title = has
      ? `${d.folder.path}\n${d.cases.length} 个测试点`
      : "";
    $("#btnRescan").disabled = !has;
    document.title = has ? `${d.problem.name} · OI 对拍器` : "OI 对拍器";
  }

  function renderSource(d) {
    const src = d.source;
    const compile = d.compile || {};
    const binary = d.binary;
    const badge = $("#sourceBadge");
    const body = $("#sourceBody");

    let label = "未选择";
    let tone = "muted";
    if (src) {
      if (compile.status === "error") { label = "编译失败"; tone = "error"; }
      else if (compile.status === "ok") { label = compile.warnings ? `已编译 · ${compile.warnings} 警告` : "已编译"; tone = "ok"; }
      else if (binary) { label = "已有程序"; tone = "ok"; }
      else { label = "待编译"; tone = "muted"; }
    } else if (binary) {
      label = "可执行文件";
      tone = "ok";
    }
    badge.textContent = label;
    badge.className = `badge ${tone}`;

    if (src) {
      const kindIcon = src.kind === "py" ? "py" : "cpp";
      body.innerHTML = `
        <div class="source-file">
          <div class="file-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
              <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>
              ${kindIcon === "py"
                ? '<path d="M10 13.5 13 16l-3 2.5"/><path d="M14 18.5h2.5"/>'
                : '<path d="M9.5 13 12 15.5 14.5 13"/><path d="M12 10v5.5"/>'}
            </svg>
          </div>
          <div class="file-info">
            <div class="file-name" title="${esc(src.path)}">${esc(src.name)}</div>
            <div class="file-meta">${fmtBytes(src.size)} · ${esc(timeAgo(src.mtime))}</div>
          </div>
        </div>`;
    } else if (binary) {
      body.innerHTML = `
        <div class="source-file">
          <div class="file-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="m8 10 2.5 2L8 14"/><path d="M12.5 14h3.5"/></svg>
          </div>
          <div class="file-info">
            <div class="file-name" title="${esc(binary.path)}">${esc(binary.name)}</div>
            <div class="file-meta">直接运行已有可执行文件</div>
          </div>
        </div>`;
    } else {
      body.innerHTML = '<div class="source-empty">尚未识别到选手程序 · 点击「更换」导入 .cpp</div>';
    }

    $("#btnCompileNow").disabled = !src || !!d.judging.running;
    $("#btnRevealSource").disabled = !src && !binary;
    $("#btnCompileNow").dataset.path = src ? src.path : "";
  }

  function setValueIfIdle(selector, value) {
    const el = $(selector);
    if (!el) return;
    if (document.activeElement === el) return;
    if (el.type === "checkbox") el.checked = !!value;
    else el.value = value == null ? "" : value;
  }

  function renderLimits(d) {
    const s = d.settings;
    setValueIfIdle("#limTime", s.timeMs);
    setValueIfIdle("#limMem", s.memoryMB);
    setValueIfIdle("#limSpace", s.spaceMB);
    setValueIfIdle("#compareMode", s.compareMode);
    setValueIfIdle("#stopOnFail", s.stopOnFail);
    setValueIfIdle("#compiler", s.compiler);
    setValueIfIdle("#flags", s.flags);
    setValueIfIdle("#stackMB", s.stackMB);

    const compiler = d.compiler || {};
    const badge = $("#compilerBadge");
    badge.textContent = compiler.ok ? "可编译" : "未找到";
    badge.className = `badge ${compiler.ok ? "ok" : "error"}`;
    badge.title = compiler.version || "";

    const preset = Object.entries(PRESETS).find(([, p]) => p.timeMs === s.timeMs && p.memoryMB === s.memoryMB);
    $$(".chip-btn[data-preset]").forEach((btn) => {
      btn.classList.toggle("active", !!preset && btn.dataset.preset === preset[0]);
    });
  }

  function renderCompile(d) {
    const compile = d.compile || {};
    const log = $("#compileLog");
    const dismissed = App.compileDismissed === compile.at;
    if (!compile.status || compile.status === "none" || dismissed) {
      log.hidden = true;
      return;
    }
    const isError = compile.status === "error";
    const hasOutput = (compile.output || "").trim().length > 0;
    const warnings = Number(compile.warnings) || 0;
    // 成功且无警告时不占用侧栏空间
    if (!isError && (!hasOutput || warnings === 0)) { log.hidden = true; return; }
    log.hidden = false;
    log.classList.toggle("error", isError);
    $("#compileLogTitle").textContent = isError
      ? "编译失败"
      : `编译完成 · ${compile.ms} ms${compile.warnings ? ` · ${compile.warnings} 个警告` : ""}`;
    $("#compileLogText").textContent = compile.output || "";
  }

  function renderChecker(d) {
    const checker = d.checker || {};
    const compile = checker.compile || {};
    const enabled = !!checker.enabled;
    const running = !!(d.judging && d.judging.running);

    $("#spjEnabled").checked = enabled;
    $("#spjEnabled").disabled = running;

    const badge = $("#checkerBadge");
    let label = "未启用";
    let tone = "muted";
    if (!checker.path) {
      label = "未选择";
    } else if (!enabled) {
      label = "已关闭";
    } else if (compile.status === "error") {
      label = "编译失败";
      tone = "error";
    } else if (compile.status === "ok") {
      label = compile.warnings ? `已编译 · ${compile.warnings} 警告` : "已编译";
      tone = "ok";
    } else {
      label = "待编译";
    }
    badge.textContent = label;
    badge.className = `badge ${tone}`;

    const body = $("#checkerBody");
    if (checker.path) {
      const kindLabel = checker.kind === "exe" ? "预编译 checker" : "testlib checker";
      const compiledAt = compile.ms ? ` · ${compile.ms} ms` : "";
      body.innerHTML = `<div class="source-file">
        <div class="file-ico">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>
            <path d="m9 13 1.8 1.8L9 16.6"/><path d="M13 17h2.5"/>
          </svg>
        </div>
        <div class="file-info">
          <div class="file-name" title="${esc(checker.path)}">${esc(checker.name)}</div>
          <div class="file-meta">${esc(kindLabel)}${compiledAt}</div>
        </div>
      </div>`;
    } else {
      body.innerHTML = '<div class="source-empty">选择 checker.cpp（使用 testlib.h）即可启用特殊评测</div>';
    }

    const testlib = checker.testlib || "";
    const hint = $("#checkerHint");
    if (checker.path && checker.kind !== "exe" && !testlib) {
      hint.innerHTML = '未在编译器目录下找到 <code>testlib.h</code>，如编译失败可用 <code>-I</code> 指定其目录。';
    } else if (checker.path && checker.kind !== "exe") {
      const segments = testlib.split(/[\\/]/).filter(Boolean);
      const short = segments.slice(-2).join("\\");
      hint.innerHTML = `testlib: <code title="${esc(testlib)}">…\\${esc(short)}</code> · 参数 <code>input output answer</code>`;
    } else {
      hint.innerHTML = 'checker 以 <code>input output answer</code> 三个参数运行，用 testlib 的退出码给出结论。';
    }

    const compileBtn = $("#btnCompileChecker");
    compileBtn.disabled = !checker.path || running;
    compileBtn.textContent = compile.status === "ok" ? "重新编译" : "编译";

    const select = $("#compareMode");
    select.disabled = enabled;
    select.title = enabled ? "已启用 Special Judge，答案比较方式不生效" : "";

    const log = $("#checkerLog");
    const dismissed = App.checkerDismissed === compile.at;
    const showLog = compile.at && !dismissed
      && (compile.status === "error" || Number(compile.warnings) > 0);
    if (!showLog) {
      log.hidden = true;
    } else {
      log.hidden = false;
      log.classList.toggle("error", compile.status === "error");
      $("#checkerLogTitle").textContent = compile.status === "error"
        ? "Checker 编译失败"
        : `Checker 编译完成 · ${compile.ms} ms`;
      $("#checkerLogText").textContent = compile.output || "";
    }
  }

  function statusPill(result) {
    const status = result && result.status ? result.status : "NONE";
    const meta = STATUS[status] || STATUS.NONE;
    return `<span class="pill ${meta.cls}" title="${esc((result && result.message) || "")}"><i></i>${meta.label}</span>`;
  }

  function metricHTML(kind, value, limit) {
    if (value === null || value === undefined || value <= 0) {
      return '<div class="metric muted"><span class="value">—</span><div class="meter"><i></i></div></div>';
    }
    const formatted = kind === "time" ? fmtTime(value) : fmtMem(value);
    const ratio = limit > 0 ? Math.min(1.5, value / limit) : 0;
    const tone = ratio >= 1 ? "bad" : ratio >= 0.8 ? "warn" : "";
    const width = Math.min(100, ratio * 100);
    return `<div class="metric ${tone}">
        <span class="value">${formatted.value}<small>${formatted.unit}</small></span>
        <div class="meter"><i style="width:${width.toFixed(1)}%"></i></div>
      </div>`;
  }

  function caseRow(caseItem, index) {
    const result = caseItem.result;
    const checked = App.selected.has(caseItem.id) ? "checked" : "";
    const sizeLabel = caseItem.hasAnswer ? fmtBytes(caseItem.size) : `${fmtBytes(caseItem.size)} · 无标准答案`;
    const rel = caseItem.rel && caseItem.rel !== caseItem.name ? caseItem.rel : "";
    const settings = App.data.settings;
    return `<tr class="case-row ${checked ? "selected" : ""}" data-id="${caseItem.id}" data-status="${result ? result.status : ""}">
      <td class="col-check"><input type="checkbox" class="row-check" ${checked} /></td>
      <td class="col-idx">${index + 1}</td>
      <td>
        <div class="case-name" title="${esc(caseItem.in)}">${esc(caseItem.name)}</div>
        <div class="case-sub">${esc(rel ? rel : sizeLabel)}</div>
      </td>
      <td class="col-status">${statusPill(result)}</td>
      <td class="col-time">${metricHTML("time", result ? result.timeMs : null, settings.timeMs)}</td>
      <td class="col-mem">${metricHTML("mem", result ? result.memoryKB : null, settings.memoryMB * 1024)}</td>
      <td class="col-act"><button class="row-btn" data-view="${caseItem.id}">详情</button></td>
    </tr>`;
  }

  function visibleCases() {
    const all = (App.data && App.data.cases) || [];
    if (!App.filter) return all;
    const needle = App.filter.toLowerCase();
    return all.filter((c) => c.name.toLowerCase().includes(needle) || (c.rel || "").toLowerCase().includes(needle));
  }

  function renderCases(d) {
    const cases = visibleCases();
    const key = `${App.filter}|${cases.map((c) => c.id).join(",")}|${d.cases.length}`;
    const tbody = $("#caseBody");

    if (key !== App.lastCaseKey) {
      App.lastCaseKey = key;
      if (!cases.length) {
        tbody.innerHTML = d.cases.length
          ? '<tr><td colspan="7"><div class="code-empty">没有匹配的测试用例</div></td></tr>'
          : "";
      } else {
        tbody.innerHTML = cases.map((c, i) => caseRow(c, i)).join("");
      }
      return;
    }

    cases.forEach((caseItem) => {
      const row = tbody.querySelector(`tr[data-id="${caseItem.id}"]`);
      if (!row) return;
      const result = caseItem.result;
      const previous = row.dataset.status;
      const current = result ? result.status : "";
      const settings = d.settings;
      row.querySelector(".col-status").innerHTML = statusPill(result);
      row.querySelector(".col-time").innerHTML = metricHTML("time", result ? result.timeMs : null, settings.timeMs);
      row.querySelector(".col-mem").innerHTML = metricHTML("mem", result ? result.memoryKB : null, settings.memoryMB * 1024);
      row.querySelector(".row-check").checked = App.selected.has(caseItem.id);
      row.classList.toggle("selected", App.selected.has(caseItem.id));
      row.dataset.status = current;
      if (current && current !== previous && current !== "PENDING") {
        row.classList.remove("flash");
        void row.offsetWidth;
        row.classList.add("flash");
      }
    });
  }

  function renderSummary(d) {
    const host = $("#summaryChips");
    const judged = d.cases.filter((c) => c.result && !["PENDING", "SKIP", "STOPPED"].includes(c.result.status));
    const total = d.cases.length;
    if (!total) { host.innerHTML = ""; return; }

    const counts = {};
    let maxTime = 0;
    let maxMem = 0;
    judged.forEach((c) => {
      const status = c.result.status;
      counts[status] = (counts[status] || 0) + 1;
      maxTime = Math.max(maxTime, Number(c.result.timeMs) || 0);
      maxMem = Math.max(maxMem, Number(c.result.memoryKB) || 0);
    });

    const ac = counts.AC || 0;
    const chips = [];
    if (judged.length) {
      chips.push(`<div class="chip"><span class="ok-text">通过</span><b>${ac}</b><span class="muted">/ ${total}</span></div>`);
      if (maxTime > 0) chips.push(`<div class="chip">最高用时 <b>${fmtTime(maxTime).value}</b> ${fmtTime(maxTime).unit}</div>`);
      if (maxMem > 0) chips.push(`<div class="chip">最大内存 <b>${fmtMem(maxMem).value}</b> ${fmtMem(maxMem).unit}</div>`);
      Object.entries(counts).forEach(([status, count]) => {
        if (status === "AC") return;
        const meta = STATUS[status] || STATUS.NONE;
        const tone = meta.cls === "ok" ? "ok-text" : meta.cls === "info" ? "" : "bad-text";
        chips.push(`<div class="chip"><span class="${tone}">${meta.label}</span><b>${count}</b></div>`);
      });
      if (judged.length === total) {
        const allOk = ac === total || (counts.RUN || 0) + ac === total;
        chips.push(`<div class="chip ${allOk ? "verdict-ok" : "verdict-bad"}">${allOk ? "全部通过" : "存在未通过"}</div>`);
      }
    }
    if (d.judging.running) {
      const current = d.cases.find((c) => c.id === d.judging.current);
      chips.unshift(`<div class="chip"><span class="spinner" style="width:12px;height:12px;border-width:1.5px"></span>评测中 <b>${d.judging.done}</b><span class="muted">/ ${d.judging.total}</span>${current ? ` · ${esc(current.name)}` : ""}</div>`);
    }
    host.innerHTML = chips.join("");
  }

  function renderJudging(d) {
    const judging = d.judging;
    const running = !!judging.running;
    $("#btnJudgeAll").disabled = running || !d.cases.length;
    $("#btnJudgeSelected").disabled = running || !d.cases.length || App.selected.size === 0;
    $("#btnStop").hidden = !running;
    $("#progressLine").classList.toggle("on", running);
    const percent = judging.total ? Math.min(100, (judging.done / judging.total) * 100) : 0;
    $("#progressLine i").style.width = `${percent}%`;

    if (App.wasRunning && !running) {
      const summary = judging.summary || {};
      const counts = summary.counts || {};
      const total = judging.total || 0;
      const ac = counts.AC || 0;
      const bad = total - ac;
      if (App.toastDone && total) {
        if (bad === 0) toast(`全部 ${total} 个测试点通过`, "ok", "评测完成");
        else toast(`通过 ${ac} / ${total}，最高用时 ${fmtTime(summary.maxTimeMs || 0).value}ms`, bad > 0 ? "warn" : "ok", "评测完成");
      }
      if (App.drawerId) reloadDetail();
    }
    App.wasRunning = running;
  }

  function renderHero(d) {
    const hero = $("#hero");
    const hasCases = d.cases.length > 0;
    hero.classList.toggle("hidden", hasCases);
    // 空状态时连表头一起隐藏，避免表头从半透明背景里透出来
    $("#tableScroll").hidden = !hasCases;
    if (hasCases) return;

    if (d.folder && d.folder.exists) {
      $("#heroTitle").textContent = "没有找到测试数据";
      $("#heroDesc").innerHTML = `已识别题目 <b>${esc(d.problem.name)}</b>，但没有发现配对的 <code>.in</code> / <code>.out</code> 文件。可以导入数据，或重新扫描文件夹。`;
    } else {
      $("#heroTitle").textContent = "打开一个题目文件夹";
      $("#heroDesc").innerHTML = '自动识别题目名称、测试数据（<code>.in</code> / <code>.out</code>）与选手程序（<code>.cpp</code>），一键批量评测。';
    }
  }

  function renderRecent(d) {
    const host = $("#recentList");
    const items = (d.recent || []).filter((item) => item.exists);
    host.innerHTML = items
      .slice(0, 6)
      .map((item) => {
        const parts = item.path.split(/[\\/]/).filter(Boolean);
        const name = parts.length ? parts[parts.length - 1] : item.path;
        const parent = parts.slice(-3, -1).join("\\");
        return `<button class="recent-item" data-recent="${esc(item.path)}" title="${esc(item.path)}">
          <span class="recent-name">${esc(name)}</span>
          <span class="recent-dir">${esc(parent)}</span>
        </button>`;
      })
      .join("");
  }

  function updateSelectionUI() {
    const cases = visibleCases();
    const selectedVisible = cases.filter((c) => App.selected.has(c.id)).length;
    const total = (App.data && App.data.cases.length) || 0;
    $("#selectionText").textContent = `${App.selected.size} / ${total}`;
    const all = $("#checkAll");
    all.checked = cases.length > 0 && selectedVisible === cases.length;
    all.indeterminate = selectedVisible > 0 && selectedVisible < cases.length;
    $("#btnJudgeSelected").disabled = !total || App.selected.size === 0 || (App.data && App.data.judging.running);
  }

  /* -------------------------------------------------------------- 操作 */

  async function openFolderDialog() {
    try {
      showBusy("等待选择文件夹…");
      const result = await api("dialog", { kind: "folder" });
      if (!result.paths || !result.paths.length) return;
      await openPath(result.paths[0]);
    } catch (err) {
      toast(err.message, "bad", "打开失败");
    } finally {
      hideBusy();
    }
  }

  async function openPath(path) {
    if (!path) return;
    try {
      showBusy("正在扫描题目文件夹…");
      const data = await api("open", { path });
      applyState(data);
      toast(`已打开 ${data.problem.name || path}，识别到 ${data.cases.length} 个测试点`, "ok");
    } catch (err) {
      toast(err.message, "bad", "打开失败");
    } finally {
      hideBusy();
    }
  }

  async function rescanFolder() {
    if (!App.data || !App.data.folder) return;
    try {
      showBusy("正在重新扫描文件夹…");
      const data = await api("rescan", {});
      applyState(data);
      toast(`已重新扫描，识别到 ${data.cases.length} 个测试点`, "ok");
    } catch (err) {
      toast(err.message, "bad", "扫描失败");
    } finally {
      hideBusy();
    }
  }

  async function importSource(path) {
    try {
      showBusy("等待选择选手程序…");
      const body = path ? { path } : {};
      const data = await api("import/source", body);
      if (data.cancelled) { hideBusy(); return; }
      applyState(data);
      toast(`已选择选手程序 ${data.source ? data.source.name : ""}`, "ok");
    } catch (err) {
      toast(err.message, "bad", "导入失败");
    } finally {
      hideBusy();
    }
  }

  async function importCases() {
    try {
      showBusy("等待选择测试数据…");
      const result = await api("dialog", { kind: "cases" });
      if (!result.paths || !result.paths.length) return;
      hideBusy();
      showBusy("正在导入测试数据…");
      const data = await api("import/cases", { paths: result.paths });
      applyState(data);
      toast(`已导入测试数据，共 ${data.cases.length} 个测试点`, "ok");
    } catch (err) {
      toast(err.message, "bad", "导入失败");
    } finally {
      hideBusy();
    }
  }

  async function pickChecker() {
    try {
      showBusy("等待选择 checker…");
      const result = await api("dialog", { kind: "checker" });
      if (!result.paths || !result.paths.length) return;
      hideBusy();
      showBusy("正在准备 checker…");
      const data = await api("checker", { path: result.paths[0] });
      applyState(data);
      toast(`已启用 Special Judge：${data.checker.name}`, "ok");
    } catch (err) {
      toast(err.message, "bad", "选择 checker 失败");
    } finally {
      hideBusy();
    }
  }

  async function compileChecker() {
    try {
      showBusy("正在编译 checker…");
      const data = await api("checker/compile", {});
      applyState(data);
      App.checkerDismissed = null;
      renderChecker(data);
      if (data.checker.compile.status === "ok") {
        toast("Checker 编译成功", "ok");
      } else {
        toast("Checker 编译失败，请查看日志", "bad");
      }
    } catch (err) {
      toast(err.message, "bad", "Checker 编译失败");
    } finally {
      hideBusy();
    }
  }

  async function toggleChecker(enabled) {
    try {
      const data = await api("checker", { enabled });
      applyState(data);
      toast(enabled ? "已启用 Special Judge" : "已关闭 Special Judge", enabled ? "ok" : "info");
    } catch (err) {
      toast(err.message, "bad");
      try { applyState(await api("state")); } catch (ignored) { /* ignore */ }
    }
  }

  async function openExample(name) {
    try {
      showBusy(name === "SPJP1001" ? "正在打开 Special Judge 示例…" : "正在打开示例题目…");
      const data = await api("example", { name });
      applyState(data);
      const spj = data.checker && data.checker.enabled;
      toast(`示例已加载${spj ? "（已自动启用 Special Judge）" : ""}，点击「全部评测」试试`, "ok");
    } catch (err) {
      toast(err.message, "bad");
    } finally {
      hideBusy();
    }
  }

  async function compileNow() {
    try {
      showBusy("正在编译…");
      const data = await api("compile", {});
      applyState(data);
      App.compileDismissed = null;
      renderCompile(data);
      if (data.compile.status === "ok") {
        toast(`编译成功${data.compile.ms ? ` · ${data.compile.ms} ms` : ""}`, "ok");
      } else {
        toast("编译失败，请查看编译日志", "bad");
      }
    } catch (err) {
      toast(err.message, "bad", "编译失败");
    } finally {
      hideBusy();
    }
  }

  async function startJudge(all) {
    if (!App.data || !App.data.cases.length) return;
    if (App.data.judging && App.data.judging.running) return;
    const ids = all ? null : Array.from(App.selected);
    if (!all && !ids.length) {
      toast("请先勾选要评测的测试用例", "warn");
      return;
    }
    try {
      const data = await api("judge", all ? { all: true } : { ids });
      applyState(data);
      schedulePoll(180);
    } catch (err) {
      toast(err.message, "bad", "无法开始评测");
    }
  }

  async function stopJudge() {
    try {
      const data = await api("stop", {});
      applyState(data);
      toast("已请求停止评测", "warn");
    } catch (err) {
      toast(err.message, "bad");
    }
  }

  async function saveSettings(patch) {
    try {
      const data = await api("settings", patch);
      applyState(data);
    } catch (err) {
      toast(err.message, "bad", "设置未保存");
    }
  }

  function applyThemeToControls(theme) {
    const value = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = value;
    $("#btnTheme .theme-glyph").textContent = value === "light" ? "☀" : "☾";
    $$("#themeSegment button").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.themeValue === value);
    });
  }

  function setTheme(theme) {
    applyThemeToControls(theme);
    saveSettings({ theme });
  }

  /* -------------------------------------------------------------- 抽屉 */

  function openDrawer(caseId, tab = "diff") {
    const caseItem = (App.data.cases || []).find((c) => c.id === caseId);
    if (!caseItem) return;
    const hasChecker = !!(caseItem.result && caseItem.result.checker);
    App.drawerId = caseId;
    const tabs = ["diff", "checker", "out", "ans", "in", "err"];
    App.drawerTab = tabs.includes(tab) && (tab !== "checker" || hasChecker) ? tab : "diff";
    App.detail = null;
    $("#tabChecker").hidden = !hasChecker;
    $("#drawerTitle").textContent = caseItem.name;
    $("#drawerSub").textContent = `第 ${App.data.cases.indexOf(caseItem) + 1} 个测试点 · 输入 ${fmtBytes(caseItem.size)}`;
    $("#drawer").classList.add("open");
    $("#drawer").setAttribute("aria-hidden", "false");
    $("#drawerBackdrop").classList.add("open");
    $$("#drawerTabs button").forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === App.drawerTab));
    $("#drawerBody").innerHTML = '<div class="code-empty">加载中…</div>';
    $("#drawerFootInfo").textContent = caseItem.in;
    reloadDetail();
  }

  function closeDrawer() {
    App.drawerId = null;
    App.detail = null;
    $("#drawer").classList.remove("open");
    $("#drawer").setAttribute("aria-hidden", "true");
    $("#drawerBackdrop").classList.remove("open");
  }

  async function reloadDetail() {
    if (!App.drawerId) return;
    try {
      const detail = await api(`detail?id=${encodeURIComponent(App.drawerId)}&parts=in,ans,out,err,checkerErr`);
      if (App.drawerId !== detail.id) return;
      App.detail = detail;
      const hasChecker = !!(detail.result && detail.result.checker);
      $("#tabChecker").hidden = !hasChecker;
      if (!hasChecker && App.drawerTab === "checker") {
        App.drawerTab = "diff";
        $$("#drawerTabs button").forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === "diff"));
      }
      renderDrawerBody();
    } catch (err) {
      $("#drawerBody").innerHTML = `<div class="code-empty">${esc(err.message)}</div>`;
    }
  }

  function renderDrawerBody() {
    const detail = App.detail;
    if (!detail) return;
    const body = $("#drawerBody");
    const tab = App.drawerTab;
    if (tab === "diff") body.innerHTML = renderDiffTab(detail);
    else if (tab === "checker") body.innerHTML = renderCheckerTab(detail);
    else if (tab === "out") body.innerHTML = codeBlock("程序输出", detail.out, "out");
    else if (tab === "ans") body.innerHTML = codeBlock("标准答案", detail.ans, "ans");
    else if (tab === "in") body.innerHTML = codeBlock("输入数据", detail.in, "in");
    else if (tab === "err") body.innerHTML = codeBlock("错误输出 (stderr)", detail.err, "err");
    if (tab === "diff") requestAnimationFrame(scrollDiffToFirstChange);
  }

  function bigPill(status) {
    const meta = STATUS[status] || STATUS.NONE;
    return `<span class="big-pill ${meta.cls}">${meta.label}</span>`;
  }

  function renderDiffTab(detail) {
    const result = detail.result || {};
    const status = result.status || "NONE";
    const meta = STATUS[status] || STATUS.NONE;
    const checker = result.checker;
    const parts = [];

    parts.push(`<div class="result-head">
      ${bigPill(status)}
      <div class="result-text">
        <b>${esc(result.message || (status === "AC" ? "输出与标准答案一致" : "尚未评测"))}</b>
        <span>${detail.case.hasAnswer ? "已配置标准答案" : "无标准答案"} · ${checker ? "Special Judge 判定" : `比较方式：${esc(compareLabel())}`}</span>
      </div>
    </div>`);

    if (checker) {
      const tone = checker.status === "AC" ? "ok" : checker.status === "CJF" ? "error" : "warn";
      parts.push(`<div class="spj-panel">
        <div class="spj-panel-head">
          <span class="spj-panel-title">Special Judge</span>
          <span class="badge ${tone}">${esc(checker.verdict || checker.status)}</span>
        </div>
        <div class="spj-panel-msg">${esc(checker.message || "")}</div>
        <div class="spj-panel-meta">checker 用时 ${checker.timeMs} ms · 退出码 ${checker.exitCode}${checker.score !== null && checker.score !== undefined ? ` · 得分 ${checker.score}` : ""}</div>
      </div>`);
    }

    if (result.timeMs !== undefined && result.finishedAt) {
      const time = fmtTime(result.timeMs);
      const mem = fmtMem(result.memoryKB);
      parts.push(`<div class="stat-grid">
        <div class="stat"><span>用时</span><b>${time.value}<small>${time.unit}</small></b></div>
        <div class="stat"><span>内存</span><b>${mem.value}<small>${mem.unit}</small></b></div>
        <div class="stat"><span>退出码</span><b>${result.exitCode === undefined ? "—" : result.exitCode}</b></div>
      </div>`);
    }

    if (status === "AC") {
      parts.push(checker
        ? '<div class="hint-card"><b>Special Judge 判定通过 ✓</b><br/>该测试点在时间与内存限制内通过 checker 校验。</div>'
        : '<div class="hint-card"><b>答案完全一致 ✓</b><br/>该测试点在时间与内存限制内通过。可以继续评测其他测试点，或调整限制后重新评测。</div>');
    } else if (status === "WA") {
      parts.push('<div class="diff-grid">');
      parts.push(diffPane("标准答案", detail.ans, "expected"));
      parts.push(diffPane("程序输出", detail.out, "actual"));
      parts.push("</div>");
      if (checker) {
        parts.push(`<div class="hint-card" style="margin-top:12px">
          <b>Special Judge：</b>${esc(checker.message || "答案错误")}。
          上面的并排对比仅用于辅助定位，最终判定以 checker 为准。
        </div>`);
      } else {
        parts.push(`<div class="hint-card" style="margin-top:12px">
          <b>差异定位：</b>${esc((result.compare && result.compare.reason) || "输出与标准答案不同")}
          ${result.compare && result.compare.column ? `，第 ${result.compare.column} 个字符开始不同` : ""}。
          <ul>
            <li>检查输出格式：多余空格、换行、大小写。</li>
            <li>若答案只差行末空格，可在左侧把比较方式改为「忽略行末空格」。</li>
          </ul>
        </div>`);
      }
    } else if (STATUS_HINT[status]) {
      parts.push(`<div class="hint-card"><b>${meta.label}</b><br/>${esc(STATUS_HINT[status])}</div>`);
    } else {
      parts.push(`<div class="hint-card">该测试点还没有评测结果。点击右上角「全部评测」或「评测选中」开始。</div>`);
    }
    return parts.join("");
  }

  function renderCheckerTab(detail) {
    const result = detail.result || {};
    const checker = result.checker;
    if (!checker) {
      return '<div class="code-empty">该测试点没有使用 Special Judge</div>';
    }
    const meta = STATUS[checker.status] || STATUS.NONE;
    const parts = [];
    parts.push(`<div class="result-head">
      <span class="big-pill ${meta.cls}">${meta.label}</span>
      <div class="result-text">
        <b>${esc(checker.message || "")}</b>
        <span>checker 判定：${esc(checker.verdict || checker.status)}</span>
      </div>
    </div>`);

    const hasScore = checker.score !== null && checker.score !== undefined;
    parts.push(`<div class="stat-grid">
      <div class="stat"><span>checker 用时</span><b>${checker.timeMs}<small>ms</small></b></div>
      <div class="stat"><span>退出码</span><b>${checker.exitCode}</b></div>
      <div class="stat"><span>${hasScore ? "得分" : "判定"}</span><b>${hasScore ? checker.score : esc(checker.verdict || checker.status)}</b></div>
    </div>`);

    const stderr = detail.checkerErr;
    if (stderr && stderr.exists && (stderr.text || "").trim()) {
      parts.push(codeBlock("Checker 输出 (stderr)", stderr, "checkerErr"));
    } else {
      parts.push('<div class="hint-card">checker 没有向 stderr 写入额外信息。testlib 的判定信息通常会写在这里。</div>');
    }
    return parts.join("");
  }

  function compareLabel() {
    const mode = (App.data.settings && App.data.settings.compareMode) || "trim";
    if (mode === "strict") return "严格逐字节";
    if (mode === "tokens") return "忽略所有空白";
    return "忽略行末空格";
  }

  function splitLines(text, trim) {
    let value = String(text == null ? "" : text).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const lines = value.split("\n");
    while (lines.length && lines[lines.length - 1] === "") lines.pop();
    return trim ? lines.map((line) => line.replace(/[ \t]+$/, "")) : lines;
  }

  function diffPanes() {
    if (!App.detail) return { expected: new Set(), actual: new Set(), expectedLines: [], actualLines: [] };
    const trim = ((App.data.settings && App.data.settings.compareMode) || "trim") !== "strict";
    const expectedText = (App.detail.ans && App.detail.ans.text) || "";
    const actualText = (App.detail.out && App.detail.out.text) || "";
    const expectedLines = splitLines(expectedText, trim).slice(0, 500);
    const actualLines = splitLines(actualText, trim).slice(0, 500);
    const { changedA, changedB } = lcsDiff(expectedLines, actualLines);
    return { expected: changedA, actual: changedB, expectedLines, actualLines };
  }

  function lcsDiff(a, b) {
    const n = a.length;
    const m = b.length;
    const changedA = new Set();
    const changedB = new Set();
    if (n * m > 400000) {
      const limit = Math.max(n, m);
      for (let i = 0; i < limit; i += 1) {
        if (a[i] !== b[i]) { if (i < n) changedA.add(i); if (i < m) changedB.add(i); }
      }
      return { changedA, changedB };
    }
    const width = m + 1;
    const dp = new Int32Array((n + 1) * width);
    for (let i = n - 1; i >= 0; i -= 1) {
      for (let j = m - 1; j >= 0; j -= 1) {
        dp[i * width + j] = a[i] === b[j]
          ? dp[(i + 1) * width + j + 1] + 1
          : Math.max(dp[(i + 1) * width + j], dp[i * width + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) { i += 1; j += 1; }
      else if (dp[(i + 1) * width + j] >= dp[i * width + j + 1]) { changedA.add(i); i += 1; }
      else { changedB.add(j); j += 1; }
    }
    while (i < n) { changedA.add(i); i += 1; }
    while (j < m) { changedB.add(j); j += 1; }
    return { changedA, changedB };
  }

  function diffPane(title, bundle, side) {
    const panes = diffPanes();
    const lines = side === "expected" ? panes.expectedLines : panes.actualLines;
    const changed = side === "expected" ? panes.expected : panes.actual;
    if (!bundle || !bundle.exists) {
      return `<div class="diff-pane"><div class="diff-pane-head"><span>${esc(title)}</span></div><div class="code-empty">文件不存在</div></div>`;
    }
    const rows = lines.length
      ? lines.map((line, index) => {
        const cls = changed.has(index) ? (side === "expected" ? "diff-expected" : "diff-actual") : "";
        return `<div class="dline ${cls}" data-line="${index}"><span class="ln">${index + 1}</span><span class="lc">${esc(line) || " "}</span></div>`;
      }).join("")
      : '<div class="code-empty">（空）</div>';
    return `<div class="diff-pane">
      <div class="diff-pane-head"><span>${esc(title)}</span><span>${fmtBytes(bundle.size)}</span></div>
      <div class="diff-lines" data-side="${side}">${rows}</div>
    </div>`;
  }

  function scrollDiffToFirstChange() {
    const first = $("#drawerBody .dline.diff-expected, #drawerBody .dline.diff-actual");
    if (!first) return;
    const index = Number(first.dataset.line || 0);
    $$("#drawerBody .diff-lines").forEach((pane) => {
      pane.scrollTop = Math.max(0, index * 20 - 80);
    });
  }

  function codeBlock(title, bundle, kind) {
    if (!bundle || !bundle.exists) {
      return `<div class="code-block"><div class="code-block-head"><span>${esc(title)}</span></div><div class="code-empty">没有内容</div></div>`;
    }
    const text = bundle.text || "";
    return `<div class="code-block">
      <div class="code-block-head">
        <span>${esc(title)} · ${fmtBytes(bundle.size)}${bundle.truncated ? " · 已截断" : ""}</span>
        <button class="link" data-copy="${kind}">复制</button>
      </div>
      ${text.trim().length ? `<pre data-code="${kind}">${esc(text)}</pre>` : '<div class="code-empty">（空）</div>'}
    </div>`;
  }

  /* -------------------------------------------------------------- 轮询 */

  function schedulePoll(delay) {
    clearTimeout(App.timer);
    App.timer = setTimeout(poll, delay);
  }

  async function poll() {
    try {
      const data = await api("state");
      applyState(data);
      schedulePoll(data.judging.running ? 300 : 1500);
    } catch (err) {
      schedulePoll(2500);
    }
  }

  /* -------------------------------------------------------------- 初始化 */

  function bindEvents() {
    $("#btnOpenFolder").addEventListener("click", openFolderDialog);
    $("#btnRescan").addEventListener("click", rescanFolder);
    $("#heroOpen").addEventListener("click", openFolderDialog);
    $("#heroExample").addEventListener("click", () => openExample("P1001"));
    $("#heroSpj").addEventListener("click", () => openExample("SPJP1001"));
    $("#heroPathForm").addEventListener("submit", (event) => {
      event.preventDefault();
      const path = $("#heroPath").value.trim().replace(/^"|"$/g, "");
      if (path) openPath(path);
    });
    $("#recentList").addEventListener("click", (event) => {
      const button = event.target.closest("[data-recent]");
      if (button) openPath(button.dataset.recent);
    });
    $("#btnImportSource").addEventListener("click", () => importSource(null));
    $("#btnChangeSource").addEventListener("click", () => importSource(null));
    $("#btnImportCases").addEventListener("click", importCases);
    $("#btnCompileNow").addEventListener("click", compileNow);
    $("#btnPickChecker").addEventListener("click", pickChecker);
    $("#btnCompileChecker").addEventListener("click", compileChecker);
    $("#spjEnabled").addEventListener("change", (event) => toggleChecker(event.target.checked));
    $("#checkerLogClose").addEventListener("click", () => {
      if (App.data && App.data.checker) App.checkerDismissed = App.data.checker.compile.at;
      $("#checkerLog").hidden = true;
    });
    $("#btnRevealSource").addEventListener("click", () => {
      const src = App.data.source;
      const target = src ? src.path : App.data.binary && App.data.binary.path;
      if (target) reveal(target);
    });
    $("#btnSettings").addEventListener("click", openSettings);
    $("#btnTheme").addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      setTheme(next);
    });
    $("#btnJudgeAll").addEventListener("click", () => startJudge(true));
    $("#btnJudgeSelected").addEventListener("click", () => startJudge(false));
    $("#btnStop").addEventListener("click", stopJudge);

    $("#search").addEventListener("input", (event) => {
      App.filter = event.target.value.trim();
      renderCases(App.data);
      updateSelectionUI();
    });

    $("#checkAll").addEventListener("change", (event) => {
      const cases = visibleCases();
      if (event.target.checked) cases.forEach((c) => App.selected.add(c.id));
      else cases.forEach((c) => App.selected.delete(c.id));
      renderCases(App.data);
      updateSelectionUI();
    });

    $("#caseBody").addEventListener("click", (event) => {
      const view = event.target.closest("[data-view]");
      if (view) { openDrawer(view.dataset.view); return; }
      if (event.target.classList.contains("row-check")) return;
      const row = event.target.closest("tr[data-id]");
      if (row) openDrawer(row.dataset.id);
    });

    $("#caseBody").addEventListener("change", (event) => {
      if (!event.target.classList.contains("row-check")) return;
      const row = event.target.closest("tr[data-id]");
      if (!row) return;
      const id = row.dataset.id;
      if (event.target.checked) App.selected.add(id); else App.selected.delete(id);
      row.classList.toggle("selected", event.target.checked);
      updateSelectionUI();
    });

    $("#drawerClose").addEventListener("click", closeDrawer);
    $("#drawerBackdrop").addEventListener("click", closeDrawer);
    $("#drawerTabs").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-tab]");
      if (!button) return;
      App.drawerTab = button.dataset.tab;
      $$("#drawerTabs button").forEach((b) => b.classList.toggle("active", b === button));
      renderDrawerBody();
    });
    $("#drawerBody").addEventListener("click", async (event) => {
      const copy = event.target.closest("[data-copy]");
      if (!copy || !App.detail) return;
      const key = copy.dataset.copy;
      const code = $(`#drawerBody [data-code="${key}"]`);
      if (!code) return;
      try {
        await navigator.clipboard.writeText(code.textContent);
        copy.textContent = "已复制";
        setTimeout(() => { copy.textContent = "复制"; }, 1200);
      } catch (err) {
        toast("复制失败", "bad");
      }
    });
    $("#drawerReveal").addEventListener("click", () => {
      if (App.detail && App.detail.case && App.detail.case.in) reveal(App.detail.case.in);
    });

    $("#settingsClose").addEventListener("click", closeSettings);
    $("#settingsBackdrop").addEventListener("click", closeSettings);
    $("#themeSegment").addEventListener("click", (event) => {
      const button = event.target.closest("[data-theme-value]");
      if (button) setTheme(button.dataset.themeValue);
    });
    $("#autoCompile").addEventListener("change", (event) => saveSettings({ autoCompile: event.target.checked }));
    $("#toastDone").addEventListener("change", (event) => {
      App.toastDone = event.target.checked;
      localStorage.setItem("oi-toast-done", App.toastDone ? "1" : "0");
    });
    $("#btnOpenData").addEventListener("click", () => {
      if (App.data && App.data.app && App.data.app.dataDir) reveal(App.data.app.dataDir);
    });
    $("#btnQuit").addEventListener("click", async () => {
      if (!confirm("确定退出 OI 对拍器吗？")) return;
      try { await api("quit", {}); } catch (err) { /* ignore */ }
      document.body.innerHTML = '<div style="display:grid;place-items:center;height:100vh;font-family:sans-serif;color:#9fb0cc;background:#070b16;font-size:14px">评测器已退出，可以关闭此窗口。</div>';
    });

    bindLimitInputs();
    $$(".chip-btn[data-preset]").forEach((button) => {
      button.addEventListener("click", () => {
        const preset = PRESETS[button.dataset.preset];
        if (!preset) return;
        $$(".chip-btn[data-preset]").forEach((b) => b.classList.toggle("active", b === button));
        saveSettings(preset);
      });
    });

    $("#compileLogClose").addEventListener("click", () => {
      App.compileDismissed = App.data.compile ? App.data.compile.at : null;
      $("#compileLog").hidden = true;
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeDrawer();
        closeSettings();
      }
      if (event.key === "F5") {
        event.preventDefault();
        if (!(App.data && App.data.judging.running)) startJudge(true);
      }
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        if (!(App.data && App.data.judging.running)) startJudge(false);
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "o") {
        event.preventDefault();
        openFolderDialog();
      }
    });
  }

  function bindLimitInputs() {
    const saveNumber = (id, key, min) => {
      $(id).addEventListener("change", (event) => {
        let value = parseInt(event.target.value, 10);
        if (Number.isNaN(value) || value < min) value = min;
        event.target.value = value;
        saveSettings({ [key]: value });
      });
    };
    saveNumber("#limTime", "timeMs", 10);
    saveNumber("#limMem", "memoryMB", 1);
    saveNumber("#limSpace", "spaceMB", 1);
    saveNumber("#stackMB", "stackMB", 1);

    $("#compareMode").addEventListener("change", (event) => saveSettings({ compareMode: event.target.value }));
    $("#stopOnFail").addEventListener("change", (event) => saveSettings({ stopOnFail: event.target.checked }));
    $("#compiler").addEventListener("change", (event) => saveSettings({ compiler: event.target.value.trim() || "g++" }));
    $("#flags").addEventListener("change", (event) => saveSettings({ flags: event.target.value.trim() }));
  }

  async function reveal(path) {
    try {
      await api("reveal", { path });
    } catch (err) {
      toast(err.message, "bad");
    }
  }

  function openSettings() {
    if (!App.data) return;
    const settings = App.data.settings;
    $("#autoCompile").checked = !!settings.autoCompile;
    $("#toastDone").checked = App.toastDone;
    $("#dataDirPath").textContent = (App.data.app && App.data.app.dataDir) || "—";
    $("#dataDirPath").title = (App.data.app && App.data.app.dataDir) || "";
    $("#settingsModal").classList.add("open");
    $("#settingsBackdrop").classList.add("open");
  }

  function closeSettings() {
    $("#settingsModal").classList.remove("open");
    $("#settingsBackdrop").classList.remove("open");
  }

  function handleDeepLink() {
    if (!App.data) return;
    const hash = (location.hash || "").replace(/^#/, "");
    const query = new URLSearchParams(location.search);
    const params = new URLSearchParams(hash);
    const caseId = params.get("case") || query.get("case");
    const tab = params.get("tab") || query.get("tab");
    if (caseId && App.data.cases.some((c) => c.id === caseId)) {
      openDrawer(caseId, tab || "diff");
    }
  }

  async function boot() {
    bindEvents();
    applyThemeToControls(document.documentElement.dataset.theme || "dark");
    try {
      const data = await api("state");
      applyState(data);
      App.wasRunning = !!data.judging.running;
      handleDeepLink();
    } catch (err) {
      toast(`无法连接本地服务：${err.message}`, "bad");
    }
    schedulePoll(500);
  }

  boot();
})();

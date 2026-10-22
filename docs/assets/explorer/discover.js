/* Explorer data view: the discovery tools.
 *
 *  - heat-strip: every varying signal of the session as one coloured row, similar rows adjacent,
 *    doubling as a session minimap and a brush for the plot's time range
 *  - related signals: what follows a chosen signal, at which delay
 *  - what changed: which signals reacted inside the zoomed window
 *
 * The maths is in analysis.js (unit-tested); this file only arranges it on screen.
 * Plan: plans/2026-10-22-explorer-capture-plots.md
 */
(function () {
  "use strict";

  var X = window.canairExplorer, A = window.CanairAnalysis, P = window.canairPlot;
  if (!X || !A || !P) return;
  var el = X.el, tray = X.tray;

  var BINS = 600, GUTTER = 6, TOP_N = 12, MAX_LAG_S = 30, DELTA_S = 5;

  /* ------------------------------------------------------- worker or inline */

  var worker = null, pending = {}, nextId = 1;
  function runDirect(fn, args) {
    return new Promise(function (resolve, reject) {
      setTimeout(function () { try { resolve(A[fn].apply(null, args)); } catch (e) { reject(e); } }, 0);
    });
  }
  function analyze(fn, args) {
    if (worker === null) {
      try {
        worker = new Worker(new URL("worker.js", X.SCRIPT_URL));
        worker.onmessage = function (e) {
          var job = pending[e.data.id];
          delete pending[e.data.id];
          if (job) { if (e.data.error) job.reject(new Error(e.data.error)); else job.resolve(e.data.result); }
        };
        worker.onerror = function () {
          worker = false;
          var jobs = pending; pending = {};
          Object.keys(jobs).forEach(function (id) { runDirect(jobs[id].fn, jobs[id].args).then(jobs[id].resolve, jobs[id].reject); });
        };
      } catch (e) { worker = false; }
    }
    if (!worker) return runDirect(fn, args);
    return new Promise(function (resolve, reject) {
      var id = nextId++;
      pending[id] = { fn: fn, args: args, resolve: resolve, reject: reject };
      worker.postMessage({ id: id, fn: fn, args: args });
    });
  }

  /* ------------------------------------------------------------------ grids */

  function varyingKeys(S) {
    return Object.keys(S.signals).filter(function (k) { return S.signals[k].varies; });
  }
  function grids(S) {
    if (!S.cache) S.cache = {};
    if (!S.grids) {
      S.grids = {};
      varyingKeys(S).forEach(function (k) {
        var s = S.signals[k];
        S.grids[k] = A.resample(s.t, s.v, 0, S.step, S.n, s.hold);
      });
    }
    return S.grids;
  }
  /** Binned, normalized rows in cluster order, computed once per session. */
  function heatData(S) {
    if (!S.heat) {
      var g = grids(S), rows = {};
      Object.keys(g).forEach(function (k) { rows[k] = A.normalize(A.binMeans(g[k], BINS)); });
      S.heat = { rows: rows, order: A.clusterOrder(rows) };
    }
    return S.heat;
  }

  /* -------------------------------------------------------------- heat-strip */

  function ramp(x) { return "hsl(" + Math.round(250 - 200 * x) + ",70%," + Math.round(22 + 38 * x) + "%)"; }

  function heat(V) {
    var S = V.S;
    var count = varyingKeys(S).length, constant = Object.keys(S.signals).length - count;
    V.heat.replaceChildren(el("h4", {}, "Session overview"), el("p", { class: "cx-muted" }, "Computing…"));
    if (!count) { V.heat.replaceChildren(el("h4", {}, "Session overview"), el("p", { class: "cx-muted" }, "Nothing in this session changed value.")); return; }
    setTimeout(function () {
      if (V.S !== S || !V.heat) return;
      var h = heatData(S), n = h.order.length;
      var rh = n <= 60 ? 5 : n <= 120 ? 3 : 2;
      var canvas = el("canvas", { class: "cx-heatcanvas", height: n * rh });
      var wrap = el("div", { class: "cx-heatwrap" }, canvas);
      var brush = el("div", { class: "cx-brush", hidden: true });
      var tip = el("div", { class: "cx-muted cx-heattip" }, "Hover a row to see which signal it is; click to add it to the plot; drag across time to zoom the plot.");
      wrap.appendChild(brush);
      V.heat.replaceChildren(
        el("h4", {}, "Session overview ", el("span", { class: "cx-muted" }, count + " signals that vary, similar ones side by side" +
          (constant ? " · " + constant + " constant hidden" : ""))),
        wrap, tip);
      var width = Math.max(300, wrap.clientWidth), dpr = devicePixelRatio || 1;
      canvas.width = width * dpr; canvas.style.height = n * rh + "px";
      var ctx = canvas.getContext("2d");
      ctx.scale(dpr, dpr);
      var plotW = width - GUTTER - 2;
      h.order.forEach(function (k, row) {
        var cells = h.rows[k];
        ctx.fillStyle = S.signals[k].verified ? "#59a14f" : "#f28e2b";
        ctx.fillRect(0, row * rh, GUTTER - 2, rh);
        for (var b = 0; b < BINS; b++) {
          if (cells[b] !== cells[b]) continue;
          ctx.fillStyle = ramp(cells[b]);
          ctx.fillRect(GUTTER + b * plotW / BINS, row * rh, plotW / BINS + 0.6, rh);
        }
      });
      V.brushEl = brush; V.heatGeom = { width: width, plotW: plotW };
      showBrush(V);
      var down = null;
      function timeAt(ev) {
        var x = ev.clientX - canvas.getBoundingClientRect().left;
        return Math.max(0, Math.min(1, (x - GUTTER) / plotW)) * S.dur / 1000;
      }
      function rowAt(ev) { return h.order[Math.floor((ev.clientY - canvas.getBoundingClientRect().top) / rh)]; }
      canvas.addEventListener("mousedown", function (ev) {
        down = { x: ev.clientX, t: timeAt(ev) };
        window.addEventListener("mouseup", up, { once: true });
      });
      canvas.addEventListener("mousemove", function (ev) {
        var k = rowAt(ev), s = k && S.signals[k];
        if (s) tip.textContent = s.name + " · " + s.ecu + ":" + s.pid + (s.unit ? " · " + s.unit : "") + (s.verified ? "" : " · unverified") + (tray.has(k) ? " · in plot" : " · click to plot");
        if (down && Math.abs(ev.clientX - down.x) > 4) {
          var a = Math.min(down.t, timeAt(ev)), b = Math.max(down.t, timeAt(ev));
          brush.hidden = false;
          brush.style.left = GUTTER + a / (S.dur / 1000) * plotW + "px";
          brush.style.width = (b - a) / (S.dur / 1000) * plotW + "px";
        }
      });
      function up(ev) {
        if (!down) return;
        var d = down; down = null;
        if (Math.abs(ev.clientX - d.x) > 4) P.setRange(Math.min(d.t, timeAt(ev)), Math.max(d.t, timeAt(ev)));
        else { var k = rowAt(ev); if (k) tray.toggle(k); }
        showBrush(V);
      }
      canvas.addEventListener("dblclick", function () { P.setRange(0, S.dur / 1000); });
    }, 0);
  }

  function showBrush(V) {
    if (!V.brushEl || !V.heatGeom) return;
    var dur = V.S.dur / 1000, g = V.heatGeom;
    V.brushEl.hidden = !V.range;
    if (V.range) {
      V.brushEl.style.left = GUTTER + V.range[0] / dur * g.plotW + "px";
      V.brushEl.style.width = (V.range[1] - V.range[0]) / dur * g.plotW + "px";
    }
  }

  /* ------------------------------------------------------------ side panels */

  function sparkline(S, key) {
    var rows = S.heat && S.heat.rows[key], c = el("canvas", { class: "cx-spark", width: 90, height: 22 });
    if (!rows) return c;
    var ctx = c.getContext("2d");
    ctx.strokeStyle = "#888"; ctx.lineWidth = 1; ctx.beginPath();
    var started = false;
    for (var b = 0; b < BINS; b += 5) {
      var v = rows[b];
      if (v !== v) { started = false; continue; }
      var x = b / BINS * 90, y = 20 - v * 18;
      if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true; }
    }
    ctx.stroke();
    return c;
  }
  function addButton(key) {
    return el("button", { type: "button", class: "cx-plus" + (tray.has(key) ? " cx-on" : ""), "data-id": key, title: "Add to the plot",
      onclick: function () { tray.toggle(key); } }, tray.has(key) ? "✓" : "＋");
  }
  function signalCell(S, key) {
    var s = S.signals[key];
    return el("div", {}, el("strong", {}, s.name), " ", el("span", { class: "cx-muted" }, s.ecu + ":" + s.pid + (s.unit ? " · " + s.unit : "")),
      s.verified ? null : [" ", el("span", { class: "cx-badge cx-warn" }, "unverified")]);
  }

  function lagText(S, lag) {
    var sec = Math.abs(lag) * S.step / 1000;
    return lag === 0 ? "in sync" : (lag > 0 ? "follows by " : "leads by ") + (sec >= 90 ? Math.round(sec / 60) + " min" : Math.round(sec) + " s");
  }

  function relatedPanel(V, body) {
    var S = V.S, keys = tray.ids.filter(function (k) { return S.signals[k] && S.signals[k].varies; });
    grids(S);
    var focus = V.focus && S.signals[V.focus] && S.signals[V.focus].varies ? V.focus : keys[0];
    var mode = V.relMode || "delta";
    var toggle = el("div", { class: "cx-seg" }, [["delta", "Changes (recommended)"], ["level", "Levels"]].map(function (m) {
      return el("button", { type: "button", class: "cx-segbtn" + (mode === m[0] ? " cx-active" : ""), onclick: function () { V.relMode = m[0]; panels(V); } }, m[1]);
    }));
    if (!focus) {
      body.replaceChildren(el("p", { class: "cx-muted" }, "Put a varying signal in the plot, then click its name in the table to see which other signals follow it."));
      return;
    }
    var key = [S.entry.id, focus, mode].join("|");
    body.replaceChildren(el("p", {}, "Signals that move with ", el("strong", {}, S.signals[focus].name), ": ", toggle),
      el("p", { class: "cx-muted" }, mode === "delta"
        ? "Compares how signals change over " + DELTA_S + " s, so two things that both drift upward over a session do not look related."
        : "Compares raw levels. Anything that rises or falls over the whole session will match; treat strong hits with suspicion."),
      el("p", { class: "cx-muted" }, "Searching…"));
    V.relKey = key;
    var g = grids(S), others = {};
    Object.keys(g).forEach(function (k) { if (k !== focus) others[k] = g[k]; });
    var maxLag = Math.max(1, Math.round(MAX_LAG_S * 1000 / S.step));
    var job = S.cache[key] || (S.cache[key] = analyze("rankRelated", [g[focus], others,
      { mode: mode, maxLag: maxLag, deltaSteps: Math.max(1, Math.round(DELTA_S * 1000 / S.step)) }]));
    job.then(function (res) {
      if (V.relKey !== key || !V.S || V.S !== S) return;
      heatData(S);
      var rows = res.slice(0, TOP_N);
      body.replaceChildren(body.children[0], body.children[1],
        rows.length ? el("table", { class: "cx-table" }, el("tbody", {}, rows.map(function (r) {
          return el("tr", {}, el("td", {}, addButton(r.key)), el("td", {}, signalCell(S, r.key)),
            el("td", { class: "cx-num" }, (r.r > 0 ? "+" : "") + r.r.toFixed(2)),
            el("td", { class: "cx-muted" }, lagText(S, r.lag)), el("td", { class: "cx-muted" }, "n=" + r.n),
            el("td", {}, r.mirror ? el("span", { class: "cx-badge cx-plain", title: "Matches at almost exactly 1.0: probably the same value, rescaled" }, "mirror") : null),
            el("td", {}, sparkline(S, r.key)));
        }))) : el("p", { class: "cx-muted" }, "Nothing else in this session moved enough to compare."),
        el("p", { class: "cx-muted" }, "A strong match is a lead, not an explanation: check that it makes physical sense, then look at the raw bytes."));
    }, function (err) { body.replaceChildren(el("p", { class: "cx-muted" }, "Analysis failed: " + err.message)); });
  }

  function changedPanel(V, body) {
    var S = V.S;
    grids(S);
    if (!V.range) {
      body.replaceChildren(el("p", {}, "Zoom into a moment, by dragging on the plot or on the overview above, and this lists the signals that reacted inside it."),
        el("p", { class: "cx-muted" }, "Try the instant charging starts, a relay clicks, or the pedal goes down."));
      return;
    }
    var key = [S.entry.id, V.range[0].toFixed(1), V.range[1].toFixed(1)].join("|");
    V.chgKey = key;
    var i0 = Math.max(0, Math.floor(V.range[0] * 1000 / S.step)), i1 = Math.min(S.n - 1, Math.ceil(V.range[1] * 1000 / S.step));
    body.replaceChildren(el("p", {}, "What changed between ", el("strong", {}, P.fmt(V.range[0]) + " s"), " and ", el("strong", {}, P.fmt(V.range[1]) + " s"), "?"),
      el("p", { class: "cx-muted" }, "Searching…"));
    var job = S.cache[key] || (S.cache[key] = analyze("changeScores", [grids(S), i0, i1]));
    job.then(function (res) {
      if (V.chgKey !== key || V.S !== S) return;
      var rows = res.slice(0, TOP_N);
      body.replaceChildren(body.children[0],
        rows.length ? el("table", { class: "cx-table" }, el("tbody", {}, rows.map(function (r) {
          return el("tr", {}, el("td", {}, addButton(r.key)), el("td", {}, signalCell(S, r.key)),
            el("td", {}, el("span", { class: "cx-badge " + (r.kind === "flip" ? "cx-ok" : "cx-plain") }, r.kind === "flip" ? "flag flipped" : r.kind === "level" ? "level shift" : "more active")),
            el("td", { class: "cx-muted" }, "inside " + P.fmt(r.inside) + " · outside " + P.fmt(r.outside)),
            el("td", { class: "cx-num" }, r.score.toFixed(1) + " σ"));
        }))) : el("p", { class: "cx-muted" }, "No signal stands out in this window."),
        el("p", { class: "cx-muted" }, "Scores are in units of each signal's own spread over the session. A window with few samples exaggerates them."));
    }, function (err) { body.replaceChildren(el("p", { class: "cx-muted" }, "Analysis failed: " + err.message)); });
  }

  function panels(V) {
    if (!V.S || !V.discover) return;
    var tab = V.dtab || "related";
    var body = el("div", { class: "cx-discover-body" });
    V.discover.replaceChildren(
      el("div", { class: "cx-tabs" }, [["related", "Related signals"], ["changed", "What changed here?"]].map(function (t) {
        return el("button", { type: "button", class: "cx-tab" + (tab === t[0] ? " cx-active" : ""), onclick: function () { V.dtab = t[0]; panels(V); } }, t[1]);
      })), body);
    (tab === "related" ? relatedPanel : changedPanel)(V, body);
  }

  window.canairDiscover = {
    heat: heat,
    panels: panels,
    rangeChanged: function (V) { showBrush(V); if (V.dtab === "changed") panels(V); },
  };
})();
